"""验证群聊 @ / 未知命令统一显示转发目标列表。"""
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
    def __init__(self, plaintext="", segments=None, author_bot=False):
        self.plaintext = plaintext
        self.segments = list(segments or [])
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
    def __init__(self, plaintext="", segments=None, author_bot=False):
        segments = list(segments or [])
        if not any(segment.type == "mention_user" for segment in segments):
            segments.insert(0, FakeSegment("", "mention_user", is_bot=True))
        super().__init__(plaintext, segments, author_bot)

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

        # 仅 @、不同事件格式的 @、@ 后的未知内容、未知斜杠命令都导向目标列表。
        for event in (
            FakeGroupAtMessage("", [FakeSegment("", "mention_user", is_bot=True)]),
            FakeGroupMessage("", [FakeSegment("", "to_me")]),
            FakeGroupMessage("", []),
            FakeGroupAtMessage(
                "随便问问",
                [FakeSegment("", "mention_user", is_bot=True), FakeSegment("随便问问")],
            ),
            FakeGroupMessage(
                "", [FakeSegment("", "mention_user", is_bot=True, user_id="bot-openid")]
            ),
            FakeGroupMessage("/不存在", [FakeSegment("/不存在")]),
        ):
            if not event.get_plaintext() and not event.get_message():
                event.is_tome = lambda: True
            await manage.handle_group_at_hint(bot, event)
            relay_list.assert_awaited_with(bot, event)
        assert relay_list.await_count == 6
        relay_list.reset_mock()

        # 普通聊天、@其他成员、已知命令、机器人消息不被接管。
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
        ):
            await manage.handle_group_at_hint(bot, event)
        relay_list.assert_not_awaited()

    # 群未登记：@ 机器人 / 未知命令属于「功能请求」，提示先登记群，
    # 不展示转发目标列表；普通群消息/@其他成员不受影响。
    with (
        mock.patch.object(manage, "QQBot", FakeQQBot),
        mock.patch.object(manage, "GroupMessageCreateEvent", FakeGroupMessage),
        mock.patch.object(manage.qq_fwd, "send_relay_list", relay_list),
        mock.patch.object(manage, "_stop_group_hint_propagation"),
        mock.patch.object(manage, "is_group_registered", return_value=False),
        mock.patch.object(manage, "send_titled_reply", new=mock.AsyncMock()) as send_reply,
    ):
        relay_list.reset_mock()
        await manage.handle_group_at_hint(
            bot,
            FakeGroupAtMessage("随便问问", [FakeSegment("", "mention_user", is_bot=True), FakeSegment("随便问问")]),
        )
        relay_list.assert_not_awaited()
        send_reply.assert_awaited_once_with(
            mock.ANY, mock.ANY, "群准入提示", manage.GROUP_NOT_REGISTERED_TEXT
        )

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
