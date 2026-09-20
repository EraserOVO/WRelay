"""统一运行 WRelay 回归测试。

测试脚本保持独立，便于单独定位；本入口负责统一 UTF-8 环境、工作目录和
退出码。用法：.venv/Scripts/python.exe scripts/run_tests.py
"""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_fetch_reference.py",
    "test_fetch_video.py",
    "test_retry_recovery.py",
    "test_startup_relay_race.py",
    "test_send_failure_logs.py",
    "test_failure_notification.py",
    "test_message_trace.py",
    "test_x_forwarding_groups.py",
    "test_x_poll_concurrency.py",
    "test_x_rss_fetch_retry.py",
    "test_x_rss_syndication_fallback.py",
    "test_x_rss_empty_item_skip.py",
    "test_translation.py",
    "test_forward_service.py",
    "test_message_datetime.py",
    "test_http_clients.py",
    "test_ai_model_fallback.py",
    "test_qq_event_compat.py",
    "test_discord_rest_retry.py",
    "test_panel_safety.py",
    "test_user_errors.py",
    "test_backup_restore.py",
    "test_persistence_safety.py",
    "test_tun_network_routing.py",
)


def main():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    failures = []

    for name in TESTS:
        path = ROOT / "scripts" / name
        result = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            print("PASS - " + name)
            continue
        failures.append(name)
        print("FAIL - {} (exit {})".format(name, result.returncode))
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)

    if failures:
        print("失败测试: " + ", ".join(failures))
        return 1
    print("全部测试通过: {} 个".format(len(TESTS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
