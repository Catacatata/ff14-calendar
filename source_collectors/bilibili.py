"""B站官方动态增量采集。只采公开内容，不解析活动日期，不创建日程。"""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit

from .common import SourceError, extract_links, finish_status, news_ids, public_url, record_content, safe_error
from .bilibili_auth import credential_values

UID = 6655514
SUPPORTED = {"DYNAMIC_TYPE_WORD", "DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_ARTICLE"}


def clean_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\u200b-\u200d\ufeff]", "", text).strip()


def text_fragments(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("words", "text") and isinstance(child, str):
                yield child
            elif isinstance(child, (list, dict)):
                yield from text_fragments(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_fragments(child)


def opus_links(value):
    """读取图文富文本节点的链接目标，不用“网页链接”等显示文字猜 URL。"""
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("jump_url", "link_url", "href") and isinstance(child, str):
                result.append(child)
            elif key == "link" and isinstance(child, dict):
                result.append(child.get("url", ""))
            if isinstance(child, (dict, list)):
                result.extend(opus_links(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(opus_links(child))
    return result


def image_urls(value):
    urls = []
    def walk(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in ("url", "src") and isinstance(child, str):
                    url = public_url(child)
                    host = urlsplit(url).hostname or ""
                    if host == "hdslb.com" or host.endswith(".hdslb.com"):
                        urls.append(url)
                elif isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return list(dict.fromkeys(urls))[:30]


def parse_feed_item(item):
    if not isinstance(item, dict) or not re.fullmatch(r"\d{1,24}", str(item.get("id_str", ""))):
        raise SourceError("动态缺少有效 ID")
    modules = item.get("modules", {})
    author = modules.get("module_author", {})
    if str(author.get("mid")) != str(UID):
        raise SourceError("动态列表作者不是目标官方账号")
    dynamic = modules.get("module_dynamic") or {}
    major = dynamic.get("major") or {}
    summary = (major.get("opus") or {}).get("summary") or {}
    description = dynamic.get("desc") or summary
    text = clean_text(description.get("text", ""))
    links = extract_links(text, [node.get("jump_url", "")
                                for node in description.get("rich_text_nodes", []) if isinstance(node, dict)])
    timestamp = author.get("pub_ts")
    try:
        published = datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        published = None
    content = {"text": text, "title": clean_text((major.get("opus") or {}).get("title", "")),
               "images": image_urls(major), "links": links}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    tag = modules.get("module_tag") or {}
    return {"id": str(item["id_str"]), "author_uid": str(UID),
            "dynamic_type": str(item.get("type", "")), "published_at": published,
            "is_pinned": bool(author.get("is_top")) or "置顶" in str(tag.get("text", "")),
            "url": f'https://t.bilibili.com/{item["id_str"]}',
            "feed": content, "feed_hash": digest}


def parse_opus(data, expected_id):
    if not isinstance(data, dict) or data.get("fallback"):
        raise SourceError("图文正文不可用")
    item = data.get("item") or {}
    if str(item.get("id_str")) != str(expected_id) or str((item.get("basic") or {}).get("uid")) != str(UID):
        raise SourceError("图文 ID 或作者校验失败")
    modules = item.get("modules", [])
    content = [module["module_content"] for module in modules
               if isinstance(module, dict) and module.get("module_content")]
    # 同一段的节点直接拼接，避免将拆分的新闻链接、日期插入换行。
    paragraphs = [paragraph for block in content for paragraph in block.get("paragraphs", [])]
    text = clean_text("\n".join("".join(text_fragments(paragraph)) for paragraph in paragraphs))
    if not text or len(text) > 100000:
        raise SourceError("图文正文为空或超出采集上限")
    links = extract_links(text, opus_links(content))
    titles = [module["module_title"].get("text", "") for module in modules
              if isinstance(module, dict) and isinstance(module.get("module_title"), dict)]
    return {"title": clean_text(" ".join(titles)), "text": text,
            "links": links, "official_news_ids": news_ids(links), "images": image_urls(content)}


class BilibiliClient:
    def __init__(self, auth="authenticated"):
        from bilibili_api import Credential, opus, user, select_client, request_settings
        values = credential_values(auth)
        select_client("curl_cffi")
        request_settings.set("impersonate", "chrome131")
        credential = Credential(**values) if values else None
        self.account = user.User(UID, credential=credential)
        self.credential = credential
        self.opus_class = opus.Opus

    async def feed(self, offset=""):
        return await self.account.get_dynamics_new(offset=offset)

    async def detail(self, dynamic_id):
        return await self.opus_class(int(dynamic_id), credential=self.credential).get_info()


def due(last_attempt, now, hours):
    try:
        return (datetime.fromisoformat(now) - datetime.fromisoformat(last_attempt)).total_seconds() >= hours * 3600
    except (ValueError, TypeError):
        return True


async def collect(cache, client, now, max_pages=4, max_details=20, budget_seconds=180):
    """有限最新页扫描 + 持久续扫队列；不因新缓存遮住原有扫描缺口。"""
    state = copy.deepcopy(cache)
    known = set(cache["items"])
    jobs = state.setdefault("scan_queue", [])
    existing_jobs = len(jobs)
    errors, seen = [], set()
    pages = attempts = 0
    access_denied = False
    started = time.monotonic()
    stop_reason = "page_limit"
    baseline_limited = False

    async def request(awaitable):
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            awaitable.close()
            raise SourceError("本次采集达到时间预算")
        return await asyncio.wait_for(awaitable, min(35, remaining))

    async def scan(job, limit, minimum_overlap):
        nonlocal pages, attempts, access_denied, baseline_limited
        boundary = set(job["boundary_ids"])
        visited = set(job.get("visited_offsets", []))
        overlap_seen = False
        for index in range(limit):
            offset = job["offset"]
            try:
                attempts += 1
                data = await request(client.feed(offset))
                rows = data.get("items") if isinstance(data, dict) else None
                if not isinstance(rows, list) or not rows:
                    raise SourceError("动态列表为空或结构异常，保留已有记录")
                parsed = [parse_feed_item(item) for item in rows]
                pages += 1
                non_pinned = [item["id"] for item in parsed if not item["is_pinned"]]
                for item in parsed:
                    identity = item["id"]
                    old = state["items"].get(identity, {})
                    old.update(item)
                    old.setdefault("first_seen_at", now)
                    old["last_seen_at"] = now
                    old.setdefault("content_state", "pending" if item["dynamic_type"] in SUPPORTED else "unsupported")
                    state["items"][identity] = old
                    seen.add(identity)
                if not data.get("has_more"):
                    return True, "end_of_feed"
                overlap_seen = overlap_seen or any(identity in boundary for identity in non_pinned)
                if index + 1 >= minimum_overlap and overlap_seen:
                    return True, "known_overlap"
                # 第一次建立基线只取有限近期窗口；不是无限回填全部历史。
                # 若中途失败，剩余基线页数也随游标保存，下轮继续。
                if job.get("baseline_pages_left") == 1:
                    job["baseline_pages_left"] = 0
                    baseline_limited = True
                    return True, "initial_history_limit"
                next_offset = str(data.get("offset") or "")
                if not next_offset or len(next_offset) > 256 or next_offset == offset or next_offset in visited:
                    raise SourceError("动态分页游标缺失或重复")
                visited.add(offset)
                job["visited_offsets"] = sorted(visited)
                job["offset"] = next_offset
                if "baseline_pages_left" in job:
                    job["baseline_pages_left"] -= 1
            except Exception as error:
                errors.append({"stage": "feed", **safe_error(error)})
                access_denied = (safe_error(error).get("http_status") in (401, 403, 412, 429)
                                 or getattr(error, "code", None) == -101)
                return False, "feed_error"
        return False, "page_limit"

    # 有旧缺口时给最新页与旧游标分配预算。边界是扫描开始前的 ID 快照，
    # 不能在后续任务中替换为刚抓取的 ID，否则中间遗漏会被新前缀永久遮住。
    head = {"offset": "", "boundary_ids": sorted(known), "started_at": now}
    if not known and not jobs:
        head["baseline_pages_left"] = max_pages
    head_limit = max_pages if not jobs else max(1, max_pages // 2)
    complete, stop_reason = await scan(head, head_limit, min(2, head_limit))
    if not complete and not any(job["offset"] == head["offset"]
                                and job["boundary_ids"] == head["boundary_ids"]
                                and job.get("baseline_pages_left") == head.get("baseline_pages_left")
                                for job in jobs):
        jobs.append(head)
    for _ in range(existing_jobs):
        if attempts >= max_pages or access_denied or time.monotonic() - started >= budget_seconds:
            break
        job = jobs.pop(0)
        complete, reason = await scan(job, max_pages - attempts, 1)
        if not complete:
            jobs.insert(0, job)
            break
    state["scan_queue"] = jobs

    # 有完整旧正文也可能有更新待补：哈希不一致/未解决失败与 seen 无关。
    targets = []
    cooldown = 0
    for identity, record in state["items"].items():
        if record.get("dynamic_type") not in SUPPORTED:
            continue
        changed = record.get("detail_feed_hash") != record.get("feed_hash")
        pending = record.get("content_state") != "complete"
        needs_detail = (pending or changed or bool(record.get("detail_error"))
                        or (identity in seen and due(record.get("detail_last_attempt_at"), now, 24)))
        if not needs_detail:
            continue
        if record.get("detail_error") and not due(record.get("detail_last_attempt_at"), now, 6):
            cooldown += 1
            continue
        targets.append((0 if pending or changed or record.get("detail_error") else 1,
                        record.get("detail_last_attempt_at") or "", identity))
    targets.sort()
    details = 0
    if not access_denied:
        for _, _, identity in targets[:max_details]:
            if time.monotonic() - started >= budget_seconds:
                errors.append({"stage": "detail", "reason": "本次采集达到时间预算"})
                break
            record = state["items"][identity]
            record["detail_last_attempt_at"] = now
            details += 1
            try:
                content = parse_opus(await request(client.detail(identity)), identity)
                record_content(record, content, now)
                record["detail_feed_hash"] = record["feed_hash"]
            except Exception as error:
                record["detail_error"] = safe_error(error)
                errors.append({"stage": "detail", "id": identity, **safe_error(error)})
                if safe_error(error).get("http_status") in (401, 403, 412, 429) or getattr(error, "code", None) == -101:
                    break
    pending_count = sum(record.get("dynamic_type") in SUPPORTED and
                        (record.get("content_state") != "complete" or bool(record.get("detail_error"))
                         or record.get("detail_feed_hash") != record.get("feed_hash"))
                        for record in state["items"].values())
    return finish_status(state, now, errors, pages_fetched=pages, feed_requests=attempts,
                         details_attempted=details, seen_count=len(seen), new_count=len(seen - known),
                         stop_reason=stop_reason, scan_incomplete=bool(jobs), scan_backlog=len(jobs),
                         coverage_scope="since_initial_recent_window",
                         initial_history_limited=baseline_limited, pending_count=pending_count,
                         cooldown_count=cooldown, deferred_details=max(0, len(targets) - details) + cooldown)
