"""Verify QQ manual relay timeout settings fall back on overflow values."""

from unittest import mock

import nonebot

nonebot.init()

from plugins import config
from plugins.settings_model import SettingsModel, validate_settings


def main():
    settings = SettingsModel.default().to_dict()
    settings["qq_fwd_recency_limit"] = float("inf")
    settings["qq_fwd_select_timeout"] = float("inf")
    assert validate_settings(settings) is None

    with mock.patch.object(config, "_load_settings", return_value=settings):
        assert config.get_qq_fwd_recency_limit() == 1800
        assert config.get_qq_fwd_select_timeout() == 60

    settings["qq_fwd_recency_limit"] = 900
    settings["qq_fwd_select_timeout"] = 120
    with mock.patch.object(config, "_load_settings", return_value=settings):
        assert config.get_qq_fwd_recency_limit() == 900
        assert config.get_qq_fwd_select_timeout() == 120

    print("PASS - QQ 手动转发时间窗与选择超时回退")


if __name__ == "__main__":
    main()
