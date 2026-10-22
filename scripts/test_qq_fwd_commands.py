"""验证 QQ 群手动转发命令解析和纯文本目标名单。"""

import nonebot

nonebot.init()

from plugins.qq_fwd import (
    build_relay_list,
    compute_target_list,
    parse_relay,
)


def main():
    assert parse_relay("/转发 列表") == ("list", None)
    assert parse_relay("/转发 列表 2") == ("list", None)
    assert parse_relay("/转发") == ("list", None)
    assert parse_relay("/转发 1") == ("forward", [1])
    assert parse_relay("/转发  001") == ("forward", [1])
    assert parse_relay("/转发 1 2") == ("forward", [1, 2])
    assert parse_relay("/转发 1 1") == ("forward", [1, 1])
    # 选择卡写法已移除：非序号参数一律无效。
    assert parse_relay("/转发 选 2 3") == ("invalid", None)
    assert parse_relay("/转发 选") == ("invalid", None)
    assert parse_relay("/转发 清空") == ("invalid", None)
    assert parse_relay("/转发 取消") == ("invalid", None)
    assert parse_relay("/转发 开始") == ("invalid", None)
    assert parse_relay("/转发 ²") == ("invalid", None)
    assert parse_relay("/转发 １２") == ("invalid", None)
    assert parse_relay("/转发 " + "9" * 5000) == ("invalid", None)
    assert parse_relay("relay list") == (None, None)
    assert parse_relay("ordinary message") == (None, None)

    assert compute_target_list(
        [
            {"openid": "target-1", "enabled": False},
            {"openid": "target-1", "enabled": True, "name": "Target"},
        ],
        "source",
        {"target-1"},
    ) == [{"openid": "target-1", "name": "Target"}]

    targets = [{"openid": "target-1", "name": "Target"}]
    relay_list = build_relay_list(targets)
    assert "1. Target" in relay_list
    assert "引用要转发的消息后发送 /转发 序号（可写多个，如 /转发 1 3）。" in relay_list
    assert "未引用时使用最近一条消息。" in relay_list
    assert "/转发 全部" not in relay_list

    private_list = build_relay_list(targets, allow_all=True)
    assert "1. Target" in private_list
    assert "/转发 全部 可发往全部群" in private_list

    empty_list = build_relay_list([])
    assert "当前没有可转发的目标群" in empty_list
    print("PASS - QQ 手动转发命令解析、纯文本名单与目标去重")


if __name__ == "__main__":
    main()
