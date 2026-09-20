"""验证实时路径在途时，启动补发不会重复投递同一 Discord 消息。"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill
from plugins import relay_inflight


async def main():
    relay_inflight._processing.clear()
    assert relay_inflight.claim_processing("C1", "101")

    message = {
        "message_id": "101",
        "author_username": "author",
        "content": "body",
        "embeds": [],
    }
    with (
        mock.patch.object(backfill, "load_last_messages", return_value={"C1": {"G1": "100"}}),
        mock.patch.object(backfill, "fetch_channel_messages_after", new=mock.AsyncMock(return_value=[message])),
        mock.patch.object(backfill, "prepare_discord_message", new=mock.AsyncMock()) as prepare,
        mock.patch.object(backfill, "send_and_record", new=mock.AsyncMock()) as send,
    ):
        await backfill._backfill_channel("C1", "频道", ["G1"], 10)

    assert not prepare.await_count
    assert not send.await_count
    relay_inflight.release_processing("C1", "101")
    assert not relay_inflight.is_processing("C1", "101")
    print("PASS - 实时路径在途时，启动补发跳过同一消息")


if __name__ == "__main__":
    asyncio.run(main())
