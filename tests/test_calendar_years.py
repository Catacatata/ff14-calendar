"""跨年获取回归测试：只使用模拟 API 和临时文件，不访问网络。"""
import copy
import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from icalendar import Calendar

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("calendar_generator", ROOT / "generate_calendar.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def api_event(event_id, start="2026-12-20T16:00:00+08:00", end="2027-01-15T22:59:59+08:00"):
    return {
        "id": event_id,
        "name": "糖豆人联动",
        "begin_time": int(datetime.datetime.fromisoformat(start).timestamp()),
        "end_time": int(datetime.datetime.fromisoformat(end).timestamp()),
        "url": "https://example.com/event",
    }


class CalendarYearTests(unittest.TestCase):
    def test_year_range_rolls_forward(self):
        for year in (2026, 2027, 2030):
            with self.subTest(year=year):
                months = generator.get_api_months(datetime.datetime(year, 6, 1))
                self.assertEqual(len(months), 36)
                self.assertEqual(months[0], (year - 1, 1))
                self.assertEqual(months[-1], (year + 1, 12))
                self.assertEqual(len(set(months)), 36)

    def test_year_boundary_uses_beijing_time(self):
        before = datetime.datetime(2026, 12, 31, 15, 59, 59, tzinfo=datetime.timezone.utc)
        after = before + datetime.timedelta(seconds=1)
        self.assertEqual(generator.get_api_months(before)[0], (2025, 1))
        self.assertEqual(generator.get_api_months(after)[0], (2026, 1))
        self.assertEqual(generator.get_api_months(after)[-1], (2028, 12))

    def test_default_clock_uses_beijing_timezone(self):
        now = generator.TZ.localize(datetime.datetime(2027, 1, 1))
        with patch.object(generator.datetime, "datetime", wraps=datetime.datetime) as clock:
            clock.now.return_value = now
            self.assertEqual(generator.get_api_months()[-1], (2028, 12))
            clock.now.assert_called_once_with(tz=generator.TZ)

    def test_failed_requests_keep_monthly_and_historical_cache(self):
        cache = {"2023-12": [api_event(1)], "2026-12": [api_event(2)]}
        with patch.object(generator, "load_cache", return_value=copy.deepcopy(cache)), \
             patch.object(generator, "save_cache") as save, \
             patch.object(generator, "fetch_api", return_value=None) as fetch:
            result = generator.get_api_events(datetime.datetime(2027, 1, 1))
        self.assertEqual({item["id"] for item in result}, {1, 2})
        self.assertEqual(fetch.call_count, 36)
        self.assertEqual(set(call.args for call in fetch.call_args_list), set(generator.get_api_months(datetime.datetime(2027, 1, 1))))
        save.assert_called_once_with(cache)

    def test_fetches_previous_and_next_year_and_keeps_old_cache(self):
        responses = {(2026, 12): [api_event(2)], (2028, 1): [api_event(3)]}
        with patch.object(generator, "load_cache", return_value={"2024-01": [api_event(1)]}), \
             patch.object(generator, "save_cache") as save, \
             patch.object(generator, "fetch_api", side_effect=lambda y, m: responses.get((y, m), [])):
            result = generator.get_api_events(datetime.datetime(2027, 1, 1))
        self.assertEqual({item["id"] for item in result}, {1, 2, 3})
        saved = save.call_args.args[0]
        self.assertEqual(len(saved), 37)
        self.assertEqual(saved["2028-01"], responses[(2028, 1)])
        self.assertIn("2024-01", saved)

    def test_fresh_event_wins_over_stale_cross_month_copy(self):
        old = api_event(10)
        revised = api_event(10, start="2026-12-21T16:00:00+08:00")
        cache = {"2026-12": [old], "2027-01": [old]}
        # 新结果在更早月份，后面的月份回退旧缓存也不能将新结果覆盖。
        def fetch(year, month):
            return [revised] if (year, month) == (2026, 12) else None
        with patch.object(generator, "load_cache", return_value=cache), \
             patch.object(generator, "save_cache"), \
             patch.object(generator, "fetch_api", side_effect=fetch):
            result = generator.get_api_events(datetime.datetime(2027, 1, 1))
        self.assertEqual(result, [revised])
        self.assertEqual(generator.convert_api(result[0])["id"], "api-10")

    def test_cross_year_json_and_ics_have_same_dates_and_stable_uid(self):
        event = api_event(10)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(generator, "get_api_events", return_value=[event]), \
                 patch.object(generator, "load_base_ics", return_value=[]), \
                 patch.object(generator, "load_manual", return_value=[]), \
                 patch.object(generator, "SOURCES_DIR", str(output / "no_sources")), \
                 patch.object(generator, "OUTPUT_FILE", str(output / "ff14.ics")), \
                 patch.object(generator, "JSON_OUTPUT_FILE", str(output / "calendar.json")):
                generator.generate()
            payload = json.loads((output / "calendar.json").read_text(encoding="utf-8"))
            # 使用文本模式读取，与生成器一致，并兼容 Windows 换行转换。
            events = Calendar.from_ical((output / "ff14.ics").read_text(encoding="utf-8")).walk("VEVENT")
            self.assertEqual(len(events), 1)
            self.assertEqual(payload["event_count"], 1)
            web = payload["events"][0]
            self.assertEqual(str(events[0]["UID"]), web["id"])
            self.assertEqual(web["id"], "api-10")
            self.assertEqual(events[0]["DTSTART"].dt, datetime.datetime.fromisoformat(web["start"]))
            self.assertEqual(events[0]["DTEND"].dt, datetime.datetime.fromisoformat(web["end"]))
            self.assertTrue(web["start"].startswith("2026-12-"))
            self.assertTrue(web["end"].startswith("2027-01-"))
            self.assertEqual(web["category"], "限时联动")
            self.assertEqual(str(events[0]["SUMMARY"]), "[限时联动] 糖豆人联动")


if __name__ == "__main__":
    unittest.main()
