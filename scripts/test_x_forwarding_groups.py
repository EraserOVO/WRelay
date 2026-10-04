"""验证 X 转发组（x_forwarding_groups）路由 + 旧配置兼容 + 游标不变。

覆盖（对应需求 10 个场景）：
1. 一个源一个组 → 组内全部群
2. 两个源不同组 → 互不串扰
3. 一个源属于两个组 → 并集
4. 两个组拥有同一个群 → 只出现一次
5. 组内群全局 disabled → 不投递
6. 源 disabled → 不投递
7. 无 x_forwarding_groups → 旧 x_rss_target_groups 生成默认组（行为一致）
8. x_rss_target_groups 为空 → 默认组 = 全部启用群
9. 新增群进组 → baseline 机制建游标，不补发历史
10. 移除群再重新加入 → 游标保留，不重复/不补发

另有：归一化测试（非法结构/悬空引用/重复 id/disabled 成员不裁剪）、
真实 RSS → _send_posts() 目标群计算验证（send_and_record 打桩，
绝不向真实 QQ 群发送）、Discord forwarding_groups 路由不受影响检查。

全部数据读写均在临时目录（settings.json / discord_last.json /
x_rss.json / stats 均打桩），不触碰真实 data/ 文件。

用法：.venv/Scripts/python.exe scripts/test_x_forwarding_groups.py
"""
import asyncio
import json
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import nonebot

nonebot.init()

from plugins import config as cfg
from plugins import dedup
from plugins import history as history_mod
from plugins import x_rss as x_rss_mod

TMP = Path(tempfile.mkdtemp(prefix="wrelay_xfg_test_"))

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL") + " - " + name + (" | " + detail if detail and not cond else ""))


def load_case(settings_dict):
    """写入临时 settings.json 并重置 config 缓存（强制下次重读）"""
    path = TMP / "settings.json"
    path.write_text(json.dumps(settings_dict, ensure_ascii=False), encoding="utf-8")
    cfg.SETTINGS_FILE = str(path)
    cfg._settings_cache = None
    cfg._settings_cache_mtime = None
    cfg._settings_cache_size = None


def groups(*openids, disabled=()):
    return [
        {"openid": o, "enabled": o not in disabled, "name": o, "remark": "", "qq_id": ""}
        for o in openids
    ]


def sources(*pairs):
    """pairs: (handle, enabled)"""
    return [{"source": h, "name": h, "enabled": e} for h, e in pairs]


BASE = {
    "qq_group_openids": groups("QA", "QB", "QC", "QD", disabled=("QD",)),
    "qq_user_openids": [],
    "discord_channels": [],
    "x_rss_sources": sources(("A", True), ("B", True)),
    "x_rsshub_base": "http://localhost:1200",
    "x_rss_poll_interval": 300,
}


def xfg(gid, name, src, grp, enabled=True):
    return {"id": gid, "name": name, "enabled": enabled, "sources": src, "groups": grp}


# =====================================================
# 场景 1-6：显式 x_forwarding_groups 路由
# =====================================================

