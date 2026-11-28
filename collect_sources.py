"""多源第一步：仅采集公开来源缓存，不创建或修改正式日程。

默认：登录态 B站增量采集 + 已发现官方新闻的正文补取。
退出码：0=正常；2=来源降级/未完成；1=本地缓存或文件处理错误。
"""
import argparse
import asyncio
import copy
import json
from pathlib import Path

from source_collectors import bilibili, official_news
from source_collectors.common import finish_status, load_cache, news_ids, safe_error, save_cache, utc_now

ROOT = Path(__file__).resolve().parent


def discovered_news(cache):
    """只信任官方作者记录，合并正文 ID 与列表中的链接发现。"""
    identities = set()
    for record in cache["items"].values():
        if str(record.get("author_uid")) != str(bilibili.UID):
            continue
        content = record.get("content") or {}
        feed = record.get("feed") or {}
        if isinstance(content, dict):
            values = content.get("official_news_ids", [])
            if isinstance(values, list):
                identities.update(str(value) for value in values
                                  if str(value).isdigit() and len(str(value)) <= 12)
        if isinstance(feed, dict) and isinstance(feed.get("links"), list):
            # Malformed optional discovery fields must not block explicit news IDs.
            for link in feed["links"]:
                if isinstance(link, str):
                    try:
                        identities.update(news_ids([link]))
                    except ValueError:
                        continue
    return identities


async def run(args):
    now = utc_now()
    output = Path(args.output_dir)
    selected = []
    local_error = False

    def report_file_error(source, stage, error):
        nonlocal local_error
        local_error = True
        print(json.dumps({"source": source, "stage": stage, **safe_error(error)}, ensure_ascii=False))

    def read(source, filename):
        try:
            return load_cache(output / filename, source)
        except Exception as error:
            report_file_error(source, "load", error)
            # Never replace a damaged/unreadable cache with an empty cache.
            return None

    def save(source, filename, cache):
        selected.append(cache)
        try:
            save_cache(output / filename, cache)
        except Exception as error:
            report_file_error(source, "save", error)

    # In news-only mode this cache is optional discovery input, never a write target.
    bili_cache = read("bilibili", "bilibili.json")
    if args.source in ("all", "bilibili") and bili_cache is not None:
        try:
            client = bilibili.BilibiliClient(auth=args.auth)
        except Exception as error:
            bili_cache = finish_status(bili_cache, now, [{"stage": "setup", **safe_error(error)}])
        else:
            try:
                bili_cache = await bilibili.collect(copy.deepcopy(bili_cache), client, now,
                                                    max_pages=args.max_pages,
                                                    max_details=args.max_details, budget_seconds=180)
            except Exception as error:
                bili_cache = finish_status(bili_cache, now, [{"stage": "collect", **safe_error(error)}])
        save("bilibili", "bilibili.json", bili_cache)

    if args.source in ("all", "official-news"):
        news_cache = read("official-news", "official_news.json")
        if news_cache is not None:
            identities = set(args.news_id or [])
            if bili_cache is not None:
                identities.update(discovered_news(bili_cache))
            try:
                client = official_news.NewsClient()
            except Exception as error:
                news_cache = finish_status(news_cache, now, [{"stage": "setup", **safe_error(error)}])
            else:
                try:
                    news_cache = official_news.collect(copy.deepcopy(news_cache), client, sorted(identities), now)
                except Exception as error:
                    news_cache = finish_status(news_cache, now, [{"stage": "collect", **safe_error(error)}])
                finally:
                    try:
                        client.close()
                    except Exception as error:
                        errors = list(news_cache.get("status", {}).get("errors", []))
                        news_cache = finish_status(news_cache, now, errors + [{"stage": "close", **safe_error(error)}])
            save("official-news", "official_news.json", news_cache)

    for cache in selected:
        print(json.dumps({"source": cache["source"], "item_count": len(cache["items"]),
                          "status": cache["status"], "calendar_files_modified": False}, ensure_ascii=False))
    if local_error:
        return 1
    return 2 if any(cache["status"].get("outcome") != "ok" for cache in selected) else 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("all", "bilibili", "official-news"), default="all")
    parser.add_argument("--auth", choices=("authenticated", "anonymous"), default="authenticated")
    parser.add_argument("--news-id", action="append", help="可重复，按官方新闻 ID 补取正文；不会生成日程")
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "sources"))
    parser.add_argument("--max-pages", type=int, choices=range(2, 7), default=4)
    parser.add_argument("--max-details", type=int, choices=range(1, 31), default=20)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parse_args())))
    except Exception as error:
        # Never print raw exceptions, request/response bodies, or credentials.
        print(json.dumps({"stage": "local_processing", **safe_error(error)}, ensure_ascii=False))
        raise SystemExit(1)
