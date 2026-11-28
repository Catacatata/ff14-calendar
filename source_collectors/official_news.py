"""官方新闻正文采集。接口来自官网新闻组件实测，不推断活动日期。"""
import copy
from datetime import datetime, timedelta, timezone
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from .common import SourceError, extract_links, finish_status, public_url, record_content, safe_error

DETAIL_API = "https://cqnews.web.sdo.com/api/news/newsDetail"


def parse_detail(payload, expected_id):
    if not isinstance(payload, dict) or str(payload.get("Code")) != "0" or not isinstance(payload.get("Data"), dict):
        raise SourceError("官方新闻正文返回结构或业务码异常")
    data = payload["Data"]
    if str(data.get("Id")) != str(expected_id):
        raise SourceError("官方新闻 ID 不匹配")
    html = data.get("Content")
    if not isinstance(html, str) or not html.strip():
        raise SourceError("官方新闻正文为空，保留已有记录")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    if not text:
        raise SourceError("官方新闻没有可读正文")
    links = extract_links(text, [urljoin("https://ff.web.sdo.com/", a.get("href", "")) for a in soup.find_all("a")])
    outlink = public_url(data.get("OutLink") or "")
    if outlink and outlink not in links:
        links.append(outlink)
    images = [public_url(urljoin("https://ff.web.sdo.com/", image.get("src", ""))) for image in soup.find_all("img")]
    home_image = public_url(urljoin("https://ff.web.sdo.com/", data.get("HomeImagePath") or ""))
    if data.get("HomeImagePath") and home_image:
        images.append(home_image)
    try:
        published = datetime.strptime(data.get("PublishDate", ""), "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=8))).isoformat()
    except (ValueError, TypeError):
        published = None
    topics = [link for link in links if (urlsplit(link).hostname or "") in
              {"actff1.web.sdo.com", "actff14.web.sdo.com", "act.ff.sdo.com"}]
    return {"title": str(data.get("Title") or ""), "text": text, "published_at": published,
            "url": f"https://ff.web.sdo.com/web8/index.html#/newstab/newscont/{expected_id}",
            "links": links, "topic_links": topics, "images": list(dict.fromkeys(image for image in images if image))[:30]}


class NewsClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FF14Calendar/1.0 (public news reader)",
                                     "Referer": "https://ff.web.sdo.com/web8/index.html"})

    def detail(self, identity, timeout=20):
        with self.session.get(DETAIL_API, params={"gameCode": "ff", "id": identity},
                              timeout=(5, timeout), stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2 * 1024 * 1024:
                    raise SourceError("官方新闻响应超出大小上限")
                chunks.append(chunk)
        import json
        return json.loads(b"".join(chunks))

    def close(self):
        self.session.close()


def collect(cache, client, identities, now, max_details=10, budget_seconds=60):
    state = copy.deepcopy(cache)
    for identity in identities:
        identity = str(identity)
        if not identity.isdigit() or len(identity) > 12:
            raise SourceError("官方新闻 ID 格式不正确")
        state["items"].setdefault(identity, {"id": identity, "first_seen_at": now, "content_state": "pending"})
    # 未取得正文优先；定期复查已有正文。限制请求量，不抓整站。
    from .bilibili import due
    cooldown = sum(bool(record.get("detail_error")) and not due(record.get("detail_last_attempt_at"), now, 6)
                   for record in state["items"].values())
    targets = [identity for identity, record in state["items"].items()
               if due(record.get("detail_last_attempt_at"), now, 6 if record.get("detail_error") else 24)]
    targets.sort(key=lambda identity: (state["items"][identity].get("detail_last_attempt_at") or "", identity))
    errors, attempted = [], 0
    started = time.monotonic()
    for identity in targets[:max_details]:
        remaining = budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            errors.append({"stage": "detail", "reason": "本次新闻采集达到时间预算"})
            break
        record = state["items"][identity]
        record["detail_last_attempt_at"] = now
        attempted += 1
        try:
            record_content(record, parse_detail(client.detail(identity, timeout=min(20, remaining)), identity), now)
        except Exception as error:
            record["detail_error"] = safe_error(error)
            errors.append({"stage": "detail", "id": identity, **safe_error(error)})
            if (safe_error(error).get("http_status")) in (401, 403, 412, 429):
                break
    return finish_status(state, now, errors, details_attempted=attempted, cooldown_count=cooldown,
                         deferred_details=max(0, len(targets) - attempted) + cooldown,
                         pending_count=sum(record.get("content_state") != "complete" or bool(record.get("detail_error"))
                                           for record in state["items"].values()))
