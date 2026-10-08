"""Bilibili RSS 解析与来源筛选的纯逻辑测试。"""

from plugins.bilibili_parser import format_post_text, make_cursor_id, parse_feed
from plugins.filter import check_bilibili_video_filter


RSS = '''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <guid>https://www.bilibili.com/video/BV1AA411C7mD</guid>
    <link>https://www.bilibili.com/video/BV1AA411C7mD</link>
    <title>第一个视频</title>
    <description><![CDATA[简介<br>第二行<img src="https://img.example/cover.jpg"/>]]></description>
    <author>测试 UP</author>
    <pubDate>Tue, 20 Jan 2026 10:00:00 +0800</pubDate>
    <category>游戏</category><category>攻略</category>
  </item>
  <item>
    <guid>https://www.bilibili.com/video/BV1BB411C7mD</guid>
    <link>https://www.bilibili.com/video/BV1BB411C7mD</link>
    <title>第二个视频</title>
    <description>关键词内容</description>
    <pubDate>Tue, 20 Jan 2026 11:00:00 +0800</pubDate>
    <category>科技</category>
  </item>
</channel></rss>'''.encode("utf-8")


def main():
    posts = parse_feed(RSS)
    assert len(posts) == 2
    assert posts[0]["title"] == "第一个视频"
    # BV 号大小写敏感：必须原样保留 fixture 中的小写 m
    assert posts[0]["bvid"] == "BV1AA411C7mD"
    assert posts[0]["author"] == "测试 UP"
    assert posts[0]["tags"] == ["游戏", "攻略"]
    assert posts[0]["media"] == [{"kind": "图片", "url": "https://img.example/cover.jpg"}]
    tag_source = {"kind": "tag", "value": "空洞骑士", "name": "空洞骑士"}
    assert format_post_text(posts[0], tag_source) == (
        "自动转发来自 Bilibili 中[测试 UP]的投稿：\n"
        "第一个视频\n"
        "https://www.bilibili.com/video/BV1AA411C7mD"
    )
    assert format_post_text(posts[1], tag_source).startswith("自动转发来自 Bilibili 中[空洞骑士]的投稿：\n")
    assert "作者：" not in format_post_text(posts[0], tag_source)
    assert "#空洞骑士" not in format_post_text(posts[0], tag_source)
    up_text = format_post_text(posts[0], {"kind": "up", "value": "123"})
    assert "标签：" not in up_text
    assert "第一个视频 #" not in up_text
    assert int(posts[0]["cursor_id"]) < int(posts[1]["cursor_id"])
    assert make_cursor_id(posts[0]["id"], posts[0]["pub_date"]) == posts[0]["cursor_id"]

    assert check_bilibili_video_filter({"filter_keywords": ["游戏"]}, posts[0])
    assert not check_bilibili_video_filter({"filter_keywords": ["科技"]}, posts[0])
    assert check_bilibili_video_filter({"filter_keywords": ["关键词"]}, posts[1])
    assert not check_bilibili_video_filter({"filter_keywords": ["不存在"]}, posts[1])
    assert check_bilibili_video_filter({"filter_tags": ["科技"]}, posts[0])
    assert check_bilibili_video_filter(
        {"filter_bilibili_post_types": ["视频"]}, posts[0]
    )
    assert not check_bilibili_video_filter(
        {"filter_bilibili_post_types": ["专栏"]}, posts[0]
    )
    print("PASS - Bilibili RSS 解析、游标和筛选")


if __name__ == "__main__":
    main()
