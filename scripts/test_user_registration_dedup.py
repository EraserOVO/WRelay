"""验证 QQ 用户注册与群注册同样去重：
- 已登记用户不再重复提交申请；
- 待审核申请重复提交时返回原识别码，不覆盖旧申请；
- 未登记且无待审申请时正常提交。
"""
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


class FakeUserEvent:
    class Author:
        user_openid = "user-openid"

    author = Author()

    @staticmethod
    def get_plaintext():
        return "/注册"


def sent_text(bot):
    message = bot.sent[0]
    markdown = getattr(getattr(message, "data", None), "markdown", None)
    return getattr(markdown, "content", None) or str(message)


async def run_case(*, registered, pending, expect_save):
    bot = FakeBot()
    regs = {"users": dict(pending), "groups": {}}
    with (
        mock.patch.object(registration, "is_user_registered", return_value=registered),
        mock.patch.object(registration, "load_registrations", return_value=regs),
        mock.patch.object(registration, "register_user", return_value="NEW1-CODE") as save,
    ):
        await registration._handle_user_register(bot, FakeUserEvent(), "/注册")
    if expect_save:
        save.assert_called_once()
    else:
        save.assert_not_called()
    return sent_text(bot)


async def main():
    text = await run_case(registered=True, pending={}, expect_save=False)
    assert "已经登记" in text and "无需重复申请" in text, text

    text = await run_case(
        registered=False,
        pending={"user-openid": {"application_code": "E2S9-RYXZ", "time": 1}},
        expect_save=False,
    )
    assert "已有待审核申请" in text and "E2S9-RYXZ" in text, text

    text = await run_case(registered=False, pending={}, expect_save=True)
    assert "已提交，等待管理员审核" in text and "NEW1-CODE" in text, text

    print("PASS - QQ 用户注册去重（已登记 / 待审核回显识别码 / 正常提交）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
