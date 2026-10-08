"""验证 Bilibili view 接口业务错误码转换为可读原因。

`view` 接口在稿件不可见时返回的 message 就是数字本身（例如 62012），直接回复
给用户无法理解；本测试覆盖码值映射、未知码回退和成功路径。
"""
import asyncio
from unittest import mock

import nonebot

nonebot.init()

from plugins import bilibili


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


async def main():
    assert bilibili._view_error_text({"code": 62012, "message": "62012"}) == "仅UP主自己可见：62012"
    assert bilibili._view_error_text({"code": 62002, "message": "62002"}) == "稿件不可见：62002"
    assert bilibili._view_error_text({"code": 62004, "message": "62004"}) == "稿件审核中：62004"
    assert bilibili._view_error_text({"code": -403, "message": "访问权限不足"}) == "访问权限不足：-403"
    assert bilibili._view_error_text({"code": -404, "message": "啥都木有"}) == "投稿不存在或已删除：-404"
    assert bilibili._view_error_text({"code": -400, "message": "请求错误"}) == "投稿链接或请求参数无效：-400"
    # 未知码与空 message 回退到原始文案
    assert bilibili._view_error_text({"code": 12345, "message": "未知错误"}) == "未知错误"
    assert bilibili._view_error_text({"code": -1, "message": ""}) == "读取 Bilibili 投稿失败"
    assert bilibili._view_error_text({}) == "读取 Bilibili 投稿失败"

    with mock.patch.object(
        bilibili, "_bilibili_get", new=mock.AsyncMock(
            return_value=FakeResponse({"code": 62012, "message": "62012"})
        )
    ):
        try:
            await bilibili._fetch_view_data({"bvid": "BV1TnHx63EZi", "avid": ""})
        except RuntimeError as exc:
            assert str(exc) == "仅UP主自己可见：62012", str(exc)
        else:
            raise AssertionError("业务错误码应当抛出 RuntimeError")

    with mock.patch.object(
        bilibili, "_bilibili_get", new=mock.AsyncMock(
            return_value=FakeResponse({"code": 0, "data": {"bvid": "BV1TnHx63EZi"}})
        )
    ):
        data = await bilibili._fetch_view_data({"bvid": "BV1TnHx63EZi", "avid": ""})
        assert data == {"bvid": "BV1TnHx63EZi"}

    try:
        await bilibili._fetch_view_data({"bvid": "", "avid": ""})
    except ValueError as exc:
        assert "BV/AV" in str(exc), str(exc)
    else:
        raise AssertionError("缺少 BV/AV 号应当抛出 ValueError")

    print("PASS - Bilibili view 错误码文案、回退与成功路径")


if __name__ == "__main__":
    asyncio.run(main())
