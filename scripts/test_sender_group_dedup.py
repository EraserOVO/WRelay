"""验证 sender 对默认和显式传入的重复目标群只发送一次。"""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import sender


class FakeBot:
    self_id = "bot-1"


async def main():
    bot = FakeBot()
    delivered_lists = []

    async def fake_send_text(_bot, groups, _parts, **_kwargs):
        delivered_lists.append(list(groups))
        return {group: True for group in groups}

    async def fake_send_media(_bot, groups, _items, **_kwargs):
        return {group: True for group in groups}

    with (
        mock.patch.object(sender, "get_qq_bot", return_value=bot),
        mock.patch.object(sender, "get_qq_bots", return_value={}),
        mock.patch.object(sender, "get_group_bot_ids", return_value={}),
        mock.patch.object(sender, "send_text_parts", side_effect=fake_send_text),
        mock.patch.object(sender, "send_media_items", side_effect=fake_send_media),
        mock.patch.object(sender, "_record_trace", new_callable=mock.AsyncMock),
        mock.patch.object(sender, "notify_delivery_failure", new_callable=mock.AsyncMock),
    ):
        ok_map = await sender.send_relay_message(
            [object()], [], groups=["group-a", "group-a", "group-b", "group-a"]
        )

    assert delivered_lists == [["group-a", "group-b"]]
    assert ok_map == {"group-a": True, "group-b": True}
    print("PASS - sender 重复目标只投递一次")


if __name__ == "__main__":
    asyncio.run(main())
