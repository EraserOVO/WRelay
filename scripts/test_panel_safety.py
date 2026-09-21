"""验证管理面板的认证、Origin、防坏配置和 PID 边界。

不启动 Uvicorn、不修改真实 settings；用法：
.venv/Scripts/python.exe scripts/test_panel_safety.py
"""
import asyncio
import importlib.util
import io
import json
import sys
import tempfile
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response


ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "panel" / "管理面板.pyw"
_TEST_STDOUT = sys.stdout
spec = importlib.util.spec_from_file_location("wrelay_panel_safety", PANEL_PATH)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
# 面板模块为记录日志会接管 sys.stdout；测试结束后恢复控制台输出。
sys.stdout = _TEST_STDOUT


def make_request(path, headers=None):
    raw_headers = [
        (str(k).lower().encode(), str(v).encode())
        for k, v in (headers or {}).items()
    ]
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": raw_headers,
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", panel.PANEL_PORT),
        "client": ("127.0.0.1", 50000),
    })


async def pass_through(_request):
    return Response("ok", status_code=200)


def check_guard(path, headers):
    return asyncio.run(panel.origin_guard(
        make_request(path, headers), pass_through
    ))


def main():
    assert check_guard("/api/auth/bootstrap", {}).status_code == 200
    assert check_guard(
        "/api/auth/bootstrap", {"Origin": "http://evil.example"}
    ).status_code == 403
    assert check_guard("/api/status", {}).status_code == 401
    assert check_guard(
        "/api/status", {"X-WRelay-Token": panel.PANEL_TOKEN}
    ).status_code == 200
    for path in (
        "/api/backup/export",
        "/api/backup/restore",
        "/api/translation/ai-test",
        "/api/translation/ai-test-record",
        "/api/translation/ai-env",
    ):
        assert check_guard(path, {}).status_code == 401
        assert check_guard(
            path, {"X-WRelay-Token": panel.PANEL_TOKEN}
        ).status_code == 200
    print("PASS - 面板 Origin 与 API 令牌边界")

    valid_settings = panel.get_default_settings()
    valid_settings.update({
        "qq_group_openids": [],
        "qq_user_openids": [],
        "discord_channels": [],
    })
    assert panel._validate_settings(valid_settings) is None
    assert panel._validate_settings({"qq_group_openids": {}})
    assert panel._validate_settings({
        "qq_group_openids": [],
        "qq_user_openids": [],
        "discord_channels": [],
        "backfill_limit": 0,
    })
    print("PASS - 配置结构校验拒绝非法载荷")

    existing_bots = [{"id": "A", "token": "A", "secret": "old-secret", "sandbox": False}]
    updated_bots = panel._prepare_qq_bots([
        {"appid": "A", "secret": ""},
        {"appid": "B", "secret": "new-secret"},
    ], existing_bots)
    assert [bot["id"] for bot in updated_bots] == ["A", "B"]
    assert updated_bots[0]["secret"] == "old-secret"
    assert updated_bots[0]["sandbox"] is False
    assert updated_bots[1]["secret"] == "new-secret"
    try:
        panel._prepare_qq_bots([{"appid": "B", "secret": ""}], existing_bots)
        raise AssertionError("新增 Bot 缺少 Secret 应被拒绝")
    except ValueError:
        pass
    assert panel._public_qq_bots(updated_bots) == [{"appid": "A"}, {"appid": "B"}]
    print("PASS - QQ Bot 多凭据保存保留旧 Secret 且不向前端泄露")

    with tempfile.TemporaryDirectory(prefix="wrelay_group_bot_test_") as tmp:
        old_data_dir = panel.DATA_DIR
        try:
            panel.DATA_DIR = tmp
            (Path(tmp) / "qq_group_openids.json").write_text(
                json.dumps({
                    "group_openids": ["GROUP_A", "GROUP_B"],
                    "group_bot_ids": {"GROUP_A": "BOT_A", "GROUP_B": "BOT_B"},
                }),
                encoding="utf-8",
            )
            settings = {"qq_group_openids": []}
            assert panel.sync_discovered_groups(settings) == 2
            assert settings["qq_group_openids"] == [
                {"openid": "GROUP_A", "enabled": False, "remark": "自动发现，点击启用", "bot_id": "BOT_A"},
                {"openid": "GROUP_B", "enabled": False, "remark": "自动发现，点击启用", "bot_id": "BOT_B"},
            ]
        finally:
            panel.DATA_DIR = old_data_dir
    print("PASS - 自动发现群保留 QQ Bot 归属")

    with tempfile.TemporaryDirectory(prefix="wrelay_panel_test_") as tmp:
        path = Path(tmp) / "settings.json"
        old = panel.SETTINGS_FILE
        try:
            panel.SETTINGS_FILE = str(path)
            path.write_text(json.dumps({"v": 1}), encoding="utf-8")
            first = panel._settings_revision()
            path.write_text(json.dumps({"v": 2, "padding": "changed"}), encoding="utf-8")
            second = panel._settings_revision()
        finally:
            panel.SETTINGS_FILE = old
        assert first != second
    print("PASS - settings revision 能识别外部修改")

    assert panel._proc_exists(0) is False
    assert panel._proc_exists(2 ** 31 - 1) is False
    print("PASS - PID 无效值不会被判定为运行中")

    class FakeResponse:
        def __init__(self, body=b'{"choices":[{"message":{"content":"OK"}}]}'):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.body

    with patch.object(panel, "_read_env_secret", return_value="test-key"), patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ):
        assert panel._probe_ai_api({
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key_env": "WRELAY_TEST_KEY",
            "timeout_seconds": 5,
        }) == "test-model"
    # 面板 API 需要把检测成功的模型名称返回给二级配置窗口，供其显示。
    class JsonRequest:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    with patch.object(panel, "_probe_ai_api", return_value="verified-model"):
        response = asyncio.run(panel.api_translation_ai_test(JsonRequest({
            "base_url": "https://example.test/v1",
            "model": "verified-model",
        })))
        assert response["model"] == "verified-model"
        assert response["diagnostic"]["category"] == "success"
        assert response["diagnostic"]["detail"] == "接口返回了有效内容"
        assert "endpoint" not in response["diagnostic"]
        assert "http_status" not in response["diagnostic"]

    with patch.object(
        panel,
        "_probe_ai_api",
        side_effect=panel.AiProbeError(
            "AI API 返回 HTTP 503：服务不可用",
            category="http",
            detail="服务不可用",
            http_status=503,
        ),
    ):
        response = asyncio.run(panel.api_translation_ai_test(JsonRequest({
            "base_url": "https://example.test/v1",
            "model": "failed-model",
        })))
        payload = json.loads(response.body)
        assert payload["diagnostic"]["category"] == "http"
        assert payload["diagnostic"]["detail"] == "服务不可用"
        assert "endpoint" not in payload["diagnostic"]
        assert "http_status" not in payload["diagnostic"]

    record_settings = panel.get_default_settings()
    record_settings["ai_profiles"] = [{
        "id": "profile-1",
        "name": "测试配置",
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key_env": "WRELAY_TEST_KEY",
        "timeout_seconds": 5,
        "max_output_tokens": 8,
    }]
    with patch.object(panel, "_load_settings_snapshot", return_value=(record_settings, "revision")), \
         patch.object(panel, "_settings_revision", return_value="revision"), \
         patch.object(panel, "save_settings") as save_settings:
        response = asyncio.run(panel.api_translation_ai_test_record(JsonRequest({
            "profile_id": "profile-1",
            "record": {
                "ok": False,
                "model": "test-model",
                "message": "检测失败",
                "at": 123,
                "diagnostic": {
                    "category": "empty_content",
                    "endpoint": "https://example.test/v1/chat/completions?token=hidden",
                    "http_status": 503,
                    "detail": "choices[0].message.content 为空",
                },
            },
        })))
        assert response["ok"] is True
        assert record_settings["ai_profiles"][0]["last_test"]["ok"] is False
        assert record_settings["ai_profiles"][0]["last_test"]["diagnostic"]["category"] == "empty_content"
        assert "endpoint" not in record_settings["ai_profiles"][0]["last_test"]["diagnostic"]
        assert "http_status" not in record_settings["ai_profiles"][0]["last_test"]["diagnostic"]
        save_settings.assert_called_once()
    with patch.object(panel, "_read_env_secret", return_value=""), patch(
        "urllib.request.urlopen", return_value=FakeResponse()
    ):
        assert panel._probe_ai_api({
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "api_key_env": "WRELAY_TEST_KEY",
            "api_key": "typed-key",
            "timeout_seconds": 5,
        }) == "test-model"
    http_error = HTTPError(
        "https://example.test/v1/chat/completions",
        400,
        "Bad Request",
        {},
        io.BytesIO(b'{"error":{"message":"model is not supported"}}'),
    )
    with patch.object(panel, "_read_env_secret", return_value="test-key"), patch(
        "urllib.request.urlopen", side_effect=[
            http_error,
            FakeResponse(b'{"data":[{"id":"DeepSeek-V4.1-Flash"}]}'),
        ]
    ):
        try:
            panel._probe_ai_api({
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key_env": "WRELAY_TEST_KEY",
                "timeout_seconds": 5,
            })
        except RuntimeError as exc:
            assert str(exc) == "AI API 返回 HTTP 400：model is not supported；当前可用模型：DeepSeek-V4.1-Flash"
            assert exc.category == "http"
            assert exc.http_status == 400
            assert exc.detail == "model is not supported"
        else:
            raise AssertionError("HTTP 错误应包含服务端返回的安全说明")
    try:
        panel._probe_ai_api({"base_url": "", "model": "", "api_key_env": ""})
    except ValueError:
        pass
    else:
        raise AssertionError("缺失 AI API 配置应被拒绝")
    print("PASS - AI API 检测使用最小请求且不暴露密钥")


if __name__ == "__main__":
    main()
