"""离线影子候选：公告关联不是独立投票；任何输出都不允许直接导出日历。"""
import copy
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from source_collectors.common import SourceError, news_ids, public_url
from .dates import parse_document

PARSER_VERSION = "shadow-rules-v2"
OFFICIAL_UID = "6655514"
TOPIC_HOSTS = {"actff1.web.sdo.com", "actff14.web.sdo.com", "act.ff.sdo.com"}
PRECISION = {"day": 0, "hour": 1, "minute": 2, "second": 3}


class ShadowError(SourceError):
    """固定诊断，不包含输入正文或网络异常。"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def topic_key(value):
    """仅用具体官方专题路径关联；不拿官网首页、客服、B站首页作为活动身份。"""
    value = public_url(value)
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.hostname not in TOPIC_HOSTS:
        return ""
    path = re.sub(r"/index\.html?$", "/", parsed.path, flags=re.I).rstrip("/")
    if path in ("", "/project"):
        return ""
    return urlunsplit(("https", parsed.hostname, path, parsed.query, ""))


def edition(title):
    """保留阶段/期数差异，绝不把第1阶段与第2阶段按同一专题强行合并。"""
    return tuple(re.findall(r"第\s*([0-9一二三四五六七八九十]+)\s*(阶段|期|站|届)", title))


def _validate_evidence(parsed, text, title):
    for obj in parsed["claims"] + parsed["signals"]:
        evidence = obj["evidence"]
        origin = title if evidence.get("origin") == "title" else text
        if origin[evidence["start"]:evidence["end"]] != evidence["text"]:
            raise ShadowError("解析证据偏移校验失败，停止生成影子输出")


def documents(cache, source):
    docs, skipped = {}, []
    for identity, record in sorted(cache["items"].items()):
        document_id = f"{source}:{identity}"
        reason = None
        if not re.fullmatch(r"\d{1,24}", identity) or str(record.get("id", identity)) != identity:
            reason = "invalid_native_id"
        elif source == "bilibili" and str(record.get("author_uid")) != OFFICIAL_UID:
            reason = "official_author_mismatch"
        payload = record.get("content")
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
            reason = reason or "no_complete_body"
        if reason:
            skipped.append({"document_id": document_id, "reason": reason})
            continue
        text = payload["text"]
        title = str(payload.get("title") or (record.get("feed") or {}).get("title") or "")
        if not title and source == "bilibili":
            title = re.sub(r"#[^#]+#", "", text.splitlines()[0]).strip()[:160]
        parsed = parse_document(text, title)
        _validate_evidence(parsed, text, title)
        feed = record.get("feed") or {}
        links = [value for value in list(payload.get("links") or []) + list(feed.get("links") or [])
                 if isinstance(value, str)]
        linked_news = set(news_ids(links))
        linked_news.update(str(value) for value in payload.get("official_news_ids", [])
                           if str(value).isdigit() and len(str(value)) <= 12)
        stale = (bool(record.get("detail_error")) or record.get("content_state") != "complete"
                 or (source == "bilibili" and record.get("detail_feed_hash") != record.get("feed_hash")))
        url = public_url(record.get("url") or payload.get("url") or "")
        docs[document_id] = {"id": document_id, "source": source, "native_id": identity,
                             "title": title, "url": url, "published_at": record.get("published_at") or payload.get("published_at"),
                             "last_content_success_at": record.get("last_success_at"),
                             "content_hash": record.get("content_hash") or digest(payload),
                             "stale_content": stale, "related_news_ids": sorted(linked_news),
                             "topic_keys": sorted({key for value in links if (key := topic_key(value))}),
                             "claims": parsed["claims"], "signals": parsed["signals"], "notes": parsed["notes"], "_body": text}
    return docs, skipped


def _endpoint_compatible(left, right, boundary=None):
    def normalized(point):
        if point["precision"] == "day":
            return point["value"]
        value = datetime.fromisoformat(point["value"])
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value.astimezone(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    try:
        a, b = normalized(left), normalized(right)
    except (ValueError, TypeError):
        return False
    if "day" in (left["precision"], right["precision"]):
        return a[:10] == b[:10]
    # 公告结束只写到分钟，不把 API 的同一分钟 :59 秒误判成真实延期。
    # 不将这一容差扩展到小时，亦不虚构公告结束秒数。
    if boundary == "end" and "minute" in (left["precision"], right["precision"]) and "hour" not in (left["precision"], right["precision"]):
        return a[:16] == b[:16]
    return a == b


def _existing_matches(candidate, candidate_docs, events):
    topics = {key for doc in candidate_docs for key in doc["topic_keys"]}
    anchor = candidate.get("news_id")
    title_editions = {edition(doc["title"]) for doc in candidate_docs if edition(doc["title"])}
    matches = []
    for event in events:
        if not isinstance(event, dict):
            continue
        bases = []
        details = event.get("details_source")
        if anchor and isinstance(details, str) and anchor in news_ids([details]):
            bases.append("exact_news_id")
        if topic_key(event.get("url") or "") in topics - {""}:
            bases.append("exact_topic_url")
        if not bases:
            continue
        incompatible = bool(edition(str(event.get("name", ""))) and title_editions
                            and edition(str(event.get("name", ""))) not in title_editions)
        matches.append({"id": event.get("id"), "name": event.get("name"), "basis": bases,
                        "edition_compatible": not incompatible, "start": event.get("start"), "end": event.get("end")})
    return matches


def _confirmed_change(source_docs, resolved):
    """旧范围不能因为旁边出现“已延期”就继续被采用。只承认可核对的新安排。"""
    for doc in source_docs:
        changes = [signal for signal in doc["signals"] if signal["type"] == "schedule_change"]
        if not changes:
            continue
        if not resolved["start"] or not resolved["end"]:
            return False
        if re.search(r"具体时间.{0,12}(?:另行|待定|未定)|时间另行|时间待定|另行通知", doc["_body"]):
            return False
        confirmed = False
        for signal in changes:
            if signal["evidence"].get("origin") == "title":
                continue
            text = signal["evidence"]["text"].strip()
            if re.search(r"(?:延长|延期|改期|调整|变更)公告[。!！]?\s*$", text):
                continue
            match = re.search(r"(延长至|延期至|推迟至|改期至|调整至|调整为|变更至|变更为)\s*(.+)", text)
            if not match:
                return False
            value = match.group(2).strip().rstrip("。!！☆★ ")
            extracted = parse_document("活动时间：" + value)["claims"]
            if len(extracted) != 1 or extracted[0]["status"] != "complete":
                return False
            change = extracted[0]
            if match.group(1) == "延长至" and change["start"] and not change["end"]:
                compatible = (PRECISION[change["start"]["precision"]] >= PRECISION[resolved["end"]["precision"]]
                              and _endpoint_compatible(change["start"], resolved["end"], "end"))
            else:
                compatible = bool(change["start"] and change["end"] and
                    _endpoint_compatible(change["start"], resolved["start"], "start") and
                    _endpoint_compatible(change["end"], resolved["end"], "end"))
            if not compatible:
                return False
            confirmed = True
        if not confirmed:
            return False
    return True


def build_report(bili_cache, news_cache, calendar=None, generated_at=None):
    """输入只读；输出原生来源证据、候选与冲突，不调用生成器或网络。"""
    for cache, source in ((bili_cache, "bilibili"), (news_cache, "official-news")):
        if cache.get("source") != source or cache.get("schema_version") != 1 or not isinstance(cache.get("items"), dict):
            raise ShadowError("来源缓存不符合影子解析输入结构")
    calendar = {"events": []} if calendar is None else calendar
    if not isinstance(calendar, dict) or not isinstance(calendar.get("events"), list):
        raise ShadowError("正式日历只读快照结构不正确")
    docs, skipped = {}, []
    for cache, source in ((bili_cache, "bilibili"), (news_cache, "official-news")):
        part, ignored = documents(cache, source)
        docs.update(part)
        skipped.extend(ignored)
    groups = defaultdict(list)
    observations = []
    ambiguous = set()
    for doc in docs.values():
        for index, original in enumerate(doc["claims"], 1):
            claim = copy.deepcopy(original)
            claim.update(id=f'{doc["id"]}#claim-{index}', document_id=doc["id"], stale_content=doc["stale_content"])
            anchor = None
            if doc["source"] == "official-news":
                anchor = doc["native_id"]
            elif len(doc["related_news_ids"]) == 1:
                anchor = doc["related_news_ids"][0]
            elif doc["related_news_ids"]:
                # 多链接可包含制作人来信等相关文，不把它们一股脑做传递合并。
                compatible = [identity for identity in doc["related_news_ids"]
                              if any(other["kind"] == claim["kind"]
                                     for other in docs.get(f"official-news:{identity}", {}).get("claims", []))]
                if len(compatible) == 1:
                    anchor = compatible[0]
                else:
                    ambiguous.add(claim["id"])
            family = f"news:{anchor}" if anchor else doc["id"]
            claim["announcement_family"] = family
            claim["news_id"] = anchor
            groups[(family, claim["kind"])].append(claim)
            observations.append(claim)

    candidates, conflicts = [], []
    attached_notices = set()
    for (family, kind), claims in sorted(groups.items()):
        candidate_id = "shadow-" + family.replace(":", "-") + "-" + kind
        related_docs = {claim["document_id"] for claim in claims}
        anchor = claims[0]["news_id"]
        # 没有日期的明确取消/改期通知，也能在唯一新闻关联下冻结该公告族候选。
        for doc in docs.values():
            identifies_anchor = (doc["source"] == "official-news" and doc["native_id"] == anchor) or doc["related_news_ids"] == [anchor]
            if anchor and identifies_anchor and any(
                    signal["type"] in ("cancellation", "schedule_change") for signal in doc["signals"]):
                related_docs.add(doc["id"])
                attached_notices.add(doc["id"])
        source_docs = [docs[identity] for identity in sorted(related_docs)]
        preferred = next((doc for doc in source_docs if doc["source"] == "official-news"), source_docs[0])
        blocked, warnings, local_conflicts = [], [], []
        if any(claim["id"] in ambiguous for claim in claims):
            blocked.append("ambiguous_news_association")
        if any(doc["stale_content"] for doc in source_docs):
            blocked.append("stale_source_content")
        if any(claim["status"] == "unresolved" for claim in claims):
            blocked.append("unresolved_date_claim")
        signal_types = {signal["type"] for doc in source_docs for signal in doc["signals"]}
        if "cancellation" in signal_types:
            blocked.append("cancellation_notice_not_applied")
        if "schedule_change" in signal_types:
            warnings.append("schedule_change_notice")
        if "disclaimer" in signal_types:
            warnings.append("conditional_disclaimer_not_applied")
        schedules = {}
        for claim in claims:
            key = digest({"start": {key: claim["start"][key] for key in ("value", "precision")} if claim["start"] else None,
                          "end": {key: claim["end"][key] for key in ("value", "precision")} if claim["end"] else None,
                          "conditions": claim["conditions"], "status": claim["status"]})
            schedules.setdefault(key, {"start": claim["start"], "end": claim["end"],
                                       "conditions": claim["conditions"], "status": claim["status"], "observation_ids": []})
            schedules[key]["observation_ids"].append(claim["id"])
        # 同一来源重复文案/跨平台转载都不是投票。只做一致性约束。
        resolved = {}
        for boundary in ("start", "end"):
            endpoints = [claim[boundary] for claim in claims if claim[boundary]]
            if any(not _endpoint_compatible(left, right, boundary) for index, left in enumerate(endpoints) for right in endpoints[index + 1:]):
                local_conflicts.append({"type": boundary + "_mismatch", "observation_ids": [claim["id"] for claim in claims if claim[boundary]]})
            resolved[boundary] = max(endpoints, key=lambda value: PRECISION[value["precision"]]) if endpoints else None
        conditions = {boundary: sorted({claim["conditions"][boundary] for claim in claims if claim["conditions"][boundary]})
                      for boundary in ("start", "end")}
        if any(conditions.values()):
            blocked.append("conditional_boundary")
        if len({(bool(claim["start"]), bool(claim["end"])) for claim in claims}) > 1:
            blocked.append("mixed_schedule_shapes_not_combined")
        if "schedule_change" in signal_types and not _confirmed_change(source_docs, resolved):
            blocked.append("schedule_change_without_confirmed_schedule")
        if resolved["start"] and resolved["end"]:
            length = 10 if "day" in (resolved["start"]["precision"], resolved["end"]["precision"]) else 19
            if resolved["end"]["value"][:length] < resolved["start"]["value"][:length]:
                local_conflicts.append({"type": "combined_reversed_range", "observation_ids": [claim["id"] for claim in claims]})
        if any(edition(doc["title"]) != edition(preferred["title"]) for doc in source_docs if edition(doc["title"]) and edition(preferred["title"])):
            local_conflicts.append({"type": "edition_mismatch", "observation_ids": [claim["id"] for claim in claims]})
        candidate = {"id": candidate_id, "name": preferred["title"], "kind": kind,
                     "announcement_family": family, "news_id": anchor,
                     "document_ids": sorted(related_docs), "observation_ids": [claim["id"] for claim in claims],
                     "independent_origin_count": 1, "document_count": len(source_docs),
                     "proposed_schedules": list(schedules.values()), "conditions": conditions,
                     "warnings": warnings, "blockers": blocked, "calendar_export_allowed": False}
        matches = _existing_matches(candidate, source_docs, calendar["events"])
        candidate["existing_event_matches"] = matches
        compatible_matches = [match for match in matches if match["edition_compatible"]]
        if len(compatible_matches) > 1:
            local_conflicts.append({"type": "multiple_existing_event_matches", "existing_event_ids": [match["id"] for match in compatible_matches]})
        for match in compatible_matches:
            differences = []
            for boundary in ("start", "end"):
                point = resolved[boundary]
                value = match.get(boundary)
                if point and isinstance(value, str):
                    other = {"value": value, "precision": "day" if len(value) == 10 else "second"}
                    compatible = _endpoint_compatible(point, other, boundary)
                    match.setdefault("comparison", {})[boundary] = ("exact" if value == point["value"] else
                        "compatible_at_published_precision" if compatible else "different")
                    if not compatible:
                        differences.append(boundary)
            if differences:
                local_conflicts.append({"type": "existing_calendar_mismatch", "existing_event_id": match["id"], "boundaries": differences,
                                        "action": "not_applied"})
        if any(not match["edition_compatible"] for match in matches):
            warnings.append("existing_event_edition_mismatch_not_linked")
        if local_conflicts:
            candidate["status"] = "conflict"
        elif blocked:
            candidate["status"] = "held"
        elif resolved["start"] and resolved["end"]:
            candidate["status"] = "dated_range"
        elif resolved["start"] or resolved["end"]:
            candidate["status"] = "dated_point" if all(claim["status"] == "complete" for claim in claims) else "partial"
        else:
            candidate["status"] = "held"
        candidate["selected_schedule"] = ({**resolved, "end_semantics": "inclusive_date" if resolved["end"] and resolved["end"]["precision"] == "day" else "as_published"}
                                          if candidate["status"] in ("dated_range", "dated_point", "partial") else None)
        for index, conflict in enumerate(local_conflicts, 1):
            conflicts.append({"id": f"{candidate_id}#conflict-{index}", "candidate_id": candidate_id, **conflict})
        candidate["conflict_ids"] = [conflict["id"] for conflict in conflicts if conflict["candidate_id"] == candidate_id]
        candidates.append(candidate)

    # 专题相同仅作为可能关联；阶段/年份可能不同，不能据此传递合并。
    for index, candidate in enumerate(candidates):
        topics = {key for identity in candidate["document_ids"] for key in docs[identity]["topic_keys"]}
        candidate["possible_related_candidate_ids"] = [other["id"] for j, other in enumerate(candidates) if j != index and topics.intersection(
            key for identity in other["document_ids"] for key in docs[identity]["topic_keys"])]
    input_identity = {"parser_version": PARSER_VERSION, "bilibili": digest(bili_cache), "official-news": digest(news_cache), "calendar": digest(calendar)}
    public_docs = [{key: value for key, value in doc.items() if key not in ("claims", "_body")} for doc in docs.values()]
    return {"schema_version": 1, "parser_version": PARSER_VERSION, "mode": "shadow_only", "snapshot_id": digest(input_identity),
            "generated_at": generated_at, "timezone": "Asia/Shanghai", "calendar_export_allowed": False,
            "source_inputs_complete": all(cache.get("status", {}).get("outcome") == "ok" for cache in (bili_cache, news_cache)),
            "inputs": {"fingerprints": input_identity, "source_status": {"bilibili": copy.deepcopy(bili_cache.get("status", {})),
                       "official-news": copy.deepcopy(news_cache.get("status", {}))}},
            "summary": {"documents_parsed": len(docs), "documents_skipped": len(skipped), "observations": len(observations),
                        "candidates": len(candidates), "conflicts": len(conflicts),
                        "candidate_statuses": dict(sorted((status, sum(candidate["status"] == status for candidate in candidates))
                                                          for status in {candidate["status"] for candidate in candidates}))},
            "documents": public_docs, "skipped_documents": skipped, "observations": observations, "candidates": candidates, "conflicts": conflicts,
            "unattached_notices": [doc["id"] for doc in docs.values() if not doc["claims"] and doc["id"] not in attached_notices
                                   and any(signal["type"] in ("cancellation", "schedule_change") for signal in doc["signals"])]}
