"""首次配置向导接口。所有接口沿用管理面板的本机令牌保护。"""

import json
import os
import shutil
import threading
import time
import uuid
from xml.etree import ElementTree

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from panel.onboarding import clean_draft, apply_draft, feed_url, source_spec
from plugins.json_io import run_in_thread_to_completion
from plugins.startup_config import qq_ready, discord_ready


class OnboardingStateError(RuntimeError):
    """向导进度文件存在但读取失败或结构无效。"""


def install_onboarding(app, namespace):
    class Context:
        def __getattr__(self, key):
            return namespace[key]
    p = Context()
    lock = threading.RLock()

    def path():
        return os.path.join(p.DATA_DIR, "onboarding.json")

    def state():
        try:
            with open(path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OnboardingStateError("向导进度文件读取失败") from exc
        if not isinstance(data, dict):
            raise OnboardingStateError("向导进度文件顶层结构无效")
        for key in ("flow_id", "status"):
            if key in data and not isinstance(data[key], str):
                raise OnboardingStateError("向导进度文件 %s 结构无效" % key)
        if "draft" in data and not isinstance(data["draft"], dict):
            raise OnboardingStateError("向导进度文件 draft 结构无效")
        for key in ("receipt_confirmed", "restart_needed"):
            if key in data and not isinstance(data[key], bool):
                raise OnboardingStateError("向导进度文件 %s 结构无效" % key)
        if "applied_at" in data and (
            isinstance(data["applied_at"], bool)
            or not isinstance(data["applied_at"], int)
            or data["applied_at"] < 0
        ):
            raise OnboardingStateError("向导进度文件 applied_at 结构无效")
        if "previous_traces" in data and not isinstance(data["previous_traces"], list):
            raise OnboardingStateError("向导进度文件 previous_traces 结构无效")
        return data

    def public_credentials(saved=None):
        qq = p._read_qq_bots()
        dc = p._read_discord_bots()
        if saved is None:
            saved = state()
        return {
            "bots": [{"appid": str(bot["id"])} for bot in (qq[0] if qq else []) if qq_ready(bot)],
            "discord_configured": bool(dc and any(discord_ready(bot) for bot in dc[0])),
            "restart_needed": bool(saved.get("restart_needed")),
        }

    def error(message, code=400):
        return JSONResponse({"ok": False, "msg": message}, status_code=code)

    async def json_object_body(request):
        try:
            body = await request.json()
        except Exception:
            return None, error("向导请求格式不正确")
        if not isinstance(body, dict):
            return None, error("向导请求格式不正确")
        return body, None

    def save_progress(body):
        draft = clean_draft(body.get("draft"))
        with lock:
            old = state()
            if body.get("flow_id", "") != old.get("flow_id", ""):
                return error("向导已在其他窗口更新，请关闭后重新打开", 409)
            saved = {"flow_id": old.get("flow_id") or uuid.uuid4().hex,
                     "draft": draft, "status": old.get("status", "draft"),
                     "applied_at": old.get("applied_at", 0),
                     "receipt_confirmed": old.get("receipt_confirmed", False),
                     "restart_needed": old.get("restart_needed", False),
                     "previous_traces": old.get("previous_traces", [])}
            if body.get("new") is True:
                saved.update(flow_id=uuid.uuid4().hex, status="draft", applied_at=0, receipt_confirmed=False, previous_traces=[])
            p.atomic_write_json(path(), saved)
        return {"ok": True, "state": saved}

    def confirm_receipt(body):
        with lock:
            saved = state()
            if saved.get("status") != "applied" or saved.get("flow_id") != body.get("flow_id"):
                return error("请先完成当前向导配置", 409)
            saved["receipt_confirmed"] = True
            p.atomic_write_json(path(), saved)
        return {"ok": True, "state": saved}

    def authorize_user(body):
        if body.get("confirm") is not True:
            raise ValueError("请确认授予该用户私聊转发及现有管理命令权限")
        data, revision = p._load_settings_snapshot()
        entry = next((item for item in data["qq_user_openids"] if item["openid"] == body.get("openid")), None)
        if entry is None:
            raise ValueError("请先审核该用户的注册申请")
        entry["enabled"] = True
        p.save_settings(data, expected_revision=revision)
        return {"ok": True}

    def apply_configuration(body):
        draft = clean_draft(body.get("draft"))
        with lock:
            saved = state()
            if not saved.get("flow_id") or saved["flow_id"] != body.get("flow_id"):
                return error("向导进度已变更，请关闭后重新打开", 409)
            data, revision = p._load_settings_snapshot()
            if body.get("revision") != revision:
                return error("配置已被其他操作修改，请返回上一步刷新后重新确认", 409)
            creds = public_credentials()
            if draft["purpose"] == "discord" and not creds["discord_configured"]:
                raise ValueError("请先完成 Discord 账号配置")
            if draft["purpose"] == "discord" and not draft["source_id"]:
                audit = p.api_channels_audit()
                if not any(str(item.get("id")) == draft["source_value"] for item in (audit.get("audit") or {}).get("readable", [])):
                    raise ValueError("请重新扫描并选择机器人有权读取的频道")
            updated = apply_draft(data, draft, saved["flow_id"], [bot["appid"] for bot in creds["bots"]])
            if draft["purpose"] != "qq":
                _, draft["source_id"], _ = source_spec(draft, updated)
            failure = p._validate_settings(updated)
            if failure:
                raise ValueError(failure)
            previous = saved.get("previous_traces")
            if saved.get("status") != "applied":
                previous = [item.get("trace_key") for item in p.api_message_trace(200).get("entries", [])]
            p.save_settings(updated, expected_revision=revision)
            saved.update(draft=draft, status="applied", applied_at=saved.get("applied_at") or int(time.time()), previous_traces=previous or [])
            p.atomic_write_json(path(), saved)
        return {"ok": True, "state": saved}

    def save_credentials(body):
        draft = clean_draft(body.get("draft"))
        appid = draft["bot_id"]
        secret = body.get("secret", "")
        token = body.get("token", "")
        if any(not isinstance(value, str) or len(value) > 4096 or any(ch in value for ch in "\r\n'") for value in (secret, token, appid)):
            raise ValueError("凭据格式不正确，请重新复制")
        # Validate persisted wizard state before credentials can be written.
        state()
        with p._ENV_WRITE_LOCK:
            if not os.path.exists(p.ENV_PROD):
                shutil.copyfile(os.path.join(p.PROJECT_ROOT, ".env.example"), p.ENV_PROD)
            qq = p._read_qq_bots()
            dc = p._read_discord_bots()
            if qq is None or dc is None:
                raise ValueError("现有账号配置格式无法解析，请先从设置中修复；原配置已保留")
            # 新向导只新增/更新选择的 Bot，保留其他真实账号与额外字段。
            bots = [dict(bot) for bot in qq[0] if isinstance(bot, dict) and str(bot.get("id", "")) != "你的AppID"]
            payload = [{"appid": str(bot.get("id", "")), "secret": ""} for bot in bots]
            selected = next((item for item in payload if item["appid"] == appid), None)
            if selected is None:
                payload.append({"appid": appid, "secret": secret})
            else:
                selected["secret"] = secret
            prepared = p._prepare_qq_bots(payload, bots)
            if not all(qq_ready(bot) for bot in prepared):
                raise ValueError("请填写真实的 QQ AppID 与 AppSecret")
            dc_bots = [dict(bot) for bot in dc[0]]
            if draft["purpose"] == "discord":
                if token.strip():
                    dc_bots[0]["token"] = token.strip()
                if not any(discord_ready(bot) for bot in dc_bots):
                    raise ValueError("请填写 Discord Token")
            # 先在内存合并两个键，再原子替换一次文件，避免部分保存及逐步重启。
            lines = list(qq[2])
            replacements = [(qq[1], qq[3], "QQ_BOTS", prepared)]
            if draft["purpose"] == "discord":
                replacements.append((dc[1], dc[3], "DISCORD_BOTS", dc_bots))
            for start, end, key, value in sorted(replacements, reverse=True):
                lines[start:end + 1] = [key + "='" + json.dumps(value, ensure_ascii=False) + "'\n"]
            # 复用原有唯一临时文件和写入锁；替换一个已经合并完成的键。
            key_index = next(i for i, line in enumerate(lines) if line.startswith("QQ_BOTS="))
            p._write_qq_bots(prepared, lines, key_index, key_index)
            with lock:
                saved = state()
                saved["restart_needed"] = True
                p.atomic_write_json(path(), saved)
        return {"ok": True, "credentials": public_credentials()}

    def validate_feed_content(content):
        root = ElementTree.fromstring(content)
        if root.tag.rsplit("}", 1)[-1] not in ("rss", "feed", "RDF"):
            raise ValueError("地址可连接，但没有返回有效 RSS，请检查 RSSHub 服务和来源")

    @app.get("/api/onboarding")
    def get_state():
        try:
            saved = state()
            credentials = public_credentials(saved)
            settings = p.load_settings()
            existing = bool(credentials["bots"] or credentials["discord_configured"] or any(
                settings.get(key) for key in ("qq_group_openids", "qq_user_openids", "discord_channels", "x_rss_sources", "bilibili_sources")
            ))
            return {"ok": True, "state": saved, "credentials": credentials,
                    "show_welcome": not saved and not existing}
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)

    @app.post("/api/onboarding/progress")
    async def progress(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            return await run_in_thread_to_completion(save_progress, body)
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)
        except (ValueError, TypeError, AttributeError):
            return error("无法保存进度，请检查向导输入")
        except OSError:
            return error("无法保存进度，请检查安装目录的写入权限", 500)

    @app.post("/api/onboarding/credentials")
    async def credentials(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            return await run_in_thread_to_completion(save_credentials, body)
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)
        except ValueError as exc:
            return error(str(exc))
        except Exception:
            return error("账号保存失败，请检查输入和目录写入权限；密钥不会显示在日志中", 500)

    @app.post("/api/onboarding/start")
    def start():
        try:
            with lock:
                saved = state()
                result = p.restart_bot() if p.bot_status()["running"] else p.start_bot()
                if result.get("ok"):
                    with p._health_cache_lock:
                        p._health_cache.update(ts=0.0, value=None)
                    saved["restart_needed"] = False
                    p.atomic_write_json(path(), saved)
                return result
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)

    @app.post("/api/onboarding/authorize")
    async def authorize(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            return await run_in_thread_to_completion(authorize_user, body)
        except (ValueError, KeyError) as exc:
            return error(str(exc))
        except p.ConcurrentWriteError:
            return error("配置已更新，请刷新用户列表后重试", 409)

    @app.post("/api/onboarding/probe")
    async def probe(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            draft = clean_draft(body)
            if draft["purpose"] not in ("x", "bilibili"):
                raise ValueError("仅 RSS 来源需要此检测")
            settings = await run_in_thread_to_completion(p.load_settings)
            url = feed_url(draft, settings)
            # 与运行时一致，不使用环境代理；允许本机 RSSHub 服务。
            async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 2 * 1024 * 1024:
                            raise ValueError("来源返回内容过大，请检查 RSSHub 路由")
            await run_in_thread_to_completion(validate_feed_content, content)
            return {"ok": True, "msg": "来源可读取；尚未发送任何消息"}
        except httpx.HTTPStatusError as exc:
            if draft["purpose"] == "bilibili" and exc.response.status_code == 503:
                _, _, source = source_spec(draft, settings)
                kind = source.get("kind", "up")
                if kind == "up":
                    advice = "请核对数字 UID，并查看 RSSHub 日志；常见原因是上游请求被拒（日志可能显示 412）。"
                else:
                    advice = "请核对标签或关键词，并查看 RSSHub 日志；常见原因包括上游请求被拒（可能显示 412）或缺少 Chromium 支持。"
                return error("RSSHub 返回 HTTP 503，暂时无法生成这条 Bilibili 订阅。" + advice +
                             "只有日志明确提示登录或 Cookie 问题时，才需要在 RSSHub 服务端配置 Cookie。")
            return error("来源返回 HTTP %s，请检查 RSSHub 的账号、Cookie 或路由配置后重试" % exc.response.status_code)
        except (httpx.HTTPError, ElementTree.ParseError):
            return error("无法读取 RSS，请确认 RSSHub 已运行、地址可访问且来源配置有效，再重试")
        except ValueError as exc:
            return error(str(exc))

    @app.post("/api/onboarding/receipt")
    async def receipt(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            return await run_in_thread_to_completion(confirm_receipt, body)
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)

    @app.post("/api/onboarding/apply")
    async def apply(request: Request):
        body, failure = await json_object_body(request)
        if failure:
            return failure
        try:
            return await run_in_thread_to_completion(apply_configuration, body)
        except OnboardingStateError:
            return error("向导进度文件读取失败或格式无效，请修复后重试", 500)
        except (ValueError, TypeError, AttributeError) as exc:
            return error(str(exc) or "向导配置格式不正确")
        except p.ConcurrentWriteError:
            return error("配置已被其他操作修改，请返回上一步刷新后重试", 409)
        except OSError:
            return error("保存未全部完成，请重试；已保存的来源会复用，不会重复创建", 500)

    return get_state
