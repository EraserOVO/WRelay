"""Verify malformed X/Bilibili poll intervals fall back without loop errors."""

from unittest import mock

import nonebot

nonebot.init()

from plugins import config
from plugins.settings_model import SettingsModel, validate_settings


def main():
    settings = SettingsModel.default().to_dict()
    settings["x_rss_poll_interval"] = float("inf")
    settings["bilibili_poll_interval"] = float("inf")
    assert validate_settings(settings) is None

    with mock.patch.object(config, "_load_settings", return_value=settings):
        assert config.get_x_rss_poll_interval() == 300
        assert config.get_bilibili_poll_interval() == 300

    settings["x_rss_poll_interval"] = 60
    settings["bilibili_poll_interval"] = 90
    with mock.patch.object(config, "_load_settings", return_value=settings):
        assert config.get_x_rss_poll_interval() == 60
        assert config.get_bilibili_poll_interval() == 90

    print("PASS - X/Bilibili 非有限轮询间隔回退到默认值")


if __name__ == "__main__":
    main()
