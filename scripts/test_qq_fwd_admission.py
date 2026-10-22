"""验证 QQ 群手动转发的「群登记」准入门禁。

未登记的群（按当前 Bot 对应的 qq_group_openids 判断）：
  - 群聊 relay 命令（/转发 ...）不执行，回复「群准入提示」；
  - 普通群消息不提示，也不会触发转发；
  - /注册 /注册群 申请入口不受群登记限制（未登记群正用它提交申请）。

已登记的群保持原行为（命令照常执行，引用/最近内容照常转发）。
"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import qq_fwd


class FakeBot:
    self_id = "bot-id"
    id = "bot-id"

    def __init__(self):
        self.replies = []

    async def send(self, _event, message):
        self.replies.append(message)


class FakeEvent:
    group_openid = "group-openid"
    attachments = []
    msg_elements = None
    id = "event-1"

    def __init__(self, text, member_openid="member-1"):
        self.text = text
        self.reply = None
        self.author = type("Author", (), {"member_openid": member_openid})()

    def get_plaintext(self):
        return self.text


def _reply_text(message):
    if isinstance(message, str):
        return message
    for segment in message:
        markdown = (getattr(segment, "data", {}) or {}).get("markdown")
        content = getattr(markdown, "content", None)
        if content:
            return content
    return ""


async def _run(text, registered):
    """跑一遍 handle_fwd，返回 (send_titled_reply 调用, 名单调用, 转发调用)。"""
    bot = FakeBot()
    event = FakeEvent(text)
    sent = []
    relay_list_called = mock.AsyncMock()
    relay_forward_called = mock.AsyncMock()

    async def _capture_send(_bot, _ev, title, content, rows=None):
        sent.append((title, content))

    with (
        mock.patch.object(qq_fwd, "QQBot", FakeBot),
        mock.patch.object(qq_fwd, "GroupMessageCreateEvent", FakeEvent),
        mock.patch.object(qq_fwd, "is_group_registered", return_value=registered),
        mock.patch.object(qq_fwd, "send_titled_reply", side_effect=_capture_send),
        mock.patch.object(qq_fwd, "_cmd_relay_list", relay_list_called),
        mock.patch.object(qq_fwd, "_cmd_relay_forward", relay_forward_called),
    ):
        await qq_fwd.handle_fwd(bot, event)
    return sent, relay_list_called, relay_forward_called


async def main():
    # 未登记群：/转发 列表 不执行，回复准入提示。
    sent, relay_list, relay_forward = await _run("/转发 列表", registered=False)
    assert sent and sent[0][0] == "群准入提示", sent
    assert qq_fwd.GROUP_NOT_REGISTERED_TEXT in sent[0][1], sent
    relay_list.assert_not_awaited()

    # 未登记群：/转发 1 同样被拦截。
    sent, relay_list, relay_forward = await _run("/转发 1", registered=False)
    assert sent and sent[0][0] == "群准入提示", sent
    relay_forward.assert_not_awaited()

    # 未登记群：普通群消息不提示，也不触发转发。
    sent, relay_list, relay_forward = await _run("普通群聊", registered=False)
    assert sent == [], sent
    relay_list.assert_not_awaited()
    relay_forward.assert_not_awaited()

    # 已登记群：/转发 列表 正常执行。
    sent, relay_list, relay_forward = await _run("/转发 列表", registered=True)
    relay_list.assert_awaited_once()
    assert sent == []

    # 已登记群：/转发 1 正常执行。
    sent, relay_list, relay_forward = await _run("/转发 1 2", registered=True)
    relay_forward.assert_awaited_once_with(mock.ANY, mock.ANY, [1, 2])
    assert sent == []

    # 已登记群：无法解析的参数提示用法，不进入转发。
    sent, relay_list, relay_forward = await _run("/转发 选 1", registered=True)
    assert sent and sent[0] == ("转发结果", qq_fwd.USAGE_TEXT), sent
    relay_forward.assert_not_awaited()

    # 未登记群：/注册群 申请入口不受登记限制（fwd 不接管，交给 registration）。
    sent, relay_list, relay_forward = await _run("/注册群", registered=False)
    assert sent == [], sent
    relay_list.assert_not_awaited()
    relay_forward.assert_not_awaited()

    # 未登记群：/注册 入口同样不被接管。
    sent, relay_list, relay_forward = await _run("/注册", registered=False)
    assert sent == [], sent
    relay_forward.assert_not_awaited()

    print("PASS - QQ 群手动转发的群登记准入门禁")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
