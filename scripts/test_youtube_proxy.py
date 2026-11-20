"""Verify the YouTube proxy routes without contacting an upstream service."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import httpx
import nonebot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="wrelay-youtube-env-") as empty_dir:
    original_dir = os.getcwd()
    try:
        os.chdir(empty_dir)
        nonebot.init(driver="~fastapi+~httpx", _env_file=None)
    finally:
        os.chdir(original_dir)

from plugins import youtube


FEED_URL = "https://www.youtube.com/feeds/videos.xml"
PRIMARY_PROXY = "http://127.0.0.1:8080"
SYSTEM_PROXY = "http://127.0.0.1:8081"


class YoutubeProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolated = mock.patch.multiple(
            youtube,
            _feed_proxy_override=None,
            _feed_proxy_override_source=None,
            _feed_request_tasks=set(),
            _feed_stopping=False,
        )
        isolated.start()
        self.addCleanup(isolated.stop)

    async def test_connect_timeout_switches_and_reuses_listening_proxy(self):
        for primary_proxy in (None, PRIMARY_PROXY):
            with self.subTest(primary_proxy=primary_proxy):
                response = httpx.Response(200, content=b"feed")
                primary = mock.Mock(get=mock.AsyncMock(side_effect=httpx.ConnectTimeout("timeout")))
                fallback = mock.Mock(get=mock.AsyncMock(return_value=response))
                with (
                    mock.patch.object(youtube, "_read_youtube_proxy", return_value=primary_proxy),
                    mock.patch.object(youtube, "_get_feed_client", side_effect=[primary, fallback, fallback]) as clients,
                    mock.patch.object(youtube, "system_loopback_http_proxy", return_value=SYSTEM_PROXY) as system_proxy,
                    mock.patch.object(youtube, "port_is_open", return_value=True) as probe,
                ):
                    self.assertIs(await youtube._youtube_get(FEED_URL, timeout=7), response)
                    self.assertIs(await youtube._youtube_get(FEED_URL, timeout=7), response)

                self.assertEqual(clients.call_args_list, [
                    mock.call(proxy=primary_proxy, explicit_proxy=True),
                    mock.call(proxy=SYSTEM_PROXY, explicit_proxy=True),
                    mock.call(proxy=SYSTEM_PROXY, explicit_proxy=True),
                ])
                primary.get.assert_awaited_once_with(FEED_URL, timeout=7)
                self.assertEqual(fallback.get.await_args_list, [mock.call(FEED_URL, timeout=7)] * 2)
                system_proxy.assert_called_once_with()
                probe.assert_called_once_with("127.0.0.1", 8081)
                youtube._feed_proxy_override = None
                youtube._feed_proxy_override_source = None

    async def test_remote_proxy_timeout_keeps_the_explicit_route(self):
        remote = "https://proxy.example.test:8443"
        primary = mock.Mock(get=mock.AsyncMock(side_effect=httpx.ConnectTimeout("timeout")))
        with (
            mock.patch.object(youtube, "_read_youtube_proxy", return_value=remote),
            mock.patch.object(youtube, "_get_feed_client", return_value=primary) as clients,
            mock.patch.object(youtube, "system_loopback_http_proxy") as system_proxy,
        ):
            with self.assertRaises(httpx.ConnectTimeout):
                await youtube._youtube_get(FEED_URL)
        clients.assert_called_once_with(proxy=remote, explicit_proxy=True)
        system_proxy.assert_not_called()

    async def test_same_or_closed_system_proxy_leaves_the_original_timeout(self):
        for candidate, expected_probes in ((PRIMARY_PROXY, 0), (SYSTEM_PROXY, 1), (None, 0)):
            with self.subTest(candidate=candidate):
                fault = httpx.ConnectTimeout("timeout")
                primary = mock.Mock(get=mock.AsyncMock(side_effect=fault))
                with (
                    mock.patch.object(youtube, "_read_youtube_proxy", return_value=PRIMARY_PROXY),
                    mock.patch.object(youtube, "_get_feed_client", return_value=primary) as clients,
                    mock.patch.object(youtube, "system_loopback_http_proxy", return_value=candidate),
                    mock.patch.object(youtube, "port_is_open", return_value=False) as probe,
                ):
                    with self.assertRaises(httpx.ConnectTimeout) as caught:
                        await youtube._youtube_get(FEED_URL)
                self.assertIs(caught.exception, fault)
                clients.assert_called_once_with(proxy=PRIMARY_PROXY, explicit_proxy=True)
                self.assertEqual(probe.call_count, expected_probes)
                self.assertIsNone(youtube._feed_proxy_override)

    async def test_read_timeout_and_http_429_do_not_use_another_proxy(self):
        limited = httpx.Response(429, headers={"Retry-After": "45"})
        primary = mock.Mock(get=mock.AsyncMock(side_effect=[httpx.ReadTimeout("read"), limited]))
        with (
            mock.patch.object(youtube, "_read_youtube_proxy", return_value=PRIMARY_PROXY),
            mock.patch.object(youtube, "_get_feed_client", return_value=primary),
            mock.patch.object(youtube, "system_loopback_http_proxy") as system_proxy,
        ):
            with self.assertRaises(httpx.ReadTimeout):
                await youtube._youtube_get(FEED_URL)
            self.assertIs(await youtube._youtube_get(FEED_URL), limited)
        system_proxy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
