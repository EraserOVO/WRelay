"""Verify X post delivery result counters and stats recording."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import x_rss


class DeliveryResult(dict):
    def __init__(
        self,
        values=None,
        *,
        sent_groups=(),
        deduplicated_groups=(),
        busy_groups=(),
    ):
        super().__init__(values or {})
        self.sent_groups = set(sent_groups)
        self.deduplicated_groups = set(deduplicated_groups)
        self.busy_groups = set(busy_groups)


async def main():
    cases = [
        (
            "sent",
            DeliveryResult({"G1": True}, sent_groups={"G1"}),
            {"new": 1, "sent": 1, "failed": 0},
            [mock.call(True)],
        ),
        (
            "deduplicated",
            DeliveryResult(deduplicated_groups={"G1"}),
            {"new": 0, "sent": 0, "failed": 0},
            [],
        ),
        (
            "busy",
            DeliveryResult(busy_groups={"G1"}),
            {"new": 1, "sent": 0, "failed": 0},
            [],
        ),
        (
            "failed",
            DeliveryResult({"G1": False}),
            {"new": 1, "sent": 0, "failed": 1},
            [mock.call(False)],
        ),
    ]

    for label, result, expected_counts, expected_records in cases:
        counts = {"new": 0, "sent": 0, "failed": 0}
        recorder = mock.AsyncMock()
        with mock.patch.object(x_rss, "record", new=recorder):
            await x_rss._record_post_delivery_result(
                "source",
                "123",
                ["G1"],
                result,
                counts,
            )
        assert counts == expected_counts, (label, counts)
        assert recorder.await_args_list == expected_records, label
        print(f"PASS - X 投稿 {label} 结果计数与统计记录")


if __name__ == "__main__":
    asyncio.run(main())