def test_scenarios_1_to_6():
    # 场景 1：一个源，一个组 → QA/QB
    s = dict(BASE, x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QA", "QB"])])
    load_case(s)
    check("场景1 一个源一个组 -> 组内全部群",
          cfg.get_x_groups_for_source("A") == ["QA", "QB"],
          str(cfg.get_x_groups_for_source("A")))

    # 场景 2：两个源不同组 → 互不串扰
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "组1", ["A"], ["QA"]),
        xfg("g2", "组2", ["B"], ["QB"]),
    ])
    load_case(s)
    check("场景2 源A只到QA", cfg.get_x_groups_for_source("A") == ["QA"],
          str(cfg.get_x_groups_for_source("A")))
    check("场景2 源B只到QB", cfg.get_x_groups_for_source("B") == ["QB"],
          str(cfg.get_x_groups_for_source("B")))

    # 场景 2b：暂停组不投递，但同一源所属的工作组仍正常投递
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "暂停组", ["A"], ["QA"], enabled=False),
        xfg("g2", "工作组", ["A"], ["QB"], enabled=True),
    ])
    load_case(s)
    check("场景2b 暂停X组后跳过该组且保留工作组",
          cfg.get_x_groups_for_source("A") == ["QB"],
          str(cfg.get_x_groups_for_source("A")))

    # 场景 3：一个源属于两个组 → 并集（按全局启用顺序）
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "组1", ["A"], ["QB"]),
        xfg("g2", "组2", ["A"], ["QA"]),
    ])
    load_case(s)
    check("场景3 一个源两个组 -> 并集且按启用顺序",
          cfg.get_x_groups_for_source("A") == ["QA", "QB"],
          str(cfg.get_x_groups_for_source("A")))

    # 场景 4：两个组拥有同一个群 → 去重只出现一次
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "组1", ["A"], ["QA"]),
        xfg("g2", "组2", ["A"], ["QA"]),
    ])
    load_case(s)
    check("场景4 两组共享同群 -> 只出现一次",
          cfg.get_x_groups_for_source("A") == ["QA"],
          str(cfg.get_x_groups_for_source("A")))

    # 场景 5：组内群全局 disabled（QD）→ 不投递
    s = dict(BASE, x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QD"])])
    load_case(s)
    check("场景5 组内群全局disabled -> 不投递",
          cfg.get_x_groups_for_source("A") == [],
          str(cfg.get_x_groups_for_source("A")))

    # 场景 6：源 disabled → 不投递（即使属于组）
    s = dict(BASE, x_rss_sources=sources(("A", False), ("B", True)),
             x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QA"])])
    load_case(s)
    check("场景6 源disabled -> 不投递",
          cfg.get_x_groups_for_source("A") == [],
          str(cfg.get_x_groups_for_source("A")))


# =====================================================
# 场景 7-8：旧配置兼容（无 x_forwarding_groups）
# =====================================================

def test_scenarios_7_to_8():
    # 场景 7：无 x_forwarding_groups，旧 x_rss_target_groups 非空
    s = dict(BASE, x_rss_target_groups=["QA"])
    load_case(s)
    got = cfg.get_x_groups_for_source("A")
    check("场景7 旧target_groups非空 -> 默认组=白名单", got == ["QA"], str(got))
    check("场景7 默认组sources=全部启用源",
          cfg.get_x_forwarding_groups()[0]["sources"] == ["A", "B"],
          str(cfg.get_x_forwarding_groups()))
    check("场景7 不写盘（settings文件无x_forwarding_groups）",
          "x_forwarding_groups" not in json.loads(
              (TMP / "settings.json").read_text(encoding="utf-8")))

    # 场景 8：无 x_forwarding_groups 且 x_rss_target_groups 为空 → 全部启用群
    s = dict(BASE, x_rss_target_groups=[])
    load_case(s)
    got = cfg.get_x_groups_for_source("A")
    check("场景8 target_groups为空 -> 默认组=全部启用群",
          got == ["QA", "QB", "QC"], str(got))

    # 独立性：同文件里 Discord forwarding_groups 只含 QA，
    # X 默认组仍取全部启用群 → X 路由不读 Discord 组
    s = dict(BASE, x_rss_target_groups=[],
             discord_channels=[{"id": "C1", "name": "chan", "enabled": True}],
             forwarding_groups=[{"id": "default", "name": "转发组1",
                                 "channels": ["C1"], "groups": ["QA"]}])
    load_case(s)
    check("独立性 X路由不读Discord forwarding_groups",
          cfg.get_x_groups_for_source("A") == ["QA", "QB", "QC"],
          str(cfg.get_x_groups_for_source("A")))
    check("独立性 Discord路由不受影响",
          cfg.get_groups_for_channel("C1") == ["QA"],
          str(cfg.get_groups_for_channel("C1")))

    # Discord 转发组暂停：跳过暂停组，保留同频道所属的工作组
    s = dict(BASE,
             discord_channels=[{"id": "C1", "name": "chan", "enabled": True}],
             forwarding_groups=[
                 {"id": "d1", "name": "暂停组", "enabled": False,
                  "channels": ["C1"], "groups": ["QA"]},
                 {"id": "d2", "name": "工作组", "enabled": True,
                  "channels": ["C1"], "groups": ["QB"]},
             ])
    load_case(s)
    check("Discord暂停组后跳过该组且保留工作组",
          cfg.get_groups_for_channel("C1") == ["QB"],
          str(cfg.get_groups_for_channel("C1")))


# =====================================================
# 归一化：非法结构 / 悬空引用 / 重复 id / disabled 成员不裁剪
# =====================================================

def test_normalization():
    # 非法类型 → 回退默认组，不崩溃
    s = dict(BASE, x_forwarding_groups="junk")
    load_case(s)
    fgs = cfg.get_x_forwarding_groups()
    check("归一化 非法类型回退默认组",
          len(fgs) == 1 and fgs[0]["id"] == cfg.X_FORWARDING_GROUP_DEFAULT_ID,
          str(fgs))

    # 悬空引用清理 + 重复 id 去重
    s = dict(BASE, x_forwarding_groups=[
        xfg("x1", "组1", ["A", "GHOST_SRC"], ["QA", "GHOST_GRP"]),
        xfg("x1", "重复id", ["B"], ["QB"]),
        xfg("x2", "组2", ["B"], ["QB"]),
    ])
    load_case(s)
    fgs = cfg.get_x_forwarding_groups()
    check("归一化 悬空source/group引用被剔除",
          fgs[0]["sources"] == ["A"] and fgs[0]["groups"] == ["QA"], str(fgs[0]))
    check("归一化 重复id只保留第一个", len(fgs) == 2, str(fgs))

    # disabled 成员保留在组内（enabled 只影响运行，不裁剪成员关系）
    s = dict(BASE, x_rss_sources=sources(("A", False), ("B", True)),
             x_forwarding_groups=[xfg("x1", "组1", ["A"], ["QD"])])
    load_case(s)
    fgs = cfg.get_x_forwarding_groups()
    check("归一化 disabled源/群保留组成员关系",
          fgs[0]["sources"] == ["A"] and fgs[0]["groups"] == ["QD"], str(fgs[0]))
    check("归一化 disabled源路由仍为空",
          cfg.get_x_groups_for_source("A") == [],
          str(cfg.get_x_groups_for_source("A")))


# =====================================================
# 场景 9-10：baseline / 游标（临时 discord_last.json）
# =====================================================

def write_last(data):
    path = TMP / "discord_last.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    history_mod.LAST_MESSAGE_FILE = str(path)
    history_mod._last = None
    history_mod._last_mtime = None
    history_mod._last_size = None
    return path


def read_last():
    return json.loads((TMP / "discord_last.json").read_text(encoding="utf-8"))


async def test_scenarios_9_10_async():
    x_rss_mod.X_STATE_FILE = str(TMP / "x_rss.json")

    # 场景 9：新增群进组 → 只为新群建 baseline，不补发历史
    s = dict(BASE, x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QA", "QB"])])
    load_case(s)
    write_last({"x_rss:A": {"QA": "100"}})
    channel_key = x_rss_mod._source_channel_key("A")
    changed = await x_rss_mod._ensure_baselines(channel_key, ["QA", "QB"], "150")
    last = read_last()
    check("场景9 新群QB建baseline=最新ID，老群QA游标不动",
          changed and last["x_rss:A"]["QB"] == "150" and last["x_rss:A"]["QA"] == "100",
          str(last))
    targets_old = dedup.select_target_groups(
        history_mod.load_last_messages()[channel_key], ["QA", "QB"], "120")
    check("场景9 窗口内旧推文不投给新群QB", targets_old == ["QA"], str(targets_old))

    # 场景 10：移除群再重新加入 → 游标保留，不重复不补发
    # （QA/QB 游标都在 150：窗口内推文 120 对两边都是旧推文）
    write_last({"x_rss:A": {"QA": "150", "QB": "150"}})
    before = read_last()
    s = dict(BASE, x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QA"])])
    load_case(s)   # QB 被移出组
    await x_rss_mod._ensure_baselines(channel_key, ["QA"], "160")
    check("场景10 移出组不改游标文件", read_last() == before, str(read_last()))
    s = dict(BASE, x_forwarding_groups=[xfg("g1", "组1", ["A"], ["QA", "QB"])])
    load_case(s)   # QB 重新加入
    changed = await x_rss_mod._ensure_baselines(channel_key, ["QA", "QB"], "160")
    targets = dedup.select_target_groups(
        history_mod.load_last_messages()[channel_key], ["QA", "QB"], "120")
    check("场景10 重新加入后游标仍在、旧推文不补发",
          not changed and targets == [], str(read_last()) + " targets=" + str(targets))


# =====================================================
# 真实 RSS → _send_posts() 目标群计算（发送打桩，不触网不发群）
# =====================================================

def _make_sample_posts():
    """真实 parse_feed 路径的样例 RSS（RSSHub 不可用时的回退输入）"""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>xx_Chon_xx</title>
<item><guid isPermaLink="false">https://twitter.com/A/status/200</guid>
<title>tweet 200</title><description>hello 200</description>
<link>https://twitter.com/A/status/200</link><author>A</author></item>
<item><guid isPermaLink="false">https://twitter.com/A/status/201</guid>
<title>tweet 201</title><description>hello 201</description>
<link>https://twitter.com/A/status/201</link><author>A</author></item>
</channel></rss>"""
    return xml.encode("utf-8")


async def test_send_posts_targets_async():
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "组1", ["A"], ["QA", "QB"]),
        xfg("g2", "组2", ["B"], ["QC"]),
    ])
    load_case(s)
    # 游标预设为 "1"（低于任何真实/样例推文 ID）：
    # _ensure_baselines 不会重建基线，推文走正常发送路径；
    # QX 是无关群哨兵（不在任何组），验证游标推进不触碰它
    write_last({"x_rss:A": {"QA": "1", "QB": "1", "QX": "5"}, "x_rss:B": {}})

    # 优先拉真实 RSSHub feed；不可用时回退样例 RSS（都走真实 parse_feed）
    url = "http://localhost:1200/twitter/user/A"
    body, _etag, err = await x_rss_mod._fetch_feed(url)
    if err or not body:
        print("NOTE - RSSHub 不可用({})，使用样例 RSS 走真实 parse_feed".format(err))
        body = _make_sample_posts()
    else:
        print("NOTE - 使用真实 RSSHub feed: " + url)
    posts = x_rss_mod.parse_feed(body)
    expected_ids = sorted({p["id"] for p in posts}, key=int)
    expected_latest = expected_ids[-1]

    captured = []

    async def fake_send(
        channel_key, message_id, text_parts, media_items, targets,
        idempotency_key=None,
    ):
        # 复刻真实 send_and_record 的成功路径：发送后 apply_success 落游标
        captured.append((channel_key, message_id, list(targets)))
        await history_mod.update_last_messages(
            lambda last: dedup.apply_success(
                dedup.normalize_channel_map(last, channel_key),
                {g: True for g in targets},
                message_id,
            )
        )
        return {g: True for g in targets}

    with mock.patch.object(x_rss_mod, "send_and_record", side_effect=fake_send), \
         mock.patch.object(x_rss_mod, "record"), \
         mock.patch.object(x_rss_mod, "fetch_bytes_many", return_value={}):
        await x_rss_mod._send_posts(
            {"key": "A", "url": url, "name": "测试源A"}, posts)

    ids = sorted({m[1] for m in captured}, key=int)
    targets_all = [sorted(m[2]) for m in captured]
    check("集成 伪频道键保持 x_rss:{handle}",
          all(m[0] == "x_rss:A" for m in captured), str(captured))
    check("集成 源A只投递组1的QA/QB（不含组2的QC）",
          all(t == ["QA", "QB"] for t in targets_all), str(targets_all))
    check("集成 每条推文各发一次（旧→新）", ids == expected_ids, str(ids))
    last = read_last()
    check("集成 游标按群推进且不触碰无关群",
          last["x_rss:A"]["QA"] == expected_latest
          and last["x_rss:A"]["QB"] == expected_latest
          and last["x_rss:A"]["QX"] == "5"
          and last["x_rss:B"] == {},
          str(last))

    # 场景 4 的发送侧验证：两个组共享 QA → captured targets 无重复
    s = dict(BASE, x_forwarding_groups=[
        xfg("g1", "组1", ["A"], ["QA"]),
        xfg("g2", "组2", ["A"], ["QA"]),
    ])
    load_case(s)
    write_last({"x_rss:A": {"QA": "1"}})
    captured.clear()
    with mock.patch.object(x_rss_mod, "send_and_record", side_effect=fake_send), \
         mock.patch.object(x_rss_mod, "record"), \
         mock.patch.object(x_rss_mod, "fetch_bytes_many", return_value={}):
        await x_rss_mod._send_posts(
            {"key": "A", "url": url, "name": "测试源A"}, posts)
    check("集成 两组共享同群 -> 同一条推文只发送一次",
          all(m[2] == ["QA"] for m in captured) and len(captured) == len(ids),
          str(captured))


async def main_async():
    # 全部异步调用共用一个事件循环（history._save_lock 绑定首个 loop）
    test_scenarios_1_to_6()
    test_scenarios_7_to_8()
    test_normalization()
    await test_scenarios_9_10_async()
    await test_send_posts_targets_async()


def main():
    asyncio.run(main_async())

    failed = [r for r in RESULTS if not r[1]]
    print("\n===== {} passed, {} failed =====".format(
        len(RESULTS) - len(failed), len(failed)))
    for name, _, detail in failed:
        print("FAIL: {} | {}".format(name, detail))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
