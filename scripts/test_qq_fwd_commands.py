"""验证 QQ 群手动转发命令解析和重复目标处理。"""

import nonebot

nonebot.init()

from plugins.qq_fwd import compute_target_list, parse_relay


def main():
    assert parse_relay("relay list") == ("list", None)
    assert parse_relay("relay 1") == ("number", 1)
    assert parse_relay("relay  001") == ("number", 1)
    assert parse_relay("relay") == ("invalid", None)
    assert parse_relay("relay ²") == ("invalid", None)
    assert parse_relay("relay １２") == ("invalid", None)
    assert parse_relay("relay " + "9" * 5000) == ("invalid", None)
    assert parse_relay("ordinary message") == (None, None)
    assert compute_target_list(
        [
            {"openid": "target-1", "enabled": False},
            {"openid": "target-1", "enabled": True, "name": "Target"},
        ],
        "source",
        {"target-1"},
    ) == [{"openid": "target-1", "name": "Target"}]
    print("PASS - QQ 手动转发命令解析和目标去重")


if __name__ == "__main__":
    main()
