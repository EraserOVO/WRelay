"""验证 QQ 私聊命令/手动转发的「用户授权」准入门禁。

未获授权的用户（不在 qq_user_openids 勾选放行）：
  - 私聊 /转发 命令回复「准入提示」，不执行、不转发；
  - 普通私聊内容不被记录，也不消费事件（保留 manage.py 的准入提示）；
  - 私聊管理命令回复「准入提示」，不执行。

已授权用户保持原行为。准入判断以当前授权状态为准，绝不把待审核申请当授权。
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import command, manage
from plugins.c2c_consumed import clear_consumed, is_consumed
from plugins.config import USER_NOT_AUTHORIZED_TEXT


class FakeQQBot:
    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


class FakeQQMessage:
    def __getitem__(self, key):
        if key == "emoji":
            return []
        raise KeyError(key)


class FakeC2CEvent:
    attachments = []

    def __init__(self, text, event_id="evt-1"):
        self.id = event_id
        self.text = text
        self.author = type("Author", (), {"user_openid": "user-openid"})()

    def get_plaintext(self):
        return self.text

    def get_message(self):
        return FakeQQMessage()


async def main():
    clear_consumed()
    bot = FakeQQBot()

    # 私聊 /转发 命令：未授权用户被准入提示拦截，不执行。
    sent = []

    async def _capture_send(_bot, _ev, title, content, rows=None):
        sent.append((title, content))

    event = FakeC2CEvent("/转发 1")
    with (
        mock.patch.object(command, "QQBot", FakeQQBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "get_active_user_openids", return_value=[]),
        mock.patch.object(command, "send_titled_reply", side_effect=_capture_send),
        mock.patch.object(command, "_stop_propagation") as stop_prop,
    ):
        await command._handle_relay_cmd(bot, event, "/转发 1", "user-openid")
    assert sent and sent[0][0] == "准入提示", sent
    assert USER_NOT_AUTHORIZED_TEXT in sent[0][1], sent
    stop_prop.assert_called_once()

    # 转发内容：授权在引用后被撤销时不再投递。
    sent.clear()
    with (
        mock.patch.object(command, "is_user_authorized", return_value=False),
        mock.patch.object(command, "send_titled_reply", side_effect=_capture_send),
        mock.patch.object(command, "send_relay_message", new=mock.AsyncMock()) as send,
        mock.patch.object(command, "_stop_propagation") as stop_prop,
    ):
        await command._consume_next(
            bot,
            FakeC2CEvent("/转发 1", "evt-content"),
            {"content": "待转发文字", "attachments": []},
            ["target-group"],
            "user-openid",
        )
    assert sent and sent[0][0] == "准入提示", sent
    send.assert_not_awaited()
    stop_prop.assert_called_once()

    # 未授权普通内容：不记录、不消费，交给 manage.py 回复准入提示。
    sent.clear()
    content_event = FakeC2CEvent("待转发文字", "evt-plain")
    with (
        mock.patch.object(command, "QQBot", FakeQQBot),
        mock.patch.object(command, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(command, "is_user_authorized", return_value=False),
        mock.patch.object(command, "remember_c2c_content") as remember,
    ):
        await command.handle(bot, content_event)
    remember.assert_not_called()
    assert not is_consumed(content_event)

    # 私聊管理命令：未授权用户被准入提示拦截，不执行。
    sent.clear()
    manage_event = FakeC2CEvent("/运行状态", "evt-manage")
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "C2CMessageCreateEvent", FakeC2CEvent),
        mock.patch.object(manage, "get_active_user_openids", return_value=[]),
        mock.patch.object(manage, "send_titled_reply", side_effect=_capture_send),
    ):
        await manage.handle(bot, manage_event)
    assert sent and sent[0][0] == "准入提示", sent
    assert USER_NOT_AUTHORIZED_TEXT in sent[0][1], sent

    clear_consumed()
    print("PASS - QQ 私聊命令/手动转发的用户授权准入门禁")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
