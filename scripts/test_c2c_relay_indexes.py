"""Verify private QQ relay indexes reject malformed and oversized digits safely."""

import nonebot

nonebot.init()

from plugins.command import _parse_c2c_relay_indexes


def main():
    assert _parse_c2c_relay_indexes("1", 3) == [1]
    assert _parse_c2c_relay_indexes("001 3", 3) == [1, 3]
    assert _parse_c2c_relay_indexes("0", 3) is None
    assert _parse_c2c_relay_indexes("4", 3) is None
    assert _parse_c2c_relay_indexes("²", 3) is None
    assert _parse_c2c_relay_indexes("１２", 3) is None
    assert _parse_c2c_relay_indexes("1 nope", 3) is None
    assert _parse_c2c_relay_indexes("9" * 5000, 3) is None
    assert _parse_c2c_relay_indexes("0" * 5000 + "1", 1) == [1]
    assert _parse_c2c_relay_indexes("   ", 3) is None
    print("PASS - QQ 私聊 relay 序号只接受范围内 ASCII 十进制")


if __name__ == "__main__":
    main()
