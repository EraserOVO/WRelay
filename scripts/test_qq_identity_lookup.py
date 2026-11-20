"""Targeted checks for QQ identity lookup and storage."""
import asyncio
import ast
import tempfile
from pathlib import Path
from unittest import mock

import nonebot
nonebot.init()
from plugins import identities, qq_identity_lookup as lookup

class Adapter:
    def get_api_base(self):
        from yarl import URL
        return URL("https://api.bot.qq.com")

class Bot:
    self_id = "bot-a"
    adapter = Adapter()
    def __init__(self, response):
        self.response = response
        self.calls = 0
    async def _request(self, request):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

async def main():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(identities, "IDENTITIES_FILE", Path(tmp) / "identities.json"):
        identities.record_group_identity("g", group_name="legacy")
        identities.set_admin_remark("groups", "g", "remark")
        bot = Bot({"group_name": "New group"})
        lookup.schedule_identity_lookup(bot, "groups", "g")
        lookup.schedule_identity_lookup(bot, "groups", "g")
        await asyncio.gather(*list(lookup._tasks))
        data = identities.load_identities()["groups"]["g"]
        assert bot.calls == 1 and data["group_name"] == "legacy"
        assert data["by_bot"]["bot-a"]["group_name"] == "New group"
        assert data["admin_remark"] == "remark"
        tree = ast.parse(Path("panel/管理面板.pyw").read_text(encoding="utf-8"))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_identity_items")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "panel", "exec"), namespace)
        flatten = namespace["_identity_items"]
        assert flatten("groups", "group_name", {"groups": {"g": data}})[0]["name"] == "New group"
        identities.record_group_identity("g", group_name="Other", bot_id="bot-b")
        stored = identities.load_identities()["groups"]["g"]
        assert stored["by_bot"]["bot-b"]["group_name"] == "Other"
        assert flatten("groups", "group_name", {"groups": {"g": stored}})[0]["name"] == ""
        assert flatten("groups", "group_name", {"groups": {"old": {"group_name": "Legacy"}}})[0]["name"] == "Legacy"
        user = Bot({"username": "Alice"})
        user.self_id = "bot-c"
        lookup.schedule_identity_lookup(user, "users", "u")
        await asyncio.gather(*list(lookup._tasks))
        assert identities.load_identities()["users"]["u"]["by_bot"]["bot-c"]["nickname"] == "Alice"
        failure = Bot(RuntimeError("no permission"))
        failure.self_id = "bot-d"
        lookup.schedule_identity_lookup(failure, "groups", "g")
        await asyncio.gather(*list(lookup._tasks))
        assert "bot-d" not in identities.load_identities()["groups"]["g"]["by_bot"]
        lookup.schedule_identity_lookup(failure, "groups", "g")
        assert failure.calls == 1 and not lookup._tasks
    print("PASS - QQ 身份查询去重、失败退避和 Bot 隔离")

if __name__ == "__main__":
    asyncio.run(main())
