"""验证群聊只有「单独 @机器人」才显示转发目标列表。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import manage


class FakeSegment:
    def __init__(self, text, segment_type="text", is_bot=None, user_id=None):
        self.type = segment_type
        self.data = {"text": text}
        if is_bot is not None:
            self.data["is_bot"] = is_bot
        if user_id is not None:
            self.data["user_id"] = user_id


class FakeGroupMessage:
    def __init__(
        self,
        plaintext="",
        segments=None,
        author_bot=False,
        attachments=None,
        reply=None,
    ):
        self.plaintext = plaintext
        self.segments = list(segments or [])
        self.attachments = list(attachments or [])
        if reply is not None:
            self.reply = reply
        self.author = type("Author", (), {
            "bot": author_bot,
            "member_openid": "member-openid",
        })()
        self.group_openid = "group-openid"

    def get_plaintext(self):
        return self.plaintext

    def get_message(self):
        return self.segments

    def is_tome(self):
        return False


class FakeGroupAtMessage(FakeGroupMessage):
    def __init__(self, plaintext="", segments=None, **kwargs):
        segments = list(segments or [])
        if not any(segment.type == "mention_user" for segment in segments):
            segments.insert(0, FakeSegment("", "mention_user", is_bot=True))
        super().__init__(plaintext, segments, **kwargs)

    def is_tome(self):
        return True


class FakeQQBot:
    self_id = "bot-id"


class FakePrivateMessage:
    def __init__(self, plaintext):
        self.plaintext = plaintext
        self.author = type("Author", (), {
            "id": "member-openid",
            "user_openid": "member-openid",
        })()

    def get_plaintext(self):
        return self.plaintext


async def main():
    private_help = manage._help_markdown()
    assert "点击下方按钮执行常用命令:" in private_help
    assert "**转发**" not in private_help
    assert {
        button.action.data
        for row in manage._private_help_rows()
        for button in row.buttons
    } == {"/运行状态", "/转发 列表", "/定时消息", "/QQ账号与群", "/重启"}

    relay_list = mock.AsyncMock()
    bot = FakeQQBot()
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "GroupMessageCreateEvent", FakeGroupMessage),
        mock.patch.object(manage.qq_fwd, "send_relay_list", relay_list),
        mock.patch.object(manage, "_stop_group_hint_propagation"),
        mock.patch.object(manage, "is_group_registered", return_value=True),
    ):
        # 群聊菜单命令本身只展示转发目标列表。
        await manage.handle(bot, FakeGroupMessage("/菜单"))
        relay_list.assert_awaited_once()
        assert relay_list.await_args.args[1].get_plaintext() == "/菜单"
        relay_list.reset_mock()

        # 仅 @、不同事件格式的 @ 导向目标列表。
        for event in (
            FakeGroupAtMessage("", [FakeSegment("", "mention_user", is_bot=True)]),
            FakeGroupMessage("", [FakeSegment("", "to_me")]),
            FakeGroupMessage("", []),
            FakeGroupMessage(
                "", [FakeSegment("", "mention_user", is_bot=True, user_id="bot-openid")]
            ),
            FakeGroupMessage(
                "", [FakeSegment("", "mention_user", user_id="bot-id")]
            ),
        ):
            if not event.get_plaintext() and not event.get_message():
                event.is_tome = lambda: True
            await manage.handle_group_at_hint(bot, event)
            relay_list.assert_awaited_with(bot, event)
        assert relay_list.await_count == 5
        relay_list.reset_mock()

        # 普通聊天、@其他成员、已知命令、机器人消息、@机器人后附带内容或引用、
        # 未知斜杠命令都不接管。
        unrelated_at = FakeGroupMessage(
            "你好", [FakeSegment("", "mention_user", is_bot=False, user_id="other")]
        )
        for event in (
            FakeGroupMessage("普通群聊", [FakeSegment("普通群聊")]),
            unrelated_at,
            FakeGroupAtMessage("/菜单", [FakeSegment("/菜单")]),
            FakeGroupAtMessage("/转发 列表", [FakeSegment("/转发 列表")]),
            FakeGroupAtMessage("/注册群", [FakeSegment("/注册群")]),
            FakeGroupAtMessage("/注册", [FakeSegment("/注册")]),
            FakeGroupAtMessage("未知内容", [FakeSegment("未知内容")], author_bot=True),
            FakeGroupAtMessage(
                "随便问问",
                [FakeSegment("", "mention_user", is_bot=True), FakeSegment("随便问问")],
            ),
            FakeGroupAtMessage(
                "",
                [FakeSegment("", "mention_user", is_bot=True), FakeSegment("", "image")],
                attachments=[{"url": "https://example.com/1.png"}],
            ),
            FakeGroupAtMessage(
                "", [FakeSegment("", "mention_user", is_bot=True)], reply=object()
            ),
            FakeGroupMessage("/不存在", [FakeSegment("/不存在")]),
            FakeGroupMessage("/菜单", [FakeSegment("/菜单")]),
        ):
            await manage.handle_group_at_hint(bot, event)
        relay_list.assert_not_awaited()

    # 群未登记：单独 @ 机器人属于「功能请求」，提示先登记群，
    # 不展示转发目标列表；@机器人后带内容、普通群消息和未知命令都不提示。
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "GroupMessageCreateEvent", FakeGroupMessage),
        mock.patch.object(manage.qq_fwd, "send_relay_list", relay_list),
        mock.patch.object(manage, "_stop_group_hint_propagation"),
        mock.patch.object(manage, "is_group_registered", return_value=False),
        mock.patch.object(manage, "send_titled_reply", new=mock.AsyncMock()) as send_reply,
    ):
        relay_list.reset_mock()
        await manage.handle_group_at_hint(bot, FakeGroupAtMessage())
        relay_list.assert_not_awaited()
        send_reply.assert_awaited_once_with(
            mock.ANY, mock.ANY, "群准入提示", manage.GROUP_NOT_REGISTERED_TEXT
        )
        send_reply.reset_mock()
        for event in (
            FakeGroupAtMessage(
                "随便问问",
                [FakeSegment("", "mention_user", is_bot=True), FakeSegment("随便问问")],
            ),
            FakeGroupMessage("/不存在", [FakeSegment("/不存在")]),
        ):
            await manage.handle_group_at_hint(bot, event)
        relay_list.assert_not_awaited()
        send_reply.assert_not_awaited()

    unknown_private = FakePrivateMessage("/不存在")
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "C2CMessageCreateEvent", FakePrivateMessage),
        mock.patch.object(manage, "get_active_user_openids", return_value={"member-openid"}),
        mock.patch.object(manage, "send_titled_reply", new=mock.AsyncMock()) as send_reply,
    ):
        await manage.handle(bot, unknown_private)
    send_reply.assert_awaited_once()
    _bot, _event, title, text, rows = send_reply.await_args.args
    assert title == "WRelay菜单"
    assert text == "该命令未知，点击下方按钮展开菜单："
    # 未知命令提示保留「展开菜单」，并把原命令追加成按原文执行的按钮，
    # 误触也只会重发这条未知命令，不引入新的命令语义。
    assert rows[:len(manage._unknown_command_rows())] == manage._unknown_command_rows()
    assert [button.action.data for button in rows[-1].buttons] == ["/不存在"]

    print("PASS - 群聊菜单、@ 与未知命令导向转发列表")


if __name__ == "__main__":
    asyncio.run(main())
