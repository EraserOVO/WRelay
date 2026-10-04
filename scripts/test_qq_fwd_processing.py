"""验证 QQ 手动转发内容构造或翻译异常时会回复失败。"""

import asyncio
from types import SimpleNamespace
from unittest import mock

import nonebot

nonebot.init()

from plugins import qq_fwd


class FakeEvent:
    group_openid = "source-group"
    author = SimpleNamespace(member_openid="member-1")
    attachments = []
    id = "message-1"

    @staticmethod
    def get_plaintext():
        return "待转发消息"


class FakeBot:
    def __init__(self):
        self.replies = []

    async def send(self, event, message):
        self.replies.append(message)


async def _raise_processing_error(*_args, **_kwargs):
    raise RuntimeError("simulated processing failure")


async def _build_content(*_args, **_kwargs):
    return [], []


async def _check_failure_reply(processor, patch_name):
    qq_fwd._pending.clear()
    qq_fwd.set_pending(
        "source-group", "member-1", {"openid": "target-group", "name": "Target"}
    )
    bot = FakeBot()
    event = FakeEvent()
    with (
        mock.patch.object(qq_fwd, "get_qq_fwd_select_timeout", return_value=60),
        mock.patch.object(qq_fwd, "get_qq_fwd_routing_snapshot", return_value=(
            [{"openid": "target-group", "enabled": True}], {"target-group"}
        )),
        mock.patch.object(qq_fwd, patch_name, side_effect=processor),
        mock.patch.object(qq_fwd.logger, "exception"),
    ):
        await qq_fwd._try_forward_next(bot, event, "待转发消息")
    assert bot.replies == ["转发失败"]
    assert not qq_fwd.has_pending("source-group", "member-1")


async def main():
    await _check_failure_reply(_raise_processing_error, "build_forward_content")

    async def fail_translation(*_args, **_kwargs):
        raise RuntimeError("simulated translation failure")

    # Keep content rendering successful so the second case reaches translation.
    qq_fwd._pending.clear()
    qq_fwd.set_pending(
        "source-group", "member-1", {"openid": "target-group", "name": "Target"}
    )
    bot = FakeBot()
    event = FakeEvent()
    with (
        mock.patch.object(qq_fwd, "get_qq_fwd_select_timeout", return_value=60),
        mock.patch.object(
            qq_fwd, "get_qq_fwd_routing_snapshot", return_value=(
                [{"openid": "target-group", "enabled": True}], {"target-group"}
            ),
        ),
        mock.patch.object(qq_fwd, "build_forward_content", side_effect=_build_content),
        mock.patch.object(qq_fwd, "translate_text_parts", side_effect=fail_translation),
        mock.patch.object(qq_fwd.logger, "exception"),
    ):
        await qq_fwd._try_forward_next(bot, event, "待转发消息")
    assert bot.replies == ["转发失败"]
    assert not qq_fwd.has_pending("source-group", "member-1")
    print("PASS - QQ 转发内容处理异常回复")


if __name__ == "__main__":
    asyncio.run(main())
