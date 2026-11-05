"""验证 Bilibili 视频只请求 360P 单段，失败时不返回可降级链接。"""
import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nonebot

nonebot.init()

from plugins import bilibili


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _payload(quality=16, segments=None, code=0):
    return {
        "code": code,
        "message": "" if code == 0 else "boom",
        "data": {
            "quality": quality,
            "durl": (
                segments
                if segments is not None
                else [{"url": "https://cdn.example/360.mp4", "size": 512}]
            ),
        },
    }


def _post(cid=123):
    return {
        "id": "BV1test",
        "bvid": "BV1test",
        "avid": "",
        "cid": cid,
        "page": 1,
        "link": "https://www.bilibili.com/video/BV1test",
        "post_type": "视频",
        "media": [{"kind": "视频", "url": "https://direct.example/full.mp4"}],
    }


async def _call(post, payload, *, fetch_result=b"video", limit=1024, view_error=None):
    get = mock.AsyncMock(return_value=FakeResponse(payload))
    fetch = mock.AsyncMock(return_value=fetch_result)
    if view_error is None:
        view = mock.AsyncMock(return_value={})
    else:
        view = mock.AsyncMock(side_effect=view_error)

    async def sign(params):
        return dict(params)

    with (
        mock.patch.object(bilibili, "_signed_wbi_params", new=sign),
        mock.patch.object(bilibili, "_bilibili_get", new=get),
        mock.patch.object(bilibili, "_fetch_view_data", new=view),
        mock.patch.object(bilibili, "fetch_bytes", new=fetch),
        mock.patch.object(bilibili, "get_max_download_bytes", return_value=limit),
    ):
        result = await bilibili._fetch_video_url(post)
    return result, get, fetch


async def verify_only_360p():
    result, get, fetch = await _call(_post(), _payload())
    assert result == ("https://cdn.example/360.mp4", b"video"), result
    assert get.await_count == 1
    assert get.await_args.kwargs["params"]["qn"] == 16
    assert fetch.await_args.args[0] == "https://cdn.example/360.mp4"
    assert "direct.example" not in str(fetch.await_args)
    print("PASS - 只请求并下载 360P，忽略 RSS 视频直链")


async def verify_rejects_higher_quality():
    for quality in (32, 64, None):
        result, get, fetch = await _call(_post(), _payload(quality=quality))
        assert result == (None, None), (quality, result)
        assert get.await_count == 1
        fetch.assert_not_awaited()
    print("PASS - 接口未返回 360P 时拒绝，不回退其他清晰度")


async def verify_rejects_multi_segment():
    segments = [
        {"url": "https://cdn.example/part1.mp4", "size": 256},
        {"url": "https://cdn.example/part2.mp4", "size": 256},
    ]
    result, _get, fetch = await _call(_post(), _payload(segments=segments))
    assert result == (None, None)
    fetch.assert_not_awaited()
    print("PASS - 多段视频不发送")


async def verify_size_limit_without_download():
    segments = [{"url": "https://cdn.example/360.mp4", "size": 2048}]
    result, _get, fetch = await _call(_post(), _payload(segments=segments), limit=1024)
    assert result == (None, None)
    fetch.assert_not_awaited()
    print("PASS - 超过大小上限时不下载并返回空")


async def verify_download_failure():
    result, _get, fetch = await _call(_post(), _payload(), fetch_result=None)
    assert result == (None, None)
    fetch.assert_awaited_once()
    print("PASS - 下载失败时返回空")


async def verify_api_error():
    result, _get, fetch = await _call(_post(), _payload(code=-400))
    assert result == (None, None)
    fetch.assert_not_awaited()
    print("PASS - 播放接口报错时返回空")


async def verify_missing_cid():
    result, get, fetch = await _call(
        _post(cid=None),
        _payload(),
        view_error=RuntimeError("view failed"),
    )
    assert result == (None, None)
    get.assert_not_awaited()
    fetch.assert_not_awaited()
    print("PASS - cid 获取失败时不请求播放接口")


async def verify_no_link_fallback():
    post = {
        "id": "BV1test",
        "bvid": "BV1test",
        "avid": "",
        "cid": 123,
        "link": "https://www.bilibili.com/video/BV1test",
        "title": "标题",
        "author": "UP主",
        "post_type": "视频",
        "media": [
            {"kind": "图片", "url": "https://image.example/cover.jpg"},
            {"kind": "视频", "url": "https://direct.example/full.mp4"},
        ],
    }
    source = {"name": "UP主", "value": "1"}
    with (
        mock.patch.object(
            bilibili, "_fetch_video_url", new=mock.AsyncMock(return_value=(None, None))
        ),
        mock.patch.object(
            bilibili,
            "fetch_bytes_many",
            new=mock.AsyncMock(return_value={"https://image.example/cover.jpg": b"cover"}),
        ),
    ):
        rendered = await bilibili._render_post(
            post, source, enabled_types=None, include_body=False
        )
        video_only = await bilibili._render_post(
            post, source, enabled_types={"视频"}, include_body=False
        )

    assert rendered is not None
    assert [item["kind"] for item in rendered[1]] == ["图片"]
    text = "".join(str(getattr(part, "data", {})) for part in rendered[0])
    assert "[视频]" not in text
    assert "direct.example" not in text
    assert video_only is None
    print("PASS - 视频取不到时不补 CDN/投稿页链接，仅视频时整条不发送")


async def main():
    await verify_only_360p()
    await verify_rejects_higher_quality()
    await verify_rejects_multi_segment()
    await verify_size_limit_without_download()
    await verify_download_failure()
    await verify_api_error()
    await verify_missing_cid()
    await verify_no_link_fallback()


if __name__ == "__main__":
    asyncio.run(main())
