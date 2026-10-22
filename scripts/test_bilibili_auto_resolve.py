"""验证 Bilibili 自动解析：触发范围、链接提取、静默降级与去重。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import bilibili_auto_resolve as auto


class FakeSegment:
    def __init__(self, text="", segment_type="text", is_bot=None, user_id=None):
        self.type = segment_type
        self.data = {"text": text}
        if is_bot is not None:
            self.data["is_bot"] = is_bot
        if user_id is not None:
            self.data["user_id"] = user_id


class FakeGroupMessage:
    def __init__(self, plaintext="", segments=None, author_bot=False):
        self.plaintext = plaintext
        self.segments = list(segments or [])
        self.author = type("Author", (), {"bot": author_bot, "member_openid": "member"})()
        self.group_openid = "group-openid"

    def get_plaintext(self):
        return self.plaintext

    def get_message(self):
        return self.segments

    def is_tome(self):
        return False


class FakeAtGroupMessage(FakeGroupMessage):
    def __init__(self, plaintext="", segments=None, author_bot=False):
        segments = list(segments or [])
        if not any(segment.type == "mention_user" for segment in segments):
            segments.insert(0, FakeSegment("", "mention_user", is_bot=True))
        super().__init__(plaintext, segments, author_bot)

    def is_tome(self):
        return True


class FakePrivateMessage:
    def __init__(self, plaintext):
        self.plaintext = plaintext
        self.author = type("Author", (), {"id": "member"})()

    def get_plaintext(self):
        return self.plaintext


class FakeFileSegment:
    def __init__(self, data, name):
        self.type = "video"
        self.data = {"file_data": data, "file_name": name}


class FakeTextSegment:
    def __init__(self, text):
        self.type = "text"
        self.data = {"text": text}


class FakeSegmentFactory:
    file_video = staticmethod(FakeFileSegment)
    text = staticmethod(FakeTextSegment)


class FakeQQBot:
    self_id = "bot-id"

    def __init__(self):
        self.sent = []

    async def send(self, event, message):
        self.sent.append((event, list(message)))


BV_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
SHORT_URL = "https://b23.tv/abc123"
VIDEO_REF = {"bvid": "BV1xx411c7mD", "avid": "", "page": 1, "short_url": ""}


def _post(bvid="BV1xx411c7mD", page=1):
    return {
        "id": bvid,
        "bvid": bvid,
        "avid": "",
        "cid": 123,
        "page": page,
        "link": "https://www.bilibili.com/video/%s" % bvid,
        "title": "示例视频",
        "author": "UP 主",
        "post_type": "视频",
        "media": [],
    }


def _reset_state():
    auto._sent_at.clear()
    auto._inflight.clear()


class FakeResponse:
    def __init__(self, url):
        self.url = url

    def raise_for_status(self):
        return None


async def run(url_message, bot=None, event_factory=FakeGroupMessage):
    """按默认放行配置执行一次处理器，返回 bot。"""
    bot = bot or FakeQQBot()
    with (
        mock.patch.object(auto, "QQBot", FakeQQBot),
        mock.patch.object(auto, "GroupMessageCreateEvent", event_factory),
        mock.patch.object(auto, "QQMessageSegment", FakeSegmentFactory),
        mock.patch.object(auto, "Message", list),
        mock.patch.object(auto, "get_bilibili_auto_resolve_enabled", return_value=True),
        mock.patch.object(
            auto, "get_active_group_routing_snapshot", return_value=(["group-openid"], {"group-openid": ""})
        ),
    ):
        await auto.handle_bilibili_auto_resolve(bot, FakeGroupMessage(url_message))
    return bot


def _div_close_index(markup, open_index):
    """从 open_index（<div 起始）开始返回匹配 </div> 的位置。"""
    depth = 0
    position = open_index
    while True:
        next_open = markup.find("<div", position)
        next_close = markup.find("</div>", position)
        if next_close < 0:
            raise AssertionError("div 没有闭合")
        if 0 <= next_open < next_close:
            depth += 1
            position = next_open + 4
            continue
        depth -= 1
        position = next_close + 6
        if depth == 0:
            return next_close


async def check_panel_markup():
    """面板：卡片位于 Bilibili 页转发组工作区之外，开关有渲染与保存分支。"""
    root = Path(auto.__file__).resolve().parents[1]
    html = (root / "panel" / "web" / "index.html").read_text(encoding="utf-8")
    app_js = (root / "panel" / "web" / "app.js").read_text(encoding="utf-8")

    page = html.split('<div id="pageBilibili"', 1)[1].split('<div id="pageQQ"', 1)[0]
    card = page.index('id="bilibiliAutoResolveCard"')
    fg_body_end = _div_close_index(page, page.rfind("<div", 0, page.index('id="bilibiliFgBody"')))
    assert card > fg_body_end, "新卡片必须在 bilibiliFgBody 工作区之外"
    assert 'id="btnBilibiliAutoResolveToggle"' in page
    assert 'id="hint-bilibili-auto-resolve"' in page
    hint_text = page.split('id="hint-bilibili-auto-resolve"', 1)[1].split("</div>", 1)[0]
    assert "接收所有消息" in hint_text
    assert "拿不到视频时不回复" in hint_text

    assert "renderBilibiliAutoResolveToggle(!!(settings.bilibili_auto_resolve" in app_js
    render_body = app_js.split("function renderBilibiliPage()", 1)[1].split("\n}", 1)[0]
    assert "renderBilibiliAutoResolveToggle(" in render_body, "Bilibili 页需渲染开关"
    click_body = app_js.split('btn.id === "btnBilibiliAutoResolveToggle"', 1)[1].split("} else if", 1)[0]
    assert "settings.bilibili_auto_resolve.enabled = btn.dataset.on !== \"1\"" in click_body
    assert "await saveAndReload();" in click_body


async def main():
    # 链接提取：BV、av、分 P、短链，多链接只取第一个。
    assert auto.extract_first_video_url("看看 " + BV_URL) == BV_URL
    assert auto.extract_first_video_url("https://www.bilibili.com/video/av113386784") == \
        "https://www.bilibili.com/video/av113386784"
    assert auto.extract_first_video_url(BV_URL + "?p=3") == BV_URL + "?p=3"
    assert auto.extract_first_video_url(SHORT_URL) == SHORT_URL
    assert auto.extract_first_video_url("先 " + BV_URL + " 再 https://example.com/x") == BV_URL
    assert auto.extract_first_video_url(BV_URL + " " + SHORT_URL) == BV_URL
    assert auto.extract_first_video_url("没有链接") is None
    assert auto.extract_first_video_url("https://example.com/watch?v=1") is None

    # 直播、番剧、专栏/动态：解析阶段即静默，不下载也不回复。
    for silent_url in (
        "https://live.bilibili.com/12345",
        "https://www.bilibili.com/bangumi/play/ss33471",
        "https://www.bilibili.com/opus/100200300",
        "https://t.bilibili.com/100200300",
    ):
        _reset_state()
        fetch = mock.AsyncMock()
        with mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch):
            bot = await run("分享 " + silent_url)
        assert bot.sent == [], silent_url
        fetch.assert_not_awaited()

    # 下载成功：只发视频文件，文件名带投稿编号。
    _reset_state()
    fetch_post = mock.AsyncMock(return_value=(_post(), "UP 主"))
    fetch_video = mock.AsyncMock(return_value=("https://video.example/a.mp4", b"video-bytes"))
    with (
        mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch_post),
        mock.patch.object(auto.bilibili, "_fetch_video_url", fetch_video),
    ):
        bot = await run(BV_URL)
    assert len(bot.sent) == 1, bot.sent
    event, segments = bot.sent[0]
    assert isinstance(event, FakeGroupMessage)
    assert [segment.type for segment in segments] == ["video"]
    assert segments[0].data["file_name"] == "bilibili-BV1xx411c7mD.mp4"
    assert segments[0].data["file_data"] == b"video-bytes"

    # 失败 / 超限 / 多段：静默，不回复原链接。
    for label, video_result in (
        ("下载失败", (BV_URL + "?p=1", None)),
        ("超过大小上限", ("https://video.example/big.mp4", None)),
        ("视频分段", ("https://video.example/part1.mp4", None)),
    ):
        _reset_state()
        with (
            mock.patch.object(auto.bilibili, "_fetch_video_manual_post", mock.AsyncMock(return_value=(_post(), "UP 主"))),
            mock.patch.object(auto.bilibili, "_fetch_video_url", mock.AsyncMock(return_value=video_result)),
        ):
            bot = await run(BV_URL)
        assert bot.sent == [], label

    # view 接口报错：同样静默，不回复原消息里的链接。
    _reset_state()
    with (
        mock.patch.object(
            auto.bilibili, "_fetch_video_manual_post", mock.AsyncMock(side_effect=RuntimeError("投稿审核中"))
        ),
    ):
        bot = await run("看这个 " + BV_URL)
    assert bot.sent == []

    # 短链：跟随重定向后按真实视频处理。
    _reset_state()
    resolved_video = "https://www.bilibili.com/video/BV1yy411c7zP?p=2"
    fetch_post = mock.AsyncMock(return_value=(_post("BV1yy411c7zP", page=2), "UP 主"))
    with (
        mock.patch.object(auto.bilibili, "_bilibili_get", mock.AsyncMock(return_value=FakeResponse(resolved_video))),
        mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch_post),
        mock.patch.object(auto.bilibili, "_fetch_video_url", mock.AsyncMock(return_value=("u", b"bytes"))),
    ):
        bot = await run(SHORT_URL)
    fetch_post.assert_awaited_once()
    assert fetch_post.await_args.args[0]["bvid"] == "BV1yy411c7zP"
    assert fetch_post.await_args.args[0]["page"] == 2
    assert bot.sent[0][1][0].data["file_name"] == "bilibili-BV1yy411c7zP.mp4"

    # 短链跳到专栏：静默。
    _reset_state()
    fetch_post = mock.AsyncMock()
    with (
        mock.patch.object(auto.bilibili, "_bilibili_get", mock.AsyncMock(return_value=FakeResponse("https://www.bilibili.com/opus/100200300"))),
        mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch_post),
    ):
        bot = await run(SHORT_URL)
    assert bot.sent == []
    fetch_post.assert_not_awaited()

    # 同一群同一视频 10 分钟内只发送一次；不同群互不影响。
    _reset_state()
    download = mock.AsyncMock(return_value=("u", b"bytes"))
    with (
        mock.patch.object(auto.bilibili, "_fetch_video_manual_post", mock.AsyncMock(side_effect=lambda ref: (_post(ref["bvid"], ref.get("page", 1)), "UP 主"))),
        mock.patch.object(auto.bilibili, "_fetch_video_url", download),
    ):
        first = await run(BV_URL + "?p=1")
        second = await run(BV_URL + "?p=1")
        other_page = await run(BV_URL + "?p=2")
    assert len(first.sent) == 1
    assert second.sent == [], "重复链接应被去重"
    assert len(other_page.sent) == 1, "不同分 P 视为不同视频"

    # 在途去重：并发发送同一链接只下载一次。
    _reset_state()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_fetch(post):
        started.set()
        await release.wait()
        return "u", b"bytes"

    with (
        mock.patch.object(auto.bilibili, "_fetch_video_manual_post", mock.AsyncMock(return_value=(_post(), "UP 主"))),
        mock.patch.object(auto.bilibili, "_fetch_video_url", mock.AsyncMock(side_effect=blocking_fetch)),
    ):
        bot = FakeQQBot()
        task = asyncio.create_task(run(BV_URL, bot))
        await asyncio.wait_for(started.wait(), 5)
        busy = await run(BV_URL, FakeQQBot())
        assert busy.sent == [], "在途视频不应重复下载"
        release.set()
        await task
    assert len(bot.sent) == 1

    # 机器人消息、/ 命令、@ 机器人、私聊、开关关闭、群未登记/禁用/属于其他 Bot 都不触发。
    fetch_post = mock.AsyncMock(return_value=(_post(), "UP 主"))
    gate_cases = [
        ("开关关闭", False, ["group-openid"], {"group-openid": ""}),
        ("群未登记或未启用", True, [], {}),
        ("属于其他 Bot", True, ["group-openid"], {"group-openid": "other-bot"}),
    ]
    for label, enabled, groups, binding in gate_cases:
        _reset_state()
        bot = FakeQQBot()
        with (
            mock.patch.object(auto, "QQBot", FakeQQBot),
            mock.patch.object(auto, "GroupMessageCreateEvent", FakeGroupMessage),
            mock.patch.object(auto, "QQMessageSegment", FakeSegmentFactory),
            mock.patch.object(auto, "Message", list),
            mock.patch.object(auto, "get_bilibili_auto_resolve_enabled", return_value=enabled),
            mock.patch.object(auto, "get_active_group_routing_snapshot", return_value=(groups, binding)),
            mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch_post),
        ):
            await auto.handle_bilibili_auto_resolve(bot, FakeGroupMessage(BV_URL))
        assert bot.sent == [], label
        fetch_post.assert_not_awaited()

    ignored = [
        FakeGroupMessage(BV_URL, author_bot=True),
        FakeGroupMessage("/转发 列表 " + BV_URL),
        FakeAtGroupMessage(BV_URL),
    ]
    for event in ignored:
        _reset_state()
        bot = FakeQQBot()
        with (
            mock.patch.object(auto, "QQBot", FakeQQBot),
            mock.patch.object(auto, "GroupMessageCreateEvent", FakeGroupMessage),
            mock.patch.object(auto, "QQMessageSegment", FakeSegmentFactory),
            mock.patch.object(auto, "Message", list),
            mock.patch.object(auto, "get_bilibili_auto_resolve_enabled", return_value=True),
            mock.patch.object(auto, "get_active_group_routing_snapshot", return_value=(["group-openid"], {"group-openid": ""})),
            mock.patch.object(auto.bilibili, "_fetch_video_manual_post", fetch_post),
        ):
            await auto.handle_bilibili_auto_resolve(bot, event)
        assert bot.sent == [], event.get_plaintext()
        fetch_post.assert_not_awaited()

    # 私聊完全不触发。
    _reset_state()
    bot = FakeQQBot()
    with mock.patch.object(auto, "QQBot", FakeQQBot):
        await auto.handle_bilibili_auto_resolve(bot, FakePrivateMessage(BV_URL))
    assert bot.sent == []

    # 配置读取：损坏/缺失按关闭处理。
    assert auto._qq_bot_id(FakeQQBot()) == "bot-id"
    from plugins.config import _bilibili_auto_resolve_enabled_from

    assert _bilibili_auto_resolve_enabled_from({}) is False
    assert _bilibili_auto_resolve_enabled_from({"bilibili_auto_resolve": "坏配置"}) is False
    assert _bilibili_auto_resolve_enabled_from({"bilibili_auto_resolve": {}}) is False
    assert _bilibili_auto_resolve_enabled_from({"bilibili_auto_resolve": {"enabled": "yes"}}) is False
    assert _bilibili_auto_resolve_enabled_from({"bilibili_auto_resolve": {"enabled": True}}) is True

    # 设置校验：默认值存在，布尔类型错误判为损坏。
    from plugins.settings_model import SettingsModel, validate_settings

    assert SettingsModel.default()["bilibili_auto_resolve"] == {"enabled": False}
    model = SettingsModel.from_data({})
    assert model["bilibili_auto_resolve"] == {"enabled": False}
    assert validate_settings(model) is None

    bad = dict(model)
    bad["bilibili_auto_resolve"] = {"enabled": "yes"}
    assert validate_settings(bad) == "bilibili_auto_resolve.enabled 必须是布尔值"
    bad["bilibili_auto_resolve"] = "坏配置"
    assert validate_settings(bad) == "bilibili_auto_resolve 必须是对象"
    await check_panel_markup()

    print("PASS - Bilibili 自动解析：触发范围、静默降级与去重")


if __name__ == "__main__":
    asyncio.run(main())
