"""审查补充回归：持久扫描缺口、旧正文更新、冷却恢复和真实富文本。"""
import copy
import json
from pathlib import Path
import unittest

from source_collectors import bilibili, official_news
from source_collectors.common import new_cache, record_content
from test_sources import FakeBili, feed_item, opus_payload, NOW, OLD, NEWS

ROOT = Path(__file__).resolve().parents[1]


def complete_record(identity):
    record = bilibili.parse_feed_item(feed_item(identity))
    record_content(record, bilibili.parse_opus(opus_payload(identity), str(identity)), OLD)
    record["detail_feed_hash"] = record["feed_hash"]
    record["detail_last_attempt_at"] = OLD
    return record


def page(identity, offset=None):
    return {"items": [feed_item(identity)], "has_more": offset is not None, "offset": offset or ""}


class PagedClient(FakeBili):
    def __init__(self, pages):
        super().__init__()
        self.mapping = pages

    async def feed(self, offset=""):
        self.feed_calls.append(offset)
        data = self.mapping[offset]
        if isinstance(data, Exception):
            raise data
        return copy.deepcopy(data)


class ContinuityTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_limit_persists_old_boundary_and_eventually_closes_gap(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = complete_record(1)
        pages = {"": page(10, "a"), "a": page(9, "b"), "b": page(8, "c"),
                 "c": page(7, "d"), "d": page(6, "e"), "e": page(5, "f"), "f": page(1)}
        first = await bilibili.collect(cache, PagedClient(pages), NOW)
        self.assertEqual(first["scan_queue"][0]["offset"], "d")
        self.assertEqual(first["scan_queue"][0]["boundary_ids"], ["1"])
        self.assertEqual(first["status"]["outcome"], "incomplete")
        self.assertIsNone(first["status"]["last_success_at"])
        second_client = PagedClient(pages)
        second = await bilibili.collect(first, second_client, NOW)
        self.assertEqual(second_client.feed_calls, ["", "a", "d", "e"])
        self.assertEqual(second["scan_queue"][0]["offset"], "f")
        self.assertEqual(second["scan_queue"][0]["boundary_ids"], ["1"])
        third = await bilibili.collect(second, PagedClient(pages), NOW)
        self.assertEqual(set(third["items"]), {"1", "5", "6", "7", "8", "9", "10"})
        self.assertEqual(third["scan_queue"], [])
        self.assertEqual(third["status"]["outcome"], "ok")

    async def test_new_bursts_during_backlog_do_not_hide_either_gap(self):
        class Timeline(FakeBili):
            def __init__(self):
                super().__init__()
                self.latest = 20

            async def feed(self, offset=""):
                self.feed_calls.append(offset)
                ceiling = int(offset) - 1 if offset else self.latest
                identities = list(range(ceiling, max(0, ceiling - 2), -1))
                rows = [feed_item(identity) for identity in identities]
                if not offset:
                    rows.insert(0, feed_item(1, pinned=True))
                return {"items": rows, "has_more": identities[-1] > 1, "offset": str(identities[-1])}
        client = Timeline()
        state = await bilibili.collect(new_cache("bilibili"), client, NOW)
        self.assertEqual(set(state["items"]), {"1", *(str(identity) for identity in range(13, 21))})
        for latest in (40, 45, 50):
            client.latest = latest
            state = await bilibili.collect(state, client, NOW)
        self.assertTrue(state["scan_queue"])
        for _ in range(20):
            state = await bilibili.collect(state, client, NOW)
            if not state["scan_queue"]:
                break
        self.assertFalse(state["scan_queue"])
        self.assertEqual(set(state["items"]), {"1", *(str(identity) for identity in range(13, 51))})
        self.assertEqual(state["status"]["outcome"], "ok")

    async def test_initial_snapshot_is_deliberately_bounded_not_full_history(self):
        client = PagedClient({"": page(10, "a"), "a": page(9, "b"), "b": page(8, "c"), "c": page(7, "d")})
        state = await bilibili.collect(new_cache("bilibili"), client, NOW)
        self.assertEqual(len(client.feed_calls), 4)
        self.assertEqual(state["scan_queue"], [])
        self.assertTrue(state["status"]["initial_history_limited"])
        self.assertEqual(state["status"]["coverage_scope"], "since_initial_recent_window")

    async def test_failed_initial_snapshot_retains_remaining_baseline_pages(self):
        client = PagedClient({"": page(10, "a"), "a": TimeoutError()})
        state = await bilibili.collect(new_cache("bilibili"), client, NOW)
        self.assertEqual(state["scan_queue"][0]["offset"], "a")
        self.assertEqual(state["scan_queue"][0]["baseline_pages_left"], 3)
        pages = {"": page(10, "a"), "a": page(9, "b"), "b": page(8, "c"), "c": page(7, "d")}
        for _ in range(3):
            state = await bilibili.collect(state, PagedClient(pages), NOW)
        self.assertEqual(set(state["items"]), {"7", "8", "9", "10"})
        self.assertFalse(state["scan_queue"])

    async def test_updated_complete_body_retries_after_leaving_feed(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = complete_record(1)
        client = FakeBili([{"items": [feed_item(1, text="edited summary")], "has_more": False}], detail_error=TimeoutError())
        failed = await bilibili.collect(cache, client, NOW)
        self.assertEqual(failed["items"]["1"]["content_state"], "complete")
        old_text = failed["items"]["1"]["content"]["text"]
        retry = FakeBili([page(2)], text="new full body")
        recovered = await bilibili.collect(failed, retry, "2026-11-29T04:00:00+00:00")
        self.assertIn("1", retry.detail_calls)
        self.assertNotEqual(recovered["items"]["1"]["content"]["text"], old_text)
        self.assertIsNone(recovered["items"]["1"]["detail_error"])

    async def test_updated_complete_body_deferred_by_limit_retries_off_feed(self):
        cache = new_cache("bilibili")
        for identity in (1, 2):
            cache["items"][str(identity)] = complete_record(identity)
        changed = {"items": [feed_item(1, text="edited"), feed_item(2, text="edited")], "has_more": False}
        first = await bilibili.collect(cache, FakeBili([changed]), NOW, max_details=1)
        self.assertNotEqual(first["items"]["2"]["feed_hash"], first["items"]["2"]["detail_feed_hash"])
        client = FakeBili([page(3)])
        second = await bilibili.collect(first, client, NOW)
        self.assertIn("2", client.detail_calls)
        self.assertEqual(second["items"]["2"]["feed_hash"], second["items"]["2"]["detail_feed_hash"])

    async def test_cooling_body_error_does_not_report_recovery_from_good_feed(self):
        cache = new_cache("bilibili")
        cache["items"]["1"] = complete_record(1)
        cache["status"]["last_success_at"] = OLD
        failed = await bilibili.collect(cache, FakeBili([page(1)], detail_error=TimeoutError()), NOW)
        client = FakeBili([page(1)])
        cooled = await bilibili.collect(failed, client, NOW)
        self.assertEqual(client.detail_calls, [])
        self.assertEqual(cooled["status"]["outcome"], "degraded")
        self.assertEqual(cooled["status"]["request_outcome"], "ok")
        self.assertEqual(cooled["status"]["last_success_at"], OLD)
        self.assertEqual(cooled["status"]["consecutive_failures"], 1)
        self.assertEqual(cooled["status"]["cooldown_count"], 1)
        self.assertEqual(cooled["status"]["deferred_details"], 1)

    async def test_limit_is_incomplete_not_success_then_recovers(self):
        first = await bilibili.collect(new_cache("bilibili"), FakeBili([
            {"items": [feed_item(1), feed_item(2)], "has_more": False}]), NOW, max_details=1)
        self.assertEqual(first["status"]["outcome"], "incomplete")
        self.assertEqual(first["status"]["pending_count"], 1)
        second = await bilibili.collect(first, FakeBili([page(1)]), NOW)
        self.assertEqual(second["status"]["outcome"], "ok")
        self.assertEqual(second["status"]["last_success_at"], NOW)


class RealOpusTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / "tests/fixtures/bilibili_opus_1274198612158447624.json").read_text(encoding="utf-8"))["payload"]

    def test_real_public_opus_content_and_date(self):
        parsed = bilibili.parse_opus(self.fixture(), "1274198612158447624")
        self.assertIn("2027年1月12日15:59", parsed["text"])
        self.assertEqual(parsed["official_news_ids"], ["395921"])
        self.assertEqual(len(parsed["images"]), 1)

    def test_real_rich_topic_link_is_extracted_without_url_display_text(self):
        parsed = bilibili.parse_opus(self.fixture(), "1274198612158447624")
        self.assertIn("https://search.bilibili.com/all?keyword=FF14", parsed["links"])

    def test_real_node_shape_with_simulated_non_url_news_link(self):
        # 真实样本的新闻是裸 URL；在其真实 rich 节点形状上构造显示名变体，
        # 不冒称该变体也是线上返回的原样内容。
        payload = self.fixture()
        count = 0
        for module in payload["item"]["modules"]:
            for paragraph in (module.get("module_content") or {}).get("paragraphs", []):
                nodes = (paragraph.get("text") or {}).get("nodes", [])
                template = next((node["rich"] for node in nodes if (node.get("rich") or {}).get("jump_url")), None)
                for node in nodes:
                    if "newscont/395921" in (node.get("word") or {}).get("words", ""):
                        node["type"] = "TEXT_NODE_TYPE_RICH"
                        node["word"] = None
                        node["rich"] = copy.deepcopy(template)
                        node["rich"].update(text="网页链接", orig_text="网页链接", jump_url=NEWS,
                                            type="RICH_TEXT_NODE_TYPE_WEB")
                        count += 1
        self.assertEqual(count, 1)
        parsed = bilibili.parse_opus(payload, "1274198612158447624")
        self.assertNotIn(NEWS, parsed["text"])
        self.assertEqual(parsed["official_news_ids"], ["395921"])

    def test_news_failure_cooldown_preserves_failure_until_real_recovery(self):
        fixture = json.loads((ROOT / "tests/fixtures/official_news_395921.json").read_text(encoding="utf-8"))["payload"]
        class Client:
            fail = True
            calls = 0
            def detail(self, *args, **kwargs):
                self.calls += 1
                if self.fail:
                    raise TimeoutError()
                return fixture
        client = Client()
        first = official_news.collect(new_cache("official-news"), client, ["395921"], NOW)
        second = official_news.collect(first, client, [], NOW)
        self.assertEqual(client.calls, 1)
        self.assertEqual(second["status"]["outcome"], "degraded")
        self.assertEqual(second["status"]["request_outcome"], "skipped")
        self.assertEqual(second["status"]["consecutive_failures"], 1)
        self.assertIsNone(second["status"]["last_success_at"])
        self.assertEqual(second["status"]["cooldown_count"], 1)
        client.fail = False
        third = official_news.collect(second, client, [], "2026-11-28T10:00:00+00:00")
        self.assertEqual(third["status"]["outcome"], "ok")
        self.assertEqual(third["status"]["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
