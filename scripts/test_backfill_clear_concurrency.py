"""验证清除待补发不会覆盖查询期间已前进的游标。"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill_api


async def main():
    stored = {"C1": {"G1": "100", "G2": "100", "*": "100"}}
    updates = []

    async def fetch_latest(channel_id):
        assert channel_id == "C1"
        # 模拟 Discord 查询等待期间，实时转发已推进一个群的游标。
        stored["C1"]["G1"] = "300"
        return "200"

    async def update_last_messages(mutate):
        updates.append(mutate)
        return mutate(stored)

    with (
        mock.patch.object(
            backfill_api,
            "get_backfill_routing_snapshot",
            return_value={
                "channels": {"C1": "频道"},
                "groups_by_channel": {"C1": ["G1", "G2"]},
            },
        ),
        mock.patch.object(backfill_api, "fetch_channel_latest", side_effect=fetch_latest),
        mock.patch.object(backfill_api, "update_last_messages", side_effect=update_last_messages),
    ):
        result = await backfill_api.api_backfill_clear()

    assert result == {"ok": True, "cleared": 1}, result
    assert stored == {"C1": {"G1": "300", "G2": "200", "*": "200"}}, stored
    assert len(updates) == 1, updates
    print("PASS - 清除待补发合并最新游标且不会回退并发推进的群")


if __name__ == "__main__":
    asyncio.run(main())
