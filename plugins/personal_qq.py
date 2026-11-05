"""Verified OneBot v11 gateway for NapCat and SnowLuma."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from weakref import WeakValueDictionary

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11 import Adapter, ActionFailed
from nonebot.drivers import URL, Response, WebSocketServerSetup

from plugins import personal_qq_config as config
from plugins.delivery_dedup import delivered_groups, record_delivered_groups as save_deliveries
from plugins.json_io import run_in_thread_to_completion, update_json
from plugins.paths import data_path

UNCERTAIN_FILE = data_path("personal_qq_uncertain.json")
_ready = {}
_status = {}
_connecting = set()
_send_locks = WeakValueDictionary()


class DeliveryUnknown(RuntimeError):
    """A possibly accepted send must be resolved before retrying."""


def connected_bots():
    return {config.PREFIX + uin: bot for uin, bot in _ready.items()}


def any_connected():
    return bool(_ready)


def connection_status():
    return [dict(uin=uin, **value) for uin, value in _status.items()]


def convert_message(message):
    result = []
    for segment in message:
        kind, data = segment.type, segment.data
        if kind == "text":
            result.append({"type": "text", "data": {"text": data["text"]}})
            continue
        local = {"file_image": "image", "file_audio": "record", "file_video": "video"}
        remote = {"image": "image", "audio": "record", "video": "video"}
        if kind in local:
            content = data.get("content")
            if not isinstance(content, bytes):
                raise ValueError("个人 QQ 媒体必须先读取为字节")
            file = "base64://" + base64.b64encode(content).decode("ascii")
            result.append({"type": local[kind], "data": {"file": file}})
        elif kind in remote:
            url = data.get("url", "")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                raise ValueError("个人 QQ 远程媒体地址无效")
            result.append({"type": remote[kind], "data": {"file": url}})
        else:
            raise ValueError("个人 QQ 暂不支持此消息类型: " + kind)
    return result


def uncertain_records():
    # Strict loading: corrupt uncertainty records must never permit resending.
    try:
        with open(UNCERTAIN_FILE, encoding="utf-8") as stream:
            records = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(records, dict):
        raise ValueError("个人 QQ 待确认记录损坏")
    from plugins.state_validation import validate_runtime
    validate_runtime("personal_qq_uncertain.json", records)
    return records


def _claim_part(key, record):
    def mutate(records):
        from plugins.state_validation import validate_runtime
        validate_runtime("personal_qq_uncertain.json", records)
        if key in records or any(all(existing.get(field) == record[field]
                                    for field in ("group", "channel_id", "message_id"))
                                 for existing in records.values()):
            raise DeliveryUnknown("个人 QQ 发送结果待确认，请在设置中处理")
        if len(records) >= 10000:
            raise DeliveryUnknown("个人 QQ 待确认记录已达上限")
        records[key] = record
    update_json(UNCERTAIN_FILE, mutate, default={})


def _remove_part(key):
    update_json(UNCERTAIN_FILE, lambda records: records.pop(key, None), default={})


async def resolve_uncertain(key, action):
    if action not in ("delivered", "retry"):
        raise ValueError("处理方式无效")
    lock = _send_locks.setdefault(key, asyncio.Lock())
    async with lock:
        record = (await asyncio.to_thread(uncertain_records)).get(key)
        if record is None:
            raise ValueError("记录已变化，请刷新后重试")
        if action == "delivered":
            await save_deliveries("personal-part:" + key, [record["group"]])
        await run_in_thread_to_completion(_remove_part, key)


class PersonalSender:
    def __init__(self, bot, token=None):
        self.bot = bot
        self.self_id = config.PREFIX + bot.self_id
        self.token = token

    async def send_to_group(self, group_openid, message, channel_id="", message_id=""):
        accounts = await asyncio.to_thread(config.enabled_connections)
        if self.bot.self_id not in accounts or _ready.get(self.bot.self_id) is not self:
            raise ValueError("个人 QQ 尚未完成校验或连接已停用")
        if self.token is not None and accounts[self.bot.self_id]["token"] != self.token:
            raise ValueError("个人 QQ 令牌已更新，请等待协议端重新连接")
        if not config.is_personal(group_openid) or not config.numeric_id(group_openid[len(config.PREFIX):]):
            raise ValueError("个人 QQ 目标群标识无效")
        body = await asyncio.to_thread(convert_message, message)
        # Provider/account excluded: switching gateways must not resend completed parts.
        identity = [channel_id, message_id, group_openid, body]
        key = await asyncio.to_thread(lambda: hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
        lock = _send_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if group_openid in await delivered_groups("personal-part:" + key, [group_openid]):
                return
            record = dict(group=group_openid, account=self.self_id, channel_id=channel_id,
                          message_id=message_id, created_at=time.time())
            await run_in_thread_to_completion(_claim_part, key, record)
            try:
                response = await self.bot.send_group_msg(
                    group_id=int(group_openid[len(config.PREFIX):]), message=body)
            except ActionFailed:
                await run_in_thread_to_completion(_remove_part, key)
                raise
            except BaseException as exc:
                # Includes cancellation/crash: pessimistic journal remains on disk.
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise DeliveryUnknown("个人 QQ 发送结果未知，已暂停该消息自动重发") from exc
            if not isinstance(response, dict) or not response.get("message_id"):
                raise DeliveryUnknown("个人 QQ 未返回消息编号，发送结果待确认")
            await save_deliveries("personal-part:" + key, [group_openid])
            await run_in_thread_to_completion(_remove_part, key)


class PersonalQQAdapter(Adapter):
    """Only reverse WS; authenticate each UIN and dispatch message events to guarded handlers."""

    def _setup(self):
        self.setup_websocket_server(WebSocketServerSetup(
            URL("/onebot/v11/ws"), "WRelay Personal QQ", self._handle_ws))
        self.driver.on_shutdown(self._stop)

    def _check_access_token(self, request):
        try:
            item = config.enabled_connections().get(request.headers.get("x-self-id", ""))
            supplied = request.headers.get("authorization", "")
            if item and hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + item["token"]).encode("utf-8")):
                return None
        except Exception:
            pass
        return Response(403, content="Personal QQ account or token rejected")

    @classmethod
    def json_to_event(cls, data):
        if isinstance(data, dict) and ("post_type" not in data or data.get("post_type") == "message"):
            return super().json_to_event(data)
        return None

    async def _handle_ws(self, websocket):
        uin = websocket.request.headers.get("x-self-id", "")
        if uin in _connecting or uin in get_bots():
            await websocket.close(1008, "Account already connected")
            return
        if await asyncio.to_thread(self._check_access_token, websocket.request) is not None:
            await websocket.close(1008, "Account or token rejected")
            return
        if uin in _connecting or uin in get_bots():
            await websocket.close(1008, "Account already connected")
            return
        _connecting.add(uin)
        _status[uin] = dict(connected=False, message="正在校验账号", version="")
        task = asyncio.create_task(self._verify_connection(uin, websocket))
        try:
            await super()._handle_ws(websocket)
        finally:
            task.cancel()
            _ready.pop(uin, None)
            _connecting.discard(uin)
            from plugins.personal_qq_inbound import clear_connection
            clear_connection(uin)
            previous = _status.get(uin, {})
            if previous.get("connected") or previous.get("message") == "正在校验账号":
                _status[uin] = dict(connected=False, message="未连接", version=previous.get("version", ""))
            await asyncio.gather(task, return_exceptions=True)

    async def _verify_connection(self, uin, websocket):
        version = ""
        try:
            while uin not in self.bots:
                await asyncio.sleep(0.05)
            bot = self.bots[uin]
            original_token = websocket.request.headers.get("authorization", "").removeprefix("Bearer ")
            info = await asyncio.wait_for(bot.get_login_info(), 15)
            if str(info.get("user_id")) != uin:
                raise ValueError("协议端实际 QQ 号与预期不一致")
            try:
                metadata = await asyncio.wait_for(bot.get_version_info(), 10)
                version = str(metadata.get("app_name", "")) + " " + str(metadata.get("app_version", ""))
            except Exception:
                version = "未知版本"
            while True:
                accounts = await asyncio.to_thread(config.enabled_connections)
                if uin not in accounts or accounts[uin]["token"] != original_token:
                    raise ValueError("连接已停用或删除")
                status = await asyncio.wait_for(bot.get_status(), 15)
                online = status.get("online") is True and status.get("good", True) is True
                if online:
                    if uin not in _ready:
                        _ready[uin] = PersonalSender(bot, original_token)
                        from plugins.backfill import _backfill_wrapper
                        await _backfill_wrapper(bot)
                        from plugins.scheduled_message import _start_scheduled_message_loop
                        await _start_scheduled_message_loop(bot)
                        from plugins.personal_qq_inbound import confirm_restart
                        await confirm_restart(bot)
                else:
                    _ready.pop(uin, None)
                _status[uin] = dict(connected=online, message="已连接" if online else "QQ 未在线", version=version)
                await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _ready.pop(uin, None)
            _status[uin] = dict(connected=False, message=str(exc), version=version)
            logger.warning("个人 QQ {} 连接校验失败: {}", uin, type(exc).__name__)
            await websocket.close(1008, "Account verification failed")
