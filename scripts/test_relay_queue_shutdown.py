"""Verify capacity waiters cannot create relay workers during shutdown."""

import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import relay
from plugins.relay_inflight import claim_processing, is_processing


class FakeEvent:
    def __init__(self, message_id):
        self.id = message_id


class FakeBot:
    pass


async def main():
    relay._relay_capacity = asyncio.Semaphore(1)
    relay._relay_processing = asyncio.Semaphore(1)
    relay._relay_queues.clear()
    relay._relay_workers.clear()
    relay._relay_tasks.clear()
    relay._relay_producers.clear()
    relay._relay_stopping = False

    worker_started = asyncio.Event()

    async def blocked_process(_bot, _event):
        worker_started.set()
        await asyncio.Event().wait()

    first_key = ("channel-1", "message-1")
    second_key = ("channel-2", "message-2")
    assert claim_processing(*first_key)
    assert claim_processing(*second_key)

    with mock.patch.object(relay, "_process_event", side_effect=blocked_process):
        assert await relay._enqueue_relay_event(
            first_key[0], FakeBot(), FakeEvent(first_key[1]), first_key
        )
        await worker_started.wait()

        waiting_producer = asyncio.create_task(relay._enqueue_relay_event(
            second_key[0], FakeBot(), FakeEvent(second_key[1]), second_key
        ))
        await asyncio.sleep(0)
        assert not waiting_producer.done()
        assert waiting_producer in relay._relay_producers

        await relay._shutdown_relay_queue()
        assert await waiting_producer is False
        assert not is_processing(*first_key)
        assert not is_processing(*second_key)
        assert relay._relay_tasks == set()
        assert relay._relay_queues == {}
        assert relay._relay_workers == set()
        assert relay._relay_capacity._value == 1

    print("PASS - 关闭时等待中的实时事件不会重建队列 worker")


if __name__ == "__main__":
    asyncio.run(main())
