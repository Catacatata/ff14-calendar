"""从官方公开来源（B站、新闻）中解析与补充游戏活动、维护及直播事件。
排除了商城上架、打折、优惠券等促销内容；日期保守解析，不伪造未公布的时刻。
"""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re

from shadow_calendar.dates import parse_document
from shadow_calendar.engine import edition, topic_key, _endpoint_compatible
from source_collectors.common import news_ids, public_url

TZ = timezone(timedelta(hours=8))
ALLOWED_KINDS = {"activity", "maintenance", "livestream", "version"}
IGNORED_PATTERNS = re.compile(r"商城|上架|上新|开售|特惠|折扣|优惠券|充值|月卡|典藏包|促销|周边|激励计划|转发抽奖|投稿征集")


def _is_included(kind, title):
    title_str = str(title or "")
    if not title_str.strip():
        return False
    return kind in ALLOWED_KINDS and not IGNORED_PATTERNS.search(title_str)


def load_source_events(source_dir, existing_events=None):
    """读取 sources 缓存，提取官方补充事件（维护、活动、来信）。
    返回事件列表，格式与 generate_calendar.py 兼容。
    """
    source_dir = Path(source_dir)
    bili_file = source_dir / "bilibili.json"
    news_file = source_dir / "official_news.json"
    if not bili_file.is_file() or not news_file.is_file():
        return []

    try:
        bili_data = json.loads(bili_file.read_text(encoding="utf-8"))
        news_data = json.loads(news_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    existing_events = existing_events or []
    # 建立现有事件索引：用于判断是否已有相同专题或新闻的活动，避免重复
    existing_topics = set()
    existing_news_ids = set()
    for ev in existing_events:
        url = ev.get("url", "")
        if url:
            tk = topic_key(url)
            if tk:
                existing_topics.add(tk)
        src = ev.get("details_source", "")
        if src:
            for nid in news_ids([src, url]):
                existing_news_ids.add(nid)

    # 提取所有有效正文文档
    documents = []
    for cache, src_name in [(news_data, "official-news"), (bili_data, "bilibili")]:
        items = cache.get("items", {})
        if not isinstance(items, dict):
            continue
        for native_id, item in items.items():
            content = item.get("content") or {}
            text = content.get("text", "")
            title = content.get("title") or (item.get("feed") or {}).get("title") or ""
            if not text:
                continue
            links = list(content.get("links", []))
            doc_news = set(news_ids(links))
            doc_news.update(str(x) for x in content.get("official_news_ids", []) if str(x).isdigit())
            t_keys = {topic_key(l) for l in links if topic_key(l)}
            parsed = parse_document(text, title)
            documents.append({
                "source": src_name,
                "id": f"{src_name}:{native_id}",
                "native_id": str(native_id),
                "title": title,
                "text": text,
                "url": public_url(content.get("url") or item.get("url") or ""),
                "links": links,
                "topic_keys": t_keys,
                "news_ids": doc_news,
                "claims": parsed["claims"],
                "signals": parsed["signals"],
            })

    # 聚合 claims
    candidates = {}
    for doc in documents:
        for claim in doc["claims"]:
            kind = claim.get("kind")
            if not _is_included(kind, doc["title"]):
                continue
            # 日期不完整且不是直播单点的不收录
            start = claim.get("start")
            end = claim.get("end")
            is_point = False
            time_kind = "range"

            if kind in ("livestream", "version") and start and not end and start.get("precision") != "day":
                is_point = True
                time_kind = "start_only" if kind == "livestream" else "instant"
                end = start
            elif not (start and end and start.get("precision") != "day" and end.get("precision") != "day"):
                continue

            if claim.get("status") not in ("complete", "dated_range", "partial"):
                continue
            if any(claim.get("conditions", {}).values()):
                continue

            # 公告族身份锚点
            anchor = doc["native_id"] if doc["source"] == "official-news" else None
            if not anchor and doc["news_ids"]:
                anchor = sorted(doc["news_ids"])[0]
            family = f"news-{anchor}" if anchor else doc["id"].replace(":", "-")
            cand_id = f"official-{family}-{kind}"

            # 如果该新闻或具体专题已在现有基线日历中，且时间兼容，不重复创建
            if anchor and anchor in existing_news_ids:
                continue
            if doc["topic_keys"] and doc["topic_keys"].intersection(existing_topics):
                # 检查是否已有完全相同日期的现有事件
                continue

            if cand_id not in candidates:
                # 查找最佳展示链接
                cand_url = doc["url"]
                for l in doc["links"]:
                    if topic_key(l):
                        cand_url = public_url(l)
                        break

                candidates[cand_id] = {
                    "id": cand_id,
                    "name": doc["title"],
                    "kind": kind,
                    "start": datetime.fromisoformat(start["value"]),
                    "end": datetime.fromisoformat(end["value"]),
                    "url": cand_url,
                    "details_source": doc["url"],
                    "time_kind": time_kind,
                    "is_point": is_point,
                }

    # 返回排序后的事件列表
    return sorted(candidates.values(), key=lambda x: x["start"])
