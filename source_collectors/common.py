"""公开来源缓存与状态工具。仅保存白名单字段，不保存原始请求/响应对象。"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class SourceError(Exception):
    """仅使用代码内固定错误文字，不拼接凭据或响应正文。"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def new_cache(source):
    return {"schema_version": 1, "source": source, "status": {}, "items": {}}


def load_cache(path, source):
    path = Path(path)
    if not path.exists():
        return new_cache(source)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("source") != source or not isinstance(data.get("items"), dict)
                or not isinstance(data.get("status"), dict)
                or any(not isinstance(item, dict) for item in data["items"].values())):
            raise SourceError("来源缓存结构不正确，停止写入以保留原文件")
        return data
    except (ValueError, OSError):
        raise SourceError("来源缓存无法读取，停止写入以保留原文件") from None


def save_cache(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_error(error):
    result = {"error_type": type(error).__name__}
    if isinstance(error, SourceError):
        result["reason"] = str(error)
    for attribute, key in (("status", "http_status"), ("code", "api_code")):
        value = getattr(error, attribute, None)
        if type(value) is int:
            result[key] = value
    # requests 的 HTTPError 使用 response.status_code。
    response = getattr(error, "response", None)
    if response is not None and type(getattr(response, "status_code", None)) is int:
        result["http_status"] = response.status_code
    return result


def finish_status(cache, now, errors, **metrics):
    """分别表达请求结果、覆盖完整性和持久错误；冷却/限额不等于恢复。"""
    old = cache.get("status", {})
    # setup/顶层异常也必须保留缓存里尚未完成的覆盖/正文事实。
    metrics.setdefault("scan_incomplete", bool(cache.get("scan_queue")))
    metrics.setdefault("pending_count", sum(
        record.get("content_state") == "pending" or bool(record.get("detail_error"))
        or (record.get("content_state") != "unsupported" and bool(record.get("feed_hash"))
            and record.get("detail_feed_hash") != record.get("feed_hash"))
        for record in cache["items"].values()))
    performed = bool(metrics.get("pages_fetched", 0) or metrics.get("details_attempted", 0))
    unresolved = sum(bool(record.get("detail_error")) for record in cache["items"].values())
    retained_failure = not performed and not errors and old.get("outcome") == "degraded"
    incomplete = bool(metrics.get("scan_incomplete") or metrics.get("pending_count", 0)
                      or metrics.get("deferred_details", 0))
    outcome = "degraded" if errors or unresolved or retained_failure else "incomplete" if incomplete else "ok"
    failures = old.get("consecutive_failures", 0)
    if errors:
        failures += 1
    elif outcome == "ok" and performed:
        failures = 0
    status = {
        "outcome": outcome,
        "request_outcome": "degraded" if errors else "ok" if performed else "skipped",
        "last_attempt_at": now,
        "last_success_at": old.get("last_success_at"),
        "consecutive_failures": failures,
        "used_cache": bool(cache["items"] and (outcome != "ok" or not performed)),
        "errors": (errors or (old.get("errors", []) if retained_failure else []))[:30],
        "unresolved_error_count": unresolved,
        "content_complete": not (metrics.get("pending_count", 0) or unresolved),
        "coverage_complete": not bool(metrics.get("scan_incomplete")),
        **metrics,
    }
    if outcome == "ok" and performed:
        status["last_success_at"] = now
    cache["status"] = status
    return cache


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username or parsed.password:
            return ""
        # 去掉跟踪和敏感字段，但保留可能决定页面身份的普通参数及 hash 路由。
        excluded = {"sessdata", "bili_jct", "cookie", "token", "access_token", "refresh_token", "ac_time_value", "authorization", "spm_id_from", "spm_id"}
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                           if key.lower() not in excluded and not key.lower().startswith("utm_")])
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    except ValueError:
        return ""


def extract_links(text, extra=()):
    values = re.findall(r'https?://[^\s<>"\'☆]+', text)
    values.extend(extra)
    result = []
    for value in values:
        link = public_url(value.rstrip("。；，、）)")) if isinstance(value, str) else ""
        if link and link not in result:
            result.append(link)
    return result[:100]


def news_ids(links):
    result = set()
    for link in links:
        parsed = urlsplit(link)
        if parsed.hostname == "ff.web.sdo.com":
            match = re.search(r"(?:^|/)newstab/newscont/(\d+)(?:$|[/?])", parsed.fragment)
            if match and len(match.group(1)) <= 12:
                result.add(match.group(1))
    return sorted(result)


def record_content(record, payload, now):
    """公开内容更新：保留最多五份旧值，忽略抓取时间变化。"""
    payload = copy.deepcopy(payload)
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if record.get("content_hash") and record["content_hash"] != digest:
        history = list(record.get("previous_versions", []))
        history.append({"content_hash": record["content_hash"],
                        "replaced_at": now, "content": record.get("content", {})})
        record["previous_versions"] = history[-5:]
        record["revision_count"] = record.get("revision_count", 0) + 1
    record.update(content=payload, content_hash=digest, last_success_at=now,
                  content_state="complete", detail_error=None)
    record.setdefault("first_seen_at", now)
    record.setdefault("revision_count", 0)
    return record
