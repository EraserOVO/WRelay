"""Offline QQ webhook crypto compatibility checks; no real accounts or requests."""
import json
from types import SimpleNamespace
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nonebot.adapters.qq import Adapter
from nonebot.adapters.discord import Adapter as DiscordAdapter
from nonebot.drivers import Request


class QQCryptoCompatibility(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(Adapter)
        self.bot = SimpleNamespace(bot_info=SimpleNamespace(secret="wrelay-test-secret"))
        self.key = self.adapter._get_ed25519_key(self.bot)
        self.timestamp = "1780000000"
        self.body = b'{"id":"offline-test"}'

    def request(self, body=None, signature=None):
        if signature is None:
            signature = self.key.sign(self.timestamp.encode() + self.body).hex()
        return Request("POST", "https://example.invalid/qq", headers={
            "X-Signature-Timestamp": self.timestamp,
            "X-Signature-Ed25519": signature,
        }, content=self.body if body is None else body)

    def test_webhook_challenge_signature(self):
        payload = SimpleNamespace(data=SimpleNamespace(
            plain_token="test-token", event_ts=self.timestamp))
        response = self.adapter._webhook_verify(self.bot, payload)
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["plain_token"], "test-token")
        self.key.public_key().verify(bytes.fromhex(result["signature"]),
                                     (self.timestamp + "test-token").encode())

    def test_valid_and_tampered_requests(self):
        self.assertIsNone(self.adapter._check_signature(self.bot, self.request()))
        self.assertEqual(self.adapter._check_signature(
            self.bot, self.request(body=b"tampered")).status_code, 403)
        other_key = Ed25519PrivateKey.generate()
        forged = other_key.sign(self.timestamp.encode() + self.body).hex()
        self.assertEqual(self.adapter._check_signature(
            self.bot, self.request(signature=forged)).status_code, 403)

    def test_missing_signature_rejected(self):
        request = Request("POST", "https://example.invalid/qq", content=self.body)
        self.assertEqual(self.adapter._check_signature(self.bot, request).status_code, 403)

    def test_rfc8032_vector(self):
        # RFC 8032 section 7.1, test 1: independent Ed25519 reference vector.
        seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
        expected = bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
        key = Ed25519PrivateKey.from_private_bytes(seed)
        self.assertEqual(key.sign(b""), expected)
        key.public_key().verify(expected, b"")
        self.assertEqual(DiscordAdapter.get_name(), "Discord")


if __name__ == "__main__":
    unittest.main()
