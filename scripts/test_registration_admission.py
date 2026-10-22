"""验证 QQ 群注册申请在操作人未获授权时提示先私聊 /注册 再申请群注册。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import registration


class FakeBot:
    self_id = "bot-1"

    def __init__(self):
        self.sent = []

    async def send(self, _event, message):
        self.sent.append(message)


class FakeGroupEvent:
    class Author:
        member_openid = "not-authorized-operator"

    author = Author()
    group_openid = "group-openid"

    @staticmethod
    def get_plaintext():
        return "/注册群"


def sent_text(bot):
    message = bot.sent[0]
    markdown = getattr(getattr(message, "data", None), "markdown", None)
    return getattr(markdown, "content", None) or str(message)


async def main():
    bot = FakeBot()
    with (
        mock.patch.object(registration, "get_active_user_openids", return_value=[]),
        mock.patch.object(registration, "get_qq_group_entries", return_value=[]),
        mock.patch.object(registration, "register_group") as save,
    ):
        await registration._handle_group_register(bot, FakeGroupEvent(), "/注册群")
    save.assert_not_called()
    text = sent_text(bot)
    assert "仅白名单用户可提交群注册申请" in text, text
    assert "私聊发送 /注册" in text, text
    assert "再申请群注册" in text, text

    print("PASS - 群注册未获授权操作人的引导提示")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))