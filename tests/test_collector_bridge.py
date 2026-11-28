"""多来源官方补充日历事件测试：
验证游戏活动、维护、制作人来信的保守提取，以及过滤商城促销、避免与已有事件冲突。
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from source_collectors.collector_bridge import load_source_events, _is_included

TZ = timezone(timedelta(hours=8))


class CollectorBridgeTests(unittest.TestCase):
    def test_filter_promotions_and_sales(self):
        # 允许的活动与公告
        self.assertTrue(_is_included("activity", "莫古莫古★大收集 ~天文的行路 第2阶段~ 限时开启！"))
        self.assertTrue(_is_included("maintenance", "《最终幻想14》11月10日全区全服更新维护公告"))
        self.assertTrue(_is_included("livestream", "第94期制作人来信转播 12月4日上演！"))
        self.assertTrue(_is_included("version", "7.1版本更新公告"))

        # 过滤商城、促销、优惠券等
        self.assertFalse(_is_included("sale", "道具商城历届粉丝节道具优惠券大派送！"))
        self.assertFalse(_is_included("sale_discount", "8.0抢先体验典藏包 11月4日超5折特惠开售!"))
        self.assertFalse(_is_included("activity", "特惠专享 畅玩无忧！月卡启航计划活动时间延长公告"))
        self.assertFalse(_is_included("activity", "哔哩哔哩特别活动-光之战士激励计划!"))
        self.assertFalse(_is_included("sale", "冒险准备 《金曦冒险录·银冬将至》限时上架！"))
        self.assertFalse(_is_included("activity", ""))

    def test_load_source_events_live_cache(self):
        # 使用真实公开缓存 data/sources 进行解析测试
        sources_dir = Path(__file__).resolve().parents[1] / "data" / "sources"
        if not sources_dir.exists():
            self.skipTest("data/sources 不存在，跳过缓存集成测试")

        # 模拟空的已有事件
        events = load_source_events(sources_dir, [])
        self.assertGreaterEqual(len(events), 4)

        names = [e["name"] for e in events]
        # 应该包含莫古莫古、维护、来信
        self.assertTrue(any("莫古莫古" in n for n in names))
        self.assertTrue(any("维护" in n for n in names))
        self.assertTrue(any("制作人来信" in n for n in names))

        # 不应包含月卡、打折、典藏包
        self.assertFalse(any("月卡" in n for n in names))
        self.assertFalse(any("典藏包" in n for n in names))
        self.assertFalse(any("优惠券" in n for n in names))

        # 来信应为单时刻日程
        live = next(e for e in events if "制作人来信" in e["name"])
        self.assertEqual(live["time_kind"], "start_only")
        self.assertTrue(live["is_point"])
        self.assertEqual(live["start"], live["end"])


if __name__ == "__main__":
    unittest.main()
