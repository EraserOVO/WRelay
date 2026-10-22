"""按链路裁剪消息类型筛选的回归测试。

覆盖五件事：
1. 各链路支持的类型集（plugins/filter.py 常量）
2. 存量配置的兼容：读取层归一化、校验层接受、旧名「文件」→「视频」迁移
3. 「显式空列表」与「只存了不支持类型」的语义区分
4. 各链路支持集在前后端保持一致，且消息类型只渲染支持项
5. 消息类型区域在所有 scope 的筛选弹窗中都可见（含三种手动转发）
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.config import _c2c_relay_filter
from plugins.filter import (
    ALL_MESSAGE_TYPES,
    BILIBILI_MESSAGE_TYPES,
    DISCORD_MESSAGE_TYPES,
    LEGACY_MESSAGE_TYPE_ALIASES,
    MANUAL_RELAY_FILTER_TYPES,
    MESSAGE_TYPE_VIDEO,
    X_MESSAGE_TYPES,
    get_enabled_message_types,
    normalize_message_types,
)
from plugins.settings_model import SettingsModel, validate_settings


LEGACY_SIX = ["文字", "图片", "语音", "文件", "链接", "表情"]


def main():
    # ---------- 支持集 ----------
    assert DISCORD_MESSAGE_TYPES == ALL_MESSAGE_TYPES
    assert set(DISCORD_MESSAGE_TYPES) == set(LEGACY_SIX)
    # X：正文（含链接与话题）+ 媒体（图片/视频）；无独立链接、音频、表情组件
    assert X_MESSAGE_TYPES == ("文字", "图片", "视频")
    # Bilibili：标题/正文 + 封面 + 投稿地址 + 视频本体
    assert BILIBILI_MESSAGE_TYPES == ("文字", "图片", "链接", "视频")
    # 「视频」是 X/Bilibili 的规范名；Discord 的通用附件才叫「文件」
    assert MESSAGE_TYPE_VIDEO == "视频"
    assert "文件" not in X_MESSAGE_TYPES and "文件" not in BILIBILI_MESSAGE_TYPES
    assert set(X_MESSAGE_TYPES) <= set(BILIBILI_MESSAGE_TYPES)
    assert set(X_MESSAGE_TYPES) - set(DISCORD_MESSAGE_TYPES) == {"视频"}
    assert LEGACY_MESSAGE_TYPE_ALIASES == {"文件": "视频"}
    assert set(MANUAL_RELAY_FILTER_TYPES) == {
        "qq_c2c_relay_filter", "x_c2c_relay_filter", "bilibili_c2c_relay_filter"
    }

    # ---------- 旧名迁移：渲染门控必须真的认得「视频」----------
    # 关键回归：曾出现渲染器检查「视频」而规范名是「文件」，导致视频永远
    # 发不出去。这里锁定"规范集合里必须有渲染器检查的那个名字"。
    for supported in (X_MESSAGE_TYPES, BILIBILI_MESSAGE_TYPES):
        enabled = get_enabled_message_types(
            {"filter_message_types": list(supported)}, supported
        )
        assert MESSAGE_TYPE_VIDEO in enabled, supported
    # 旧配置存的「文件」在 X/Bilibili 下必须迁移为「视频」
    migrated = normalize_message_types(["文字", "文件"], X_MESSAGE_TYPES)
    assert migrated == ["文字", "视频"], migrated
    # Discord 的「文件」是真正的附件类型，不迁移
    assert normalize_message_types(["文字", "文件"], DISCORD_MESSAGE_TYPES) == [
        "文字", "文件",
    ]
    assert get_enabled_message_types(
        {"filter_message_types": ["文字", "图片", "文件"]}, X_MESSAGE_TYPES
    ) == {"文字", "图片", "视频"}
    # normalize 保持 supported 的顺序
    assert normalize_message_types(["视频", "文字"], X_MESSAGE_TYPES) == ["文字", "视频"]

    # ---------- get_enabled_message_types 归一化 ----------
    # 老配置六类型全量 → 收敛到该链路支持集
    assert get_enabled_message_types(
        {"filter_message_types": LEGACY_SIX}, X_MESSAGE_TYPES
    ) == set(X_MESSAGE_TYPES)
    # 缺省字段 → 全部支持类型
    assert get_enabled_message_types({}, X_MESSAGE_TYPES) == set(X_MESSAGE_TYPES)
    # 显式空列表 → 不转发任何内容（用户明确取消勾选全部）
    assert get_enabled_message_types({"filter_message_types": []}, X_MESSAGE_TYPES) == set()
    # 只存了不支持的类型 → 视为未筛选，避免转发意外全部失效
    assert get_enabled_message_types(
        {"filter_message_types": ["语音", "表情"]}, X_MESSAGE_TYPES
    ) == set(X_MESSAGE_TYPES)
    # 部分选择
    assert get_enabled_message_types(
        {"filter_message_types": ["文字"]}, X_MESSAGE_TYPES
    ) == {"文字"}
    # 不传 supported（Discord 链路）时保持原有六类型行为
    assert get_enabled_message_types({"filter_message_types": ["语音"]}) == {"语音"}
    assert get_enabled_message_types({"filter_message_types": []}) == set()

    # ---------- config._c2c_relay_filter 读取层 ----------
    def read(key, types, supported):
        return _c2c_relay_filter({key: {"filter_message_types": types}}, key, supported)

    # 老配置六类型 → 归一化为支持集（含旧名「文件」→「视频」迁移）
    assert read("x_c2c_relay_filter", LEGACY_SIX, X_MESSAGE_TYPES) == {
        "filter_message_types": ["文字", "图片", "视频"]
    }
    # 只存旧名「文件」也要迁移，不能丢
    assert read("x_c2c_relay_filter", ["文件"], X_MESSAGE_TYPES) == {
        "filter_message_types": ["视频"]
    }
    assert read("bilibili_c2c_relay_filter", LEGACY_SIX, BILIBILI_MESSAGE_TYPES) == {
        "filter_message_types": ["文字", "图片", "链接", "视频"]
    }
    # Discord 的「文件」保持原样（不是视频）
    assert read("qq_c2c_relay_filter", ["文字", "文件"], DISCORD_MESSAGE_TYPES) == {
        "filter_message_types": ["文字", "文件"]
    }
    # 显式空列表原样保留（用户选择"不转发"）
    assert read("x_c2c_relay_filter", [], X_MESSAGE_TYPES) == {
        "filter_message_types": []
    }
    # 只存不支持类型 → 回退为未筛选（空 dict）
    assert read("x_c2c_relay_filter", ["语音", "表情"], X_MESSAGE_TYPES) == {}
    # 混合：保留支持项、丢弃不支持项
    assert read("x_c2c_relay_filter", ["文字", "表情"], X_MESSAGE_TYPES) == {
        "filter_message_types": ["文字"]
    }
    # 缺失/类型错误 → 空 dict
    assert _c2c_relay_filter({}, "x_c2c_relay_filter", X_MESSAGE_TYPES) == {}
    assert _c2c_relay_filter(
        {"x_c2c_relay_filter": "nope"}, "x_c2c_relay_filter", X_MESSAGE_TYPES
    ) == {}
    assert _c2c_relay_filter(
        {"x_c2c_relay_filter": {"filter_message_types": "nope"}},
        "x_c2c_relay_filter", X_MESSAGE_TYPES,
    ) == {}

    # ---------- 校验层必须接受老配置 ----------
    # 关键回归：老配置若被判为"结构无效"，config 会内存降级到默认值并丢掉
    # 用户全部配置。这里确认六类型的老值仍被接受。
    legacy = SettingsModel.default()
    for key in MANUAL_RELAY_FILTER_TYPES:
        legacy[key] = {"filter_message_types": LEGACY_SIX}
    assert validate_settings(legacy) is None, validate_settings(legacy)

    # 默认配置本身合法
    assert validate_settings(SettingsModel.default()) is None

    # 真正的垃圾值仍被拒绝
    for bad_value in (["不存在的类型"], [123], [None]):
        broken = SettingsModel.default()
        broken["x_c2c_relay_filter"] = {"filter_message_types": bad_value}
        assert validate_settings(broken), bad_value

    # ---------- 前后端类型表一致性 ----------
    # 前端 app.js 的 FILTER_SUPPORTED_MESSAGE_TYPES 与后端常量必须同步，
    # 否则会出现"面板能选但后端不渲染"或反过来的漂移。
    app_js = (
        Path(__file__).resolve().parents[1] / "panel" / "web" / "app.js"
    ).read_text(encoding="utf-8")
    # relay/channel 等 Discord scope 直接引用六类型常量，取该常量的字面量。
    literal = re.search(r'const FILTER_MESSAGE_TYPES = \[([^\]]*)\];', app_js)
    assert literal, "app.js 缺少 FILTER_MESSAGE_TYPES"
    full_types = tuple(
        item.strip().strip('"').strip("'")
        for item in literal.group(1).split(",")
        if item.strip()
    )
    assert full_types == tuple(DISCORD_MESSAGE_TYPES), full_types

    for scope, expected in (
        ("xrelay", X_MESSAGE_TYPES),
        ("birelay", BILIBILI_MESSAGE_TYPES),
        ("xsource", X_MESSAGE_TYPES),
        ("bsource", BILIBILI_MESSAGE_TYPES),
        ("relay", DISCORD_MESSAGE_TYPES),
    ):
        match = re.search(
            r'^\s*%s:\s*(\[[^\]]*\]|FILTER_MESSAGE_TYPES),' % scope,
            app_js,
            re.MULTILINE,
        )
        assert match, "app.js 缺少 %s 的支持集定义" % scope
        body = match.group(1)
        front_types = full_types if body == "FILTER_MESSAGE_TYPES" else tuple(
            item.strip().strip('"').strip("'")
            for item in body.strip("[]").split(",")
            if item.strip()
        )
        assert front_types == tuple(expected), (scope, front_types, expected)

    # ---------- 消息类型按钮只渲染支持项 ----------
    # 该链路不支持的类型必须"不出现"，而不是渲染出来再置灰禁用。
    # 这里对渲染函数做结构断言，防止回退成"渲染 + disabled"。
    renderer = re.search(
        r'function renderFilterTypes\(\) \{[\s\S]*?\n\}', app_js
    )
    assert renderer, "app.js 缺少 renderFilterTypes"
    body = renderer.group(0)
    assert "filterSupportedTypes()" in body, "renderFilterTypes 未按支持集渲染"
    assert "btn.disabled" not in body, (
        "renderFilterTypes 又出现了禁用按钮：不支持的类型应直接不渲染"
    )
    assert "FILTER_MESSAGE_TYPES.forEach" not in body, (
        "renderFilterTypes 不应遍历全部六类型"
    )

    # ---------- 弹窗字段可见性 ----------
    # 关键回归：手动转发弹窗（relay/xrelay/birelay）只剩标题和按钮，
    # 消息类型区域被 display:none 隐藏。可见性判断必须覆盖所有 scope。
    modal = re.search(r'function prepareFilterModal\([\s\S]*?\n\}', app_js)
    assert modal, "app.js 缺少 prepareFilterModal"
    visible = re.search(
        r'const messageTypeFields = document\.getElementById\("filterMessageTypeFields"\);'
        r'[\s\S]{0,400}?messageTypeFields\.style\.display\s*=\s*([^;]+);',
        modal.group(0),
    )
    assert visible, "prepareFilterModal 未设置 filterMessageTypeFields 的显示状态"
    target = visible.group(1).strip()
    if target != '"block"':
        for scope in ("channel", "relay", "xrelay", "birelay", "xsource", "bsource"):
            assert '"%s"' % scope in target, (
                "prepareFilterModal 的可见范围缺少 %s，该弹窗的消息类型区域会被隐藏"
                % scope
            )
    # 没有受众筛选区时消息类型是内容区第一块，顶部间距必须归零，否则
    # 标题与内容之间会多出一段 14px 空白（手动转发弹窗的留白问题）。
    assert 'messageTypeFields.style.marginTop' in modal.group(0), (
        "prepareFilterModal 未处理消息类型区域的顶部间距"
    )

    print("PASS - 按链路裁剪消息类型筛选（支持集、存量归一化、校验兼容、前后端一致、只渲染支持项、弹窗可见性）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
