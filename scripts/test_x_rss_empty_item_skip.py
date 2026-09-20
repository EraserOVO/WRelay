"""验证无可发送内容的 X 推文会推进游标，而不会在每轮轮询中重复处理。"""
import asyncio
import copy
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss


async def main():
    state = {"x_rss:source": {"G1": "100"}}

    def load():
        return copy.deepcopy(state)

    async def update(mutate):
        changed = mutate(state)
        return changed

    post = {
        "id": "101", "text": "", "is_retweet": False,
        "rt_author": "", "quote": None, "media": [],
    }
    with (
        mock.patch.object(x_rss, "load_last_messages", side_effect=load),
        mock.patch.object(x_rss, "update_last_messages", side_effect=update),
        mock.patch.object(x_rss, "get_x_groups_for_source", return_value=["G1"]),
        mock.patch.object(x_rss, "get_x_source_filter", return_value={}),
        mock.patch.object(x_rss, "send_and_record", new=mock.AsyncMock()) as send,
    ):
        result = await x_rss._send_posts({"key": "source", "name": "测试源"}, [post])

    assert result == (0, 0, 0), result
    assert state["x_rss:source"]["G1"] == "101", state
    send.assert_not_awaited()
    print("PASS - 无可发送内容的 X 推文标记跳过并推进游标")


if __name__ == "__main__":
    asyncio.run(main())
