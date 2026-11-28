"""B 站测试凭据处理回归：仅使用虚构值与模拟请求，不读取真实 Cookie。"""
import asyncio
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bilibili_probe", ROOT / "scripts/test_bilibili_source.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class CredentialTests(unittest.TestCase):
    def test_anonymous_does_not_read_environment(self):
        class UnreadableEnvironment:
            def get(self, *args):
                raise AssertionError("匿名模式不应读取凭据")
        self.assertIsNone(probe.build_credential("anonymous", UnreadableEnvironment()))

    def test_authenticated_requires_sessdata(self):
        with self.assertRaises(probe.ProbeValidationError):
            probe.build_credential("authenticated", {})

    def test_optional_fields_passed_without_refresh_token(self):
        env = {
            "BILI_SESSDATA": "unit-test-session",
            "BILI_JCT": "unit-test-csrf",
            "BILI_DEDEUSERID": "123",
            "BILI_BUVID3": "unit-test-buvid3",
            "BILI_BUVID4": "unit-test-buvid4",
            "BILI_AC_TIME_VALUE": "must-not-be-read",
        }
        with patch.object(probe, "Credential") as factory:
            probe.build_credential("authenticated", env)
        factory.assert_called_once_with(sessdata="unit-test-session", bili_jct="unit-test-csrf",
                                        dedeuserid="123", buvid3="unit-test-buvid3", buvid4="unit-test-buvid4")

    def test_minimal_credentials_allowed(self):
        with patch.object(probe, "Credential") as factory:
            probe.build_credential("authenticated", {"BILI_SESSDATA": "unit-test-session"})
        factory.assert_called_once_with(sessdata="unit-test-session")

    def test_bad_cookie_format_not_echoed(self):
        for value in ("unit-test-session; other=private", "unit-test-session\n", "SESSDATA=unit-test-session"):
            with self.subTest(case=value[:8]):
                with self.assertRaises(probe.ProbeValidationError) as caught:
                    probe.build_credential("authenticated", {"BILI_SESSDATA": value})
                self.assertNotIn("unit-test-session", str(caught.exception))

    def test_missing_secret_stops_before_network(self):
        feed, opus = AsyncMock(), AsyncMock()
        output = io.StringIO()
        with patch.dict(probe.os.environ, {}, clear=True), \
             patch.object(probe, "test_feed", feed), patch.object(probe, "test_opus", opus), \
             contextlib.redirect_stdout(output):
            result = asyncio.run(probe.main("curl_cffi", "authenticated"))
        self.assertEqual(result, 1)
        feed.assert_not_awaited()
        opus.assert_not_awaited()
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rows[-1]["failed_checks"], ["credential_config"])

    def test_credential_shared_and_not_logged(self):
        credential = object()
        feed, opus = AsyncMock(), AsyncMock()
        output = io.StringIO()
        with patch.dict(probe.os.environ, {"BILI_SESSDATA": "unit-test-session"}, clear=True), \
             patch.object(probe, "Credential", return_value=credential), \
             patch.object(probe, "test_feed", feed), patch.object(probe, "test_opus", opus), \
             contextlib.redirect_stdout(output):
            result = asyncio.run(probe.main("curl_cffi", "authenticated"))
        self.assertEqual(result, 0)
        feed.assert_awaited_once_with(credential)
        opus.assert_awaited_once_with(credential)
        self.assertNotIn("unit-test-session", output.getvalue())
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(rows[0]["mode"], "authenticated_read_only")

    def test_exception_body_not_logged(self):
        from bilibili_api.exceptions import ResponseCodeException
        feed = AsyncMock(side_effect=ResponseCodeException(-101, "unit-test-session", {"private": "unit-test-session"}))
        opus = AsyncMock()
        output = io.StringIO()
        with patch.object(probe, "build_credential", return_value=object()), \
             patch.object(probe, "test_feed", feed), patch.object(probe, "test_opus", opus), \
             contextlib.redirect_stdout(output):
            result = asyncio.run(probe.main("curl_cffi", "authenticated"))
        self.assertEqual(result, 1)
        opus.assert_awaited_once()
        self.assertNotIn("unit-test-session", output.getvalue())
        self.assertIn('"api_code": -101', output.getvalue())


if __name__ == "__main__":
    unittest.main()
