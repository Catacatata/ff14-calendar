"""多源第一步回归测试。全部使用固定公开样本/模拟请求，不需要网络或凭据。"""
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from source_collectors import bilibili, official_news
from source_collectors.common import (SourceError, finish_status, load_cache, new_cache,
                                      news_ids, public_url, record_content, safe_error, save_cache)

NOW = "2026-11-28T04:00:00+00:00"
OLD = "2026-11-26T04:00:00+00:00"
ROOT = Path(__file__).resolve().parents[1]
NEWS = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/395921"
TEXT = "莫古莫古★大收集 天文的行路 第2阶段\n活动时间：2026年12月1日16:00~2027年1月12日15:59\n" + NEWS


def feed_item(identity, text=TEXT, pinned=False, kind="DYNAMIC_TYPE_DRAW", author=6655514):
    return {"id_str": str(identity), "type": kind,
            "modules": {"module_author": {"mid": author, "pub_ts": 1795510883},
                        "module_tag": {"text": "置顶" if pinned else ""},
                        "module_dynamic": {"desc": {"text": text}, "major": {"type": "MAJOR_TYPE_DRAW"}}}}


def opus_payload(identity, text=TEXT, author=6655514):
    return {"item": {"id_str": str(identity), "basic": {"uid": str(author)}, "modules": [
        {"module_content": {"paragraphs": [
            {"text": {"nodes": [{"word": {"words": text}}]}},
            {"pic": {"pics": [{"url": "https://i0.hdslb.com/bfs/public.jpg"}]}},
        ]}},
    ]}}


class FakeBili:
    def __init__(self, pages=None, detail_error=None, text=TEXT):
        self.pages = list(pages or [])
        self.detail_error = detail_error
        self.text = text
        self.feed_calls, self.detail_calls = [], []

    async def feed(self, offset=""):
        self.feed_calls.append(offset)
        response = self.pages.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def detail(self, identity):
        self.detail_calls.append(identity)
        if self.detail_error:
            raise self.detail_error
        return opus_payload(identity, self.text)


class CommonTests(unittest.TestCase):
    def test_cache_atomic_round_trip_and_corruption_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            cache = new_cache("bilibili")
            save_cache(path, cache)
            self.assertEqual(load_cache(path, "bilibili"), cache)
            path.write_text("broken", encoding="utf-8")
            with self.assertRaises(SourceError):
                load_cache(path, "bilibili")
            self.assertEqual(path.read_text(encoding="utf-8"), "broken")

    def test_public_urls_preserve_identity_and_news_hash(self):
        self.assertEqual(public_url(NEWS), NEWS)
        self.assertEqual(public_url("https://example.com/?id=1&SESSDATA=private&spm_id_from=test"), "https://example.com/?id=1")
        self.assertEqual(public_url("https://user:password@example.com"), "")
        self.assertEqual(news_ids([NEWS, "https://example.com/#/newstab/newscont/1"]), ["395921"])

    def test_content_history_changes_only_when_content_changes(self):
        record = {}
        record_content(record, {"text": "old"}, OLD)
        record_content(record, {"text": "old"}, NOW)
        self.assertEqual(record["revision_count"], 0)
        record_content(record, {"text": "new"}, NOW)
        self.assertEqual(record["revision_count"], 1)
        self.assertEqual(record["previous_versions"][0]["content"], {"text": "old"})
        for number in range(10):
            record_content(record, {"text": str(number)}, NOW)
        self.assertEqual(len(record["previous_versions"]), 5)

    def test_safe_errors_do_not_include_exception_body(self):
        class Denied(Exception):
            status = 412
        self.assertEqual(safe_error(Denied("private-cookie-value")), {"error_type": "Denied", "http_status": 412})
        self.assertNotIn("private", json.dumps(safe_error(RuntimeError("private-cookie-value"))))

    def test_skipped_run_does_not_advance_success_time(self):
        cache = new_cache("official-news")
        cache["status"]["last_success_at"] = OLD
        finish_status(cache, NOW, [], details_attempted=0)
        self.assertEqual(cache["status"]["last_success_at"], OLD)


