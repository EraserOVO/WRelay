"""统一运行 WRelay 回归测试。

测试脚本保持独立，便于单独定位；本入口负责统一 UTF-8 环境、工作目录、
单项超时和退出码。用法：.venv/Scripts/python.exe scripts/run_tests.py
"""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_TIMEOUT_SECONDS = 120
TESTS = (
    "test_fetch_reference.py",
    "test_fetch_text_emoji.py",
    "test_fetch_pagination_ids.py",
    "test_fetch_video.py",
    "test_media_download_dedup.py",
    "test_media_client_lifecycle.py",
    "test_embed_link_whitespace.py",
    "test_retry_recovery.py",
    "test_retry_persistence_nonblocking.py",
    "test_cross_source_delivery_dedup.py",
    "test_startup_relay_race.py",
    "test_discord_relay_config_snapshot.py",
    "test_relay_route_config_nonblocking.py",
    "test_relay_queue_shutdown.py",
    "test_send_failure_logs.py",
    "test_failure_notification.py",
    "test_multi_qq_bot_routing.py",
    "test_message_trace.py",
    "test_x_rss_send_results.py",
    "test_x_forwarding_groups.py",
    "test_x_poll_concurrency.py",
    "test_rss_poll_loop_lifecycle.py",
    "test_rss_source_config_nonblocking.py",
    "test_rsshub_cold_start.py",
    "test_rss_poll_config_snapshot.py",
    "test_rss_delivery_config_nonblocking.py",
    "test_rss_message_type_filter.py",
    "test_rss_posts_cache_lifecycle.py",
    "test_rss_invalid_etag.py",
    "test_rss_poll_interval_safety.py",
    "test_rss_feed_root_validation.py",
    "test_rss_etag_removal.py",
    "test_rss_duplicate_state_keys.py",
    "test_x_rss_source_url_cache.py",
    "test_x_rss_metadata_io_nonblocking.py",
    "test_bilibili_source_url_cache.py",
    "test_x_rss_fetch_retry.py",
    "test_x_rss_syndication_fallback.py",
    "test_x_rss_empty_item_skip.py",
    "test_bilibili_parser.py",
    "test_bilibili_manual_opus.py",
    "test_relay_filter_supported_types.py",
    "test_bilibili_view_error_text.py",
    "test_bilibili_forwarding_groups.py",
    "test_bilibili_auto_resolve.py",
    "test_bilibili_rate_limit.py",
    "test_bilibili_protection_recovery.py",
    "test_bilibili_protection_state_read_nonblocking.py",
    "test_bilibili_state_write_nonblocking.py",
    "test_bilibili_metadata_io_nonblocking.py",
    "test_translation.py",
    "test_translation_config_nonblocking.py",
    "test_translation_client_lifecycle.py",
    "test_forward_service.py",
    "test_message_datetime.py",
    "test_http_clients.py",
    "test_ai_model_fallback.py",
    "test_qq_event_compat.py",
    "test_qq_keyboard.py",
    "test_qq_interaction.py",
    "test_restart_confirmation.py",
    "test_manage_api_error_responses.py",
    "test_manage_group_at_hint.py",
    "test_qq_fwd_commands.py",
    "test_qq_fwd_processing.py",
    "test_qq_fwd_admission.py",
    "test_qq_c2c_admission.py",
    "test_qq_fwd_media_lifecycle.py",
    "test_qq_fwd_config_nonblocking.py",
    "test_command_config_nonblocking.py",
    "test_c2c_relay_content_errors.py",
    "test_qq_fwd_timeout_config.py",
    "test_c2c_relay_indexes.py",
    "test_qq_quote_relay.py",
    "test_scheduled_message_targets.py",
    "test_c2c_consumed_guard.py",
    "test_qq_crypto_compat.py",
    "test_desktop_controls.py",
    "test_discord_rest_retry.py",
    "test_openid_logging_nonblocking.py",
    "test_registration_write_nonblocking.py",
    "test_registration_admission.py",
    "test_user_registration_dedup.py",
    "test_channel_metadata_concurrency.py",
    "test_panel_safety.py",
    "test_settings_update_preferences.py",
    "test_desktop_modal_grid.py",
    "test_onboarding.py",
    "test_user_errors.py",
    "test_backup_restore.py",
    "test_maintenance.py",
    "test_persistence_safety.py",
    "test_history_async_read.py",
    "test_backfill_clear_concurrency.py",
    "test_management_api_reads_nonblocking.py",
    "test_backfill_api_timeout.py",
    "test_backfill_config_nonblocking.py",
    "test_active_group_dedup.py",
    "test_forwarding_group_normalization.py",
    "test_sender_group_dedup.py",
    "test_sender_config_nonblocking.py",
    "test_scheduled_message_dates.py",
    "test_tun_network_routing.py",
)
MANUAL_TESTS = {
    "test_compiled_installer.py": "requires the built installer and performs an install/uninstall smoke test",
    "test_desktop_controls_native.py": "requires pythonnet and a native WinForms UI thread",
    "test_desktop_titlebar.py": "requires a native WebView2 window",
}


def verify_test_inventory():
    """Fail when a test script is neither automated nor explicitly manual."""
    actual = {path.name for path in (ROOT / "scripts").glob("test_*.py")}
    automated = set(TESTS)
    manual = set(MANUAL_TESTS)
    duplicates = sorted(name for name in automated if TESTS.count(name) > 1)
    overlap = sorted(automated & manual)
    unclassified = sorted(actual - automated - manual)
    missing = sorted((automated | manual) - actual)

    issues = []
    if duplicates:
        issues.append("自动测试重复登记: " + ", ".join(duplicates))
    if overlap:
        issues.append("自动与手工测试重复分类: " + ", ".join(overlap))
    if unclassified:
        issues.append("未分类测试脚本: " + ", ".join(unclassified))
    if missing:
        issues.append("清单引用不存在的脚本: " + ", ".join(missing))
    if issues:
        print("FAIL - 测试脚本清单不完整")
        for issue in issues:
            print("  " + issue)
        return False

    print("PASS - 测试脚本清单完整：自动 {} 个，手工 {} 个".format(
        len(automated), len(manual)
    ))
    return True


def main():
    if not verify_test_inventory():
        return 1
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    failures = []

    for name in TESTS:
        path = ROOT / "scripts" / name
        try:
            result = subprocess.run(
                [sys.executable, "-u", str(path)],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TEST_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            failures.append(name)
            print("FAIL - {} (超时，超过 {} 秒)".format(
                name, TEST_TIMEOUT_SECONDS
            ))
            for label, output in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                if not output:
                    continue
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                print("{}:".format(label))
                print(output)
            continue
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
    if sys.argv[1:] == ["--check-inventory"]:
        raise SystemExit(0 if verify_test_inventory() else 1)
    raise SystemExit(main())
