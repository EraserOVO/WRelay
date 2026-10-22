"""Verify a timed-out channel audit subprocess is killed and reaped."""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import backfill_api


class FakeProcess:
    def __init__(self):
        self.killed = False
        self.waited = False

    async def communicate(self):
        await asyncio.Event().wait()

    def kill(self):
        self.killed = True

    async def wait(self):
        self.waited = True
        return -9


class ExitedProcess:
    def __init__(self, stdout=b"", stderr=b""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = 1

    async def communicate(self):
        return self.stdout, self.stderr


async def main():
    process = FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    with mock.patch.object(
        backfill_api.asyncio,
        "create_subprocess_exec",
        side_effect=fake_create_subprocess_exec,
    ), mock.patch.object(backfill_api, "CHANNEL_REFRESH_TIMEOUT_SECONDS", 0.01):
        result = await backfill_api.api_channels_refresh()

    assert result == {"ok": False, "msg": "刷新超时"}
    assert process.killed is True
    assert process.waited is True

    exited = ExitedProcess(stdout="[错误] 未读取到 DISCORD_BOTS token\n".encode("utf-8"))

    async def fake_exited_subprocess_exec(*args, **kwargs):
        return exited

    with mock.patch.object(
        backfill_api.asyncio,
        "create_subprocess_exec",
        side_effect=fake_exited_subprocess_exec,
    ):
        result = await backfill_api.api_channels_refresh()

    assert result["ok"] is False
    assert "DISCORD_BOTS token" in result["msg"]
    print("PASS - 频道诊断超时回收，失败原因可从 stdout 返回面板")


if __name__ == "__main__":
    asyncio.run(main())
