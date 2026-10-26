"""YouTube 频道 RSS 解析、来源归一化与转发文本测试。"""

from plugins.youtube_parser import (
    channel_feed_url,
    channel_page_url,
    format_forward_text,
    normalize_channel_value,
    parse_channel_page,
    parse_feed,
    youtube_source_id,
)


CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
OTHER_CHANNEL_ID = "UC7_YxT-KID8kRbqZo7MyscQ"

FEED = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Test Channel</title>
  <entry>
    <id>yt:video:NEWVIDEO01</id>
    <yt:videoId>NEWVIDEO01</yt:videoId>
    <yt:channelId>{channel}</yt:channelId>
    <title>更新的视频</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=NEWVIDEO01"/>
    <published>2026-01-21T10:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="https://i.ytimg.com/vi/NEWVIDEO01/hqdefault.jpg"/>
      <media:description>第一行
        第二行</media:description>
    </media:group>
    <author><name>测试频道</name></author>
  </entry>
  <entry>
    <id>yt:video:OLDVIDEO01</id>
    <yt:videoId>OLDVIDEO01</yt:videoId>
    <yt:channelId>{channel}</yt:channelId>
    <title>较早的视频</title>
    <published>2026-01-20T10:00:00+00:00</published>
    <author><name>测试频道</name></author>
  </entry>
  <entry>
    <id>yt:video:OLDVIDEO01</id>
    <yt:videoId>OLDVIDEO01</yt:videoId>
    <yt:channelId>{channel}</yt:channelId>
    <title>重复条目</title>
    <published>2026-01-19T10:00:00+00:00</published>
    <author><name>测试频道</name></author>
  </entry>
  <entry>
    <id>yt:video:NOTITLE01</id>
    <yt:videoId>NOTITLE01</yt:videoId>
    <published>2026-01-22T10:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:BADDATE01</id>
    <yt:videoId>BADDATE01</yt:videoId>
    <title>日期无效</title>
    <published>not-a-date</published>
  </entry>
</feed>'''.format(channel=CHANNEL_ID).encode("utf-8")


def main():
    assert normalize_channel_value(CHANNEL_ID) == CHANNEL_ID
    assert normalize_channel_value("  " + CHANNEL_ID + "  ") == CHANNEL_ID
    assert normalize_channel_value("UCshort") == "@UCshort"
    assert normalize_channel_value("@LinusTechTips") == "@LinusTechTips"
    assert normalize_channel_value("LinusTechTips") == "@LinusTechTips"
    assert normalize_channel_value("bad handle!") == ""
    assert normalize_channel_value(
        "https://www.youtube.com/channel/" + CHANNEL_ID
    ) == CHANNEL_ID
    assert normalize_channel_value(
        "https://m.youtube.com/channel/" + CHANNEL_ID + "/videos?view=0"
    ) == CHANNEL_ID
    assert normalize_channel_value(
        "https://www.youtube.com/@LinusTechTips/videos"
    ) == "@LinusTechTips"
    assert normalize_channel_value(
        "https://www.youtube.com/c/SomeCreator"
    ) == "@SomeCreator"
    assert normalize_channel_value(
        "https://www.youtube.com/user/SomeUser"
    ) == "@SomeUser"
    assert normalize_channel_value(
        "https://example.com/channel/" + CHANNEL_ID
    ) == ""
    assert normalize_channel_value("https://www.youtube.com/feed/trending") == ""

    assert youtube_source_id(CHANNEL_ID) == "channel:" + CHANNEL_ID
    assert youtube_source_id("@LinusTechTips") == "channel:@LinusTechTips"
    assert youtube_source_id("") == ""

    assert channel_feed_url(CHANNEL_ID) == (
        "https://www.youtube.com/feeds/videos.xml?channel_id=" + CHANNEL_ID
    )
    try:
        channel_feed_url("@LinusTechTips")
    except ValueError:
        pass
    else:
        raise AssertionError("非频道 ID 不应生成 RSS 地址")

    assert channel_page_url("@LinusTechTips") == (
        "https://www.youtube.com/@LinusTechTips"
    )
    assert channel_page_url(CHANNEL_ID) == (
        "https://www.youtube.com/channel/" + CHANNEL_ID
    )

    page = parse_channel_page(
        '<html><head><meta property="og:title" content="MKBHD &amp; Co">'
        '<script>var data = {"externalId":"%s"};</script></head></html>'
        % CHANNEL_ID
    )
    assert page == {"channel_id": CHANNEL_ID, "title": "MKBHD & Co"}
    identifier_page = parse_channel_page(
        '<meta itemprop="identifier" content="%s"><title>频道 - YouTube</title>'
        % OTHER_CHANNEL_ID
    )
    assert identifier_page == {
        "channel_id": OTHER_CHANNEL_ID,
        "title": "频道",
    }
    assert parse_channel_page("<html><title>无 ID</title></html>") == {
        "channel_id": "",
        "title": "无 ID",
    }

    posts = parse_feed(FEED)
    assert [post["id"] for post in posts] == ["OLDVIDEO01", "NEWVIDEO01"]
    assert posts[0]["title"] == "较早的视频"
    assert posts[0]["link"] == "https://www.youtube.com/watch?v=OLDVIDEO01"
    assert posts[0]["thumbnail"] == ""
    assert posts[0]["description"] == ""
    assert posts[1]["title"] == "更新的视频"
    assert posts[1]["link"] == "https://www.youtube.com/watch?v=NEWVIDEO01"
    assert posts[1]["thumbnail"] == (
        "https://i.ytimg.com/vi/NEWVIDEO01/hqdefault.jpg"
    )
    assert posts[1]["description"] == "第一行 第二行"
    assert posts[1]["author"] == "测试频道"
    assert posts[1]["channel_id"] == CHANNEL_ID
    assert posts[1]["post_type"] == "视频"
    assert posts[0]["cursor_id"].isdigit()
    assert int(posts[0]["cursor_id"]) < int(posts[1]["cursor_id"])
    # 同一 Feed 重复解析得到同一游标，重启后不会把旧视频当新视频。
    assert [post["cursor_id"] for post in parse_feed(FEED)] == [
        post["cursor_id"] for post in posts
    ]
    try:
        parse_feed(b"<rss><channel></channel></rss>")
    except ValueError:
        pass
    else:
        raise AssertionError("非 Atom Feed 根节点应被拒绝")

    assert format_forward_text(posts[1], {"name": "备用名称"}) == (
        "自动转发来自 YouTube 中[测试频道]的视频：\n"
        "更新的视频\n"
        "https://www.youtube.com/watch?v=NEWVIDEO01"
    )
    assert format_forward_text(
        {"title": "无作者", "link": "https://youtu.be/x"},
        {"name": "来源名称"},
    ) == "自动转发来自 YouTube 中[来源名称]的视频：\n无作者\nhttps://youtu.be/x"
    assert format_forward_text({}, {}) == ""
    print("PASS - YouTube 频道解析、来源归一化和转发文本")


if __name__ == "__main__":
    main()
