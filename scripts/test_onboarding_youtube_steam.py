"""Isolated onboarding checks for YouTube and Steam sources and routes."""
from copy import deepcopy
import ast
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from panel.onboarding import apply_draft, clean_draft, source_spec
from plugins.settings_model import SettingsModel, validate_settings


def make_draft(purpose, value, kind="game", bot="1001", group="g1"):
    return clean_draft({"purpose": purpose, "step": "4", "bot_id": bot,
                        "source_value": value, "source_kind": kind, "groups": [group]})


async def test_source_check(settings):
    tree = ast.parse((Path(__file__).resolve().parents[1] / "panel/onboarding_api.py").read_text(encoding="utf-8"))
    install = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install_onboarding")
    handler = next(node for node in install.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "source_check")
    handler.decorator_list = []
    async def body(request):
        return (request, None) if isinstance(request, dict) else (None, {"ok": False})
    async def run(function, *args):
        return function(*args)
    namespace = {"Request": object, "json_object_body": body, "clean_draft": clean_draft,
                 "source_spec": source_spec, "run_in_thread_to_completion": run,
                 "p": SimpleNamespace(load_settings=lambda: settings),
                 "error": lambda message: {"ok": False, "msg": message}}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), "onboarding_api.py", "exec"), namespace)
    check = namespace["source_check"]
    assert (await check(make_draft("youtube", "@example")))["ok"]
    assert (await check(make_draft("steam", "620")))["ok"]
    assert not (await check(make_draft("steam", "https://example.com")))["ok"]
    assert not (await check([]))["ok"]


def main():
    settings = SettingsModel.default().to_dict()
    settings["qq_group_openids"] = [
        {"openid": "g1", "bot_id": "1001", "enabled": False},
        {"openid": "personal:22222", "bot_id": "personal:12345", "enabled": False},
    ]
    original = deepcopy(settings)
    cases = [
        (make_draft("youtube", "https://www.youtube.com/@example"), "youtube", "channel:@example"),
        (make_draft("steam", "https://store.steampowered.com/app/620/"), "steam", None),
        (make_draft("steam", "https://store.steampowered.com/developer/example", "creator"), "steam", None),
    ]
    for draft, chain, expected in cases:
        result = apply_draft(settings, draft, "test", ["1001", "personal:12345"])
        source = result[chain + "_sources"][0]
        assert expected is None or source["id"] == expected
        assert source["enabled"] and source["translation_enabled"]
        assert result[chain + "_forwarding_groups"][0]["sources"] == [source["id"]]
        assert result[chain + "_forwarding_groups"][0]["groups"] == ["g1"]
        assert validate_settings(result, validate_forwarding_groups=True) is None
        assert apply_draft(result, draft, "test", ["1001", "personal:12345"]) == result
    assert settings == original

    existing = deepcopy(settings)
    existing["steam_sources"] = [{"id": "old", "kind": "game", "value": "620", "name": "原来源",
                                   "enabled": False, "events": ["news"], "translation_enabled": False}]
    reused = apply_draft(existing, cases[1][0], "test", ["1001"])
    assert len(reused["steam_sources"]) == 1 and reused["steam_sources"][0]["events"] == ["news"]
    assert reused["steam_sources"][0]["translation_enabled"] is False
    personal = make_draft("youtube", "@example", bot="personal:12345", group="personal:22222")
    assert apply_draft(settings, personal, "test", ["1001", "personal:12345"])["qq_group_openids"][1]["enabled"]
    for draft in (make_draft("youtube", "https://example.com/watch"),
                  make_draft("steam", "https://example.com/620"),
                  make_draft("steam", "620", "creator"),
                  make_draft("youtube", "@example", bot="personal:12345", group="g1")):
        try:
            apply_draft(settings, draft, "test", ["1001", "personal:12345"])
        except ValueError:
            continue
        raise AssertionError("invalid source or wrong account accepted")
    legacy = deepcopy(settings)
    legacy["youtube_sources"] = [{"value": "@example", "filter_keywords": ["keep"]}]
    assert source_spec({**personal, "source_id": "channel:@example"}, legacy)[2]["filter_keywords"] == ["keep"]
    asyncio.run(test_source_check(settings))
    print("PASS - YouTube / Steam onboarding sources, routes, reuse and account ownership")


if __name__ == "__main__":
    main()
