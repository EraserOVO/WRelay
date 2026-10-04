"""Reject well-formed non-feed documents while accepting empty RSS feeds."""
from plugins.bilibili_parser import parse_feed as parse_bilibili_feed
from plugins.x_rss_parser import parse_feed as parse_x_feed


EMPTY_RSS = b"<rss version='2.0'><channel><title>Empty</title></channel></rss>"
HTML_ERROR = b"<html><body><h1>Service unavailable</h1></body></html>"


def main():
    assert parse_x_feed(EMPTY_RSS) == []
    assert parse_bilibili_feed(EMPTY_RSS) == []

    for parser in (parse_x_feed, parse_bilibili_feed):
        try:
            parser(HTML_ERROR)
        except ValueError:
            pass
        else:
            raise AssertionError("{} accepted a non-feed HTML document".format(parser))

    print("PASS - X/Bilibili 拒绝非 RSS 文档并保留合法空 RSS")


if __name__ == "__main__":
    main()
