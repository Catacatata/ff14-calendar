"""Runner isolation regressions: local fixtures only, no credentials or network."""
import asyncio
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import collect_sources as runner
from source_collectors import official_news
from source_collectors.common import finish_status, load_cache, new_cache, record_content, save_cache

NOW = "2026-11-28T04:00:00+00:00"
OLD = "2026-11-26T04:00:00+00:00"
NEWS = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/395921"
FIXTURE = Path(__file__).parent / "fixtures" / "official_news_395921.json"


class FixtureNewsClient:
    def __init__(self):
        self.calls = []
        self.closed = False

    def detail(self, identity, timeout=20):
        self.calls.append(identity)
        return json.loads(FIXTURE.read_text(encoding="utf-8"))["payload"]

    def close(self):
        self.closed = True


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.client = FixtureNewsClient()
        self.bili_constructor = self.enterContext(patch.object(runner.bilibili, "BilibiliClient"))
        self.enterContext(patch.object(runner.official_news, "NewsClient", return_value=self.client))
        self.enterContext(patch.object(runner, "utc_now", return_value=NOW))
        # Any accidental transport call must fail locally instead of contacting a server.
        self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")))

    def run_source(self, source="official-news", ids=None):
        args = SimpleNamespace(source=source, news_id=ids, auth="authenticated",
                               output_dir=str(self.output), max_pages=4, max_details=20)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = asyncio.run(runner.run(args))
        return code, stream.getvalue()

    def write_bili(self, items):
        cache = new_cache("bilibili")
        cache["items"] = items
        save_cache(self.output / "bilibili.json", cache)

    def old_news(self):
        cache = new_cache("official-news")
        record = {"id": "395921", "detail_last_attempt_at": OLD}
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))["payload"]
        record_content(record, official_news.parse_detail(payload, "395921"), OLD)
        cache["items"]["395921"] = record
        save_cache(self.output / "official_news.json", cache)
        return copy.deepcopy(cache)

    def news_cache(self):
        return load_cache(self.output / "official_news.json", "official-news")

    def test_broken_bili_does_not_block_explicit_news_or_overwrite_bad_file(self):
        path = self.output / "bilibili.json"
        path.write_bytes(b"broken-cache")
        code, log = self.run_source(ids=["395921"])
        self.assertEqual(code, 1)
        self.assertEqual(path.read_bytes(), b"broken-cache")
        self.assertEqual(self.client.calls, ["395921"])
        self.assertEqual(self.news_cache()["items"]["395921"]["content_state"], "complete")
        self.bili_constructor.assert_not_called()

    def test_broken_bili_still_retries_news_own_cache(self):
        self.old_news()
        (self.output / "bilibili.json").write_bytes(b"broken-cache")
        code, _ = self.run_source()
        self.assertEqual(code, 1)
        self.assertEqual(self.client.calls, ["395921"])

    def test_bili_load_io_error_does_not_block_news(self):
        original = runner.load_cache
        def read(path, source):
            if source == "bilibili":
                raise OSError("private-file-error")
            return original(path, source)
        with patch.object(runner, "load_cache", side_effect=read):
            code, log = self.run_source(ids=["395921"])
        self.assertEqual(code, 1)
        self.assertNotIn("private-file-error", log)
        self.assertEqual(self.client.calls, ["395921"])
        self.assertFalse((self.output / "bilibili.json").exists())

    def test_all_bili_save_error_still_collects_news(self):
        original = runner.save_cache
        def save(path, cache):
            if cache["source"] == "bilibili":
                raise OSError("private-save-error")
            original(path, cache)
        cache = new_cache("bilibili")
        finish_status(cache, NOW, [])
        with patch.object(runner.bilibili, "collect", AsyncMock(return_value=cache)), \
                patch.object(runner, "save_cache", side_effect=save):
            code, log = self.run_source("all", ["395921"])
        self.assertEqual(code, 1)
        self.assertNotIn("private-save-error", log)
        self.assertEqual(self.client.calls, ["395921"])
        self.assertEqual(self.news_cache()["status"]["outcome"], "ok")
        self.bili_constructor.assert_called_once_with(auth="authenticated")

    def test_news_setup_error_preserves_old_content_and_redacts(self):
        old = self.old_news()
        with patch.object(runner.official_news, "NewsClient", side_effect=RuntimeError("SECRET-cookie-response")):
            code, log = self.run_source()
        self.assertEqual(code, 2)
        cache = self.news_cache()
        self.assertEqual(cache["items"], old["items"])
        self.assertEqual(cache["status"]["outcome"], "degraded")
        self.assertEqual(cache["status"]["errors"], [{"stage": "setup", "error_type": "RuntimeError"}])
        self.assertNotIn("SECRET", log + json.dumps(cache))

    def test_news_top_level_error_preserves_old_content_and_redacts(self):
        old = self.old_news()
        def failing_collect(cache, *args):
            cache["items"].clear()
            raise RuntimeError("SECRET-response")
        with patch.object(runner.official_news, "collect", side_effect=failing_collect):
            code, log = self.run_source()
        self.assertEqual(code, 2)
        self.assertEqual(self.news_cache()["items"], old["items"])
        self.assertNotIn("SECRET", log + json.dumps(self.news_cache()))
        self.assertTrue(self.client.closed)

    def test_feed_link_without_content_discovers_news(self):
        self.write_bili({"123": {"author_uid": str(runner.bilibili.UID), "feed": {"links": [NEWS]}}})
        before = (self.output / "bilibili.json").read_bytes()
        code, _ = self.run_source()
        self.assertEqual(code, 0)
        self.assertEqual(self.client.calls, ["395921"])
        self.assertEqual((self.output / "bilibili.json").read_bytes(), before)

    def test_discovery_merges_content_and_feed_but_rejects_wrong_author(self):
        self.write_bili({
            "1": {"author_uid": runner.bilibili.UID, "feed": {"links": [NEWS]},
                  "content": {"official_news_ids": ["395922"]}},
            "2": {"author_uid": "123", "feed": {"links": [NEWS.replace("395921", "395923")]},
                  "content": {"official_news_ids": ["395924"]}},
            "3": {"content": {"official_news_ids": ["395925"]}},
        })
        with patch.object(runner.official_news, "collect", wraps=official_news.collect) as collect:
            self.run_source(ids=["395926"])
        self.assertEqual(collect.call_args.args[2], ["395921", "395922", "395926"])

    def test_broken_news_cache_preserved_while_bili_runs(self):
        path = self.output / "official_news.json"
        path.write_bytes(b"broken-news")
        cache = finish_status(new_cache("bilibili"), NOW, [])
        with patch.object(runner.bilibili, "collect", AsyncMock(return_value=cache)) as collect:
            code, _ = self.run_source("all")
        self.assertEqual(code, 1)
        collect.assert_awaited_once()
        self.assertEqual(path.read_bytes(), b"broken-news")

    def test_all_broken_bili_still_collects_news(self):
        path = self.output / "bilibili.json"
        path.write_bytes(b"broken-cache")
        code, _ = self.run_source("all", ["395921"])
        self.assertEqual(code, 1)
        self.assertEqual(path.read_bytes(), b"broken-cache")
        self.assertEqual(self.client.calls, ["395921"])
        self.bili_constructor.assert_not_called()

    def test_bili_collect_exception_does_not_block_news(self):
        with patch.object(runner.bilibili, "collect", AsyncMock(side_effect=RuntimeError("SECRET-response"))):
            code, log = self.run_source("all", ["395921"])
        self.assertEqual(code, 2)
        self.assertEqual(self.client.calls, ["395921"])
        self.assertNotIn("SECRET", log)

    def test_news_save_error_returns_local_failure(self):
        old = self.old_news()
        before = (self.output / "official_news.json").read_bytes()
        with patch.object(runner, "save_cache", side_effect=OSError("SECRET-path")):
            code, log = self.run_source()
        self.assertEqual(code, 1)
        self.assertEqual((self.output / "official_news.json").read_bytes(), before)
        self.assertEqual(self.news_cache()["items"], old["items"])
        self.assertNotIn("SECRET", log)

    def test_bili_setup_failure_does_not_block_news(self):
        self.bili_constructor.side_effect = RuntimeError("SECRET-credential")
        code, log = self.run_source("all", ["395921"])
        self.assertEqual(code, 2)
        self.assertEqual(self.client.calls, ["395921"])
        self.assertNotIn("SECRET", log)

    def test_incomplete_outcome_returns_two(self):
        cache = new_cache("official-news")
        cache["status"] = {"outcome": "incomplete"}
        with patch.object(runner.official_news, "collect", return_value=cache):
            code, _ = self.run_source()
        self.assertEqual(code, 2)

    def test_default_authentication_is_unchanged(self):
        with patch("sys.argv", ["collect_sources.py"]):
            self.assertEqual(runner.parse_args().auth, "authenticated")


if __name__ == "__main__":
    unittest.main()