class ParserTests(unittest.TestCase):
    def test_opus_extracts_text_links_and_images_without_dates_becoming_events(self):
        result = bilibili.parse_opus(opus_payload(10), "10")
        self.assertIn("2027年1月12日15:59", result["text"])
        self.assertEqual(result["official_news_ids"], ["395921"])
        self.assertEqual(result["images"], ["https://i0.hdslb.com/bfs/public.jpg"])
        self.assertNotIn("start", result)
        self.assertNotIn("end", result)

    def test_opus_rejects_wrong_author_and_empty_body(self):
        with self.assertRaises(SourceError):
            bilibili.parse_opus(opus_payload(10, author=1), "10")
        with self.assertRaises(SourceError):
            bilibili.parse_opus({"item": {"id_str": "10", "basic": {"uid": "6655514"}, "modules": []}}, "10")

    def test_word_nodes_are_joined_without_breaking_links(self):
        payload = opus_payload(10)
        payload["item"]["modules"][0]["module_content"]["paragraphs"] = [
            {"text": {"nodes": [{"word": {"words": "https://ff.web.sdo.com/"}},
                                  {"word": {"words": "web8/index.html#/newstab/newscont/395921"}}]}}]
        self.assertEqual(bilibili.parse_opus(payload, "10")["official_news_ids"], ["395921"])

    def test_real_official_news_fixture(self):
        fixture = json.loads((ROOT / "tests/fixtures/official_news_395921.json").read_text(encoding="utf-8"))
        result = official_news.parse_detail(fixture["payload"], "395921")
        self.assertIn("2026年12月1日16:00~2027年1月12日15:59", result["text"])
        self.assertIn("第2阶段", result["title"])
        self.assertIn("2026-11-24", result["published_at"])
        self.assertEqual(result["topic_links"], ["https://actff1.web.sdo.com/project/20261118mogmog/59xh7egc29ya/index.html"])
        self.assertNotIn("start", result)

    def test_news_rejects_wrong_id_and_empty_content(self):
        with self.assertRaises(SourceError):
            official_news.parse_detail({"Code": 0, "Data": {"Id": 1, "Content": "body"}}, "2")
        with self.assertRaises(SourceError):
            official_news.parse_detail({"Code": "0", "Data": {"Id": 1, "Content": ""}}, "1")


class BiliCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_two_pages_ignore_old_pin_and_keep_history(self):
        cache = new_cache("bilibili")
        for identity in (1, 2, 3):
            cache["items"][str(identity)] = bilibili.parse_feed_item(feed_item(identity))
        client = FakeBili([
            {"items": [feed_item(1, pinned=True), feed_item(4)], "has_more": True, "offset": "page2"},
            {"items": [feed_item(2)], "has_more": True, "offset": "page3"},
        ])
        state = await bilibili.collect(cache, client, NOW)
        self.assertEqual(client.feed_calls, ["", "page2"])
        self.assertEqual(set(state["items"]), {"1", "2", "3", "4"})
        self.assertEqual(state["status"]["new_count"], 1)
        self.assertEqual(state["status"]["stop_reason"], "known_overlap")
        self.assertEqual(set(cache["items"]), {"1", "2", "3"})

    async def test_feed_failure_keeps_complete_cached_content_and_success_time(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = bilibili.parse_feed_item(feed_item(1))
        record_content(cache["items"]["1"], {"text": "cached"}, OLD)
        cache["items"]["1"]["detail_last_attempt_at"] = OLD
        cache["items"]["1"]["detail_feed_hash"] = cache["items"]["1"]["feed_hash"]
        cache["status"] = {"last_success_at": OLD, "consecutive_failures": 2}
        state = await bilibili.collect(cache, FakeBili([TimeoutError()]), NOW)
        self.assertEqual(state["items"]["1"]["content"]["text"], "cached")
        self.assertEqual(state["status"]["last_success_at"], OLD)
        self.assertEqual(state["status"]["consecutive_failures"], 3)
        self.assertTrue(state["status"]["used_cache"])

    async def test_412_stops_remaining_bilibili_requests(self):
        class Denied(Exception):
            status = 412
        cache = new_cache("bilibili")
        cache["items"]["1"] = bilibili.parse_feed_item(feed_item(1))
        client = FakeBili([Denied("never-log-this")])
        state = await bilibili.collect(cache, client, NOW)
        self.assertEqual(client.detail_calls, [])
        self.assertEqual(state["status"]["errors"][0]["http_status"], 412)
        self.assertNotIn("never-log-this", json.dumps(state))

    async def test_empty_feed_does_not_clear_cache(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = bilibili.parse_feed_item(feed_item(1, kind="DYNAMIC_TYPE_FORWARD"))
        state = await bilibili.collect(cache, FakeBili([{"items": []}]), NOW)
        self.assertIn("1", state["items"])
        self.assertEqual(state["status"]["outcome"], "degraded")

    async def test_detail_failure_preserves_old_text_and_is_retried_later(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = bilibili.parse_feed_item(feed_item(1))
        record_content(cache["items"]["1"], {"text": "cached"}, OLD)
        client = FakeBili([{"items": [feed_item(1, text="updated")], "has_more": False}], detail_error=TimeoutError())
        state = await bilibili.collect(cache, client, NOW)
        self.assertEqual(state["items"]["1"]["content"]["text"], "cached")
        self.assertIsNotNone(state["items"]["1"]["detail_error"])
        next_client = FakeBili([{"items": [feed_item(1, text="updated")], "has_more": False}])
        await bilibili.collect(state, next_client, NOW)
        self.assertEqual(next_client.detail_calls, [])

    async def test_pending_detail_retries_when_not_in_latest_feed(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = bilibili.parse_feed_item(feed_item(1))
        cache["items"]["1"]["content_state"] = "pending"
        client = FakeBili([{"items": [feed_item(2)], "has_more": False}])
        state = await bilibili.collect(cache, client, NOW)
        self.assertIn("1", client.detail_calls)
        self.assertEqual(state["items"]["1"]["content_state"], "complete")

    async def test_detail_limit_and_unsupported_reposts(self):
        client = FakeBili([{"items": [feed_item(1), feed_item(2), feed_item(3, kind="DYNAMIC_TYPE_FORWARD")], "has_more": False}])
        state = await bilibili.collect(new_cache("bilibili"), client, NOW, max_details=1)
        self.assertEqual(len(client.detail_calls), 1)
        self.assertEqual(state["status"]["deferred_details"], 1)
        self.assertEqual(state["items"]["3"]["content_state"], "unsupported")

    async def test_repeated_offset_stops_pagination(self):
        client = FakeBili([
            {"items": [feed_item(1)], "has_more": True, "offset": "same"},
            {"items": [feed_item(2)], "has_more": True, "offset": "same"},
        ])
        state = await bilibili.collect(new_cache("bilibili"), client, NOW)
        self.assertEqual(len(client.feed_calls), 2)
        self.assertEqual(state["status"]["outcome"], "degraded")


class NewsCollectorTests(unittest.TestCase):
    def test_news_failure_preserves_cache_and_sanitizes_error(self):
        cache = new_cache("official-news")
        cache["items"]["395921"] = {"id": "395921", "content_state": "complete", "content": {"text": "cached"}, "last_success_at": OLD}
        class Failing:
            def detail(self, *args, **kwargs):
                raise RuntimeError("private-cookie-value")
        state = official_news.collect(cache, Failing(), ["395921"], NOW)
        self.assertEqual(state["items"]["395921"]["content"]["text"], "cached")
        self.assertNotIn("private-cookie-value", json.dumps(state))

    def test_news_cached_record_is_not_fetched_again_in_same_day(self):
        fixture = json.loads((ROOT / "tests/fixtures/official_news_395921.json").read_text(encoding="utf-8"))
        class Client:
            calls = 0
            def detail(self, *args, **kwargs):
                self.calls += 1
                return fixture["payload"]
        client = Client()
        state = official_news.collect(new_cache("official-news"), client, ["395921"], NOW)
        state = official_news.collect(state, client, ["395921"], NOW)
        self.assertEqual(client.calls, 1)
        self.assertEqual(state["items"]["395921"]["content_state"], "complete")


if __name__ == "__main__":
    unittest.main()
