"""Bilibili 手动转发链接识别与 opus（专栏/动态）解析的纯逻辑测试。

`parse_opus_detail` 的 fixture 取自 x/polymer/web-dynamic/v1/detail 的真实
响应结构（2026 年实测，登录态下确认）：

- opus/855948185687818258 → DYNAMIC_TYPE_DRAW（图集动态，封面为 http://）
- opus/684597321698115618 → DYNAMIC_TYPE_ARTICLE（专栏，jump_url 为 // 开头）

这两个用例覆盖了协议补全与两种正文位置的关键分支。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.bilibili_parser import parse_manual_post_url, parse_opus_detail


# --- 真实动态响应（截取关键字段） ---
DYNAMIC_ITEM = {
    "id_str": "855948185687818258",
    "type": "DYNAMIC_TYPE_DRAW",
    "modules": {
        "module_author": {
            "mid": 144900177,
            "name": "哔哩哔哩专栏",
            "pub_time": "2023年10月24日 14:36",
        },
        "module_dynamic": {
            "desc": None,
            "major": {
                "type": "MAJOR_TYPE_DRAW",
                "draw": {
                    "id": 1,
                    "items": [
                        {
                            "height": 5723,
                            "width": 1112,
                            "src": "http://i0.hdslb.com/bfs/new_dyn/9b3c13aea191d0242a06bd39d8393ae9144900177.png",
                        },
                    ],
                },
            },
        },
    },
}

# --- 真实专栏响应（截取关键字段） ---
ARTICLE_ITEM = {
    "id_str": "684597321698115618",
    "type": "DYNAMIC_TYPE_ARTICLE",
    "modules": {
        "module_author": {
            "mid": 85807564,
            "name": "打鱼人和他的灵魂",
            "pub_time": "2022年07月19日 20:27",
        },
        "module_dynamic": {
            "desc": None,
            "major": {
                "type": "MAJOR_TYPE_ARTICLE",
                "article": {
                    "id": 17651033,
                    "title": "B站专栏添加、设置头图的方法，如何调出确认键",
                    "desc": "最近有个好友询问我B站的网页端专栏怎么加头图。",
                    "covers": [
                        "https://i0.hdslb.com/bfs/article/banner/e37ddde8c135a26aca57aef695f06fd86e96ff71.png"
                    ],
                    "jump_url": "//www.bilibili.com/read/cv17651033/",
                    "label": "2284阅读",
                },
            },
        },
    },
}

# --- 纯文字动态（desc.text 是动态正文所在位置） ---
TEXT_DYNAMIC_ITEM = {
    "id_str": "999999999999999999",
    "type": "DYNAMIC_TYPE_WORD",
    "modules": {
        "module_author": {"name": "某UP主", "pub_time": "2026年01月01日 00:00"},
        "module_dynamic": {"desc": {"text": "这是一条纯文字动态的正文。"}, "major": None},
    },
}

# --- 转发动态：外层只有转发语，真实内容在 orig（结构取自真实 feed 响应） ---
FORWARD_DYNAMIC_ITEM = {
    "id_str": "1259633596379955204",
    "type": "DYNAMIC_TYPE_FORWARD",
    "modules": {
        "module_author": {"name": "转发者", "pub_time": "2026年01月02日 12:00"},
        "module_dynamic": {"desc": {"text": "分享视频"}, "major": None},
    },
    "orig": {
        "id_str": "1259000000000000000",
        "type": "DYNAMIC_TYPE_AV",
        "modules": {
            "module_author": {"name": "蜜雪冰城雪王"},
            "module_dynamic": {
                "desc": None,
                "major": {
                    "type": "MAJOR_TYPE_ARCHIVE",
                    "archive": {"title": "被转发的视频标题", "bvid": "BV1xx411c7mD"},
                },
            },
        },
    },
}


def main():
    # ---------- 链接识别 ----------
    opus = parse_manual_post_url(
        "https://www.bilibili.com/opus/684597321698115618?from=search&spm_id_from=333.337.0.0"
    )
    assert opus == {"kind": "opus", "opus_id": "684597321698115618"}, opus

    # 历史 /dynamic/ 路径同样识别为 opus
    history = parse_manual_post_url("https://www.bilibili.com/dynamic/855948185687818258")
    assert history == {"kind": "opus", "opus_id": "855948185687818258"}, history

    # 视频（含 ?p= 分 P）仍走视频分支
    video = parse_manual_post_url("https://www.bilibili.com/video/BV1AA411C7mD?p=2")
    assert video["kind"] == "视频"
    assert video["video_ref"]["bvid"] == "BV1AA411C7mD"
    assert video["video_ref"]["page"] == 2

    # 短链：kind 先按视频返回，由调用方跟随重定向后再判定真实类型
    short = parse_manual_post_url("https://b23.tv/abc123")
    assert short["kind"] == "视频"
    assert short["video_ref"]["short_url"] == "https://b23.tv/abc123"

    # 站外与非投稿页返回 None
    assert parse_manual_post_url("https://example.com/opus/1") is None
    assert parse_manual_post_url("https://www.bilibili.com/") is None
    assert parse_manual_post_url("") is None

    # ---------- opus 详情解析 ----------
    dyn = parse_opus_detail(DYNAMIC_ITEM, opus_id="855948185687818258")
    assert dyn["post_type"] == "动态", dyn["post_type"]
    assert dyn["link"] == "https://www.bilibili.com/opus/855948185687818258"
    assert dyn["author"] == "哔哩哔哩专栏"
    assert dyn["title"] == "" and dyn["description"] == ""
    # 动态封面原始地址是 http://，必须升级为 https
    assert dyn["media"] == [{
        "kind": "图片",
        "url": "https://i0.hdslb.com/bfs/new_dyn/9b3c13aea191d0242a06bd39d8393ae9144900177.png",
    }], dyn["media"]

    art = parse_opus_detail(ARTICLE_ITEM, opus_id="684597321698115618")
    assert art["post_type"] == "专栏", art["post_type"]
    # jump_url 是 // 开头，必须补成 https://
    assert art["link"] == "https://www.bilibili.com/read/cv17651033/", art["link"]
    assert art["title"] == "B站专栏添加、设置头图的方法，如何调出确认键"
    assert art["description"].startswith("最近有个好友询问")
    assert art["author"] == "打鱼人和他的灵魂"
    assert art["media"][0]["url"].startswith("https://i0.hdslb.com/bfs/article/banner/")

    # 纯文字动态：正文在 desc.text，无媒体
    word = parse_opus_detail(TEXT_DYNAMIC_ITEM, opus_id="999999999999999999")
    assert word["post_type"] == "动态"
    assert word["title"] == "这是一条纯文字动态的正文。"
    assert word["media"] == []

    # 转发动态：外层「分享视频」是转发语，真实标题取自 orig，两者合成一行
    fwd = parse_opus_detail(FORWARD_DYNAMIC_ITEM, opus_id="1259633596379955204")
    assert fwd["post_type"] == "动态"
    assert fwd["title"] == "分享视频：被转发的视频标题", fwd["title"]
    assert fwd["link"] == "https://www.bilibili.com/opus/1259633596379955204"

    # 转发动态且 orig 也取不到标题时，至少保留转发语而不是整条丢弃
    bare = dict(FORWARD_DYNAMIC_ITEM)
    bare["orig"] = {
        "id_str": "1", "type": "DYNAMIC_TYPE_AV",
        "modules": {"module_author": {}, "module_dynamic": {"major": None}},
    }
    bare_post = parse_opus_detail(bare, opus_id="1259633596379955204")
    assert bare_post["title"] == "分享视频", bare_post["title"]

    # 无标题、无正文、无封面 → 不转发
    assert parse_opus_detail({}, opus_id="1") is None
    assert parse_opus_detail(None) is None
    assert parse_opus_detail({"id_str": "1", "type": "DYNAMIC_TYPE_WORD"}, opus_id="1") is None

    print("PASS - Bilibili 手动转发链接识别与专栏/动态解析")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
