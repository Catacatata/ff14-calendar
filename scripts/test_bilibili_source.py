"""B 站来源只读测试：可选 Secrets 登录态，不写入日历、图片或缓存。

运行：python scripts/test_bilibili_source.py --client curl_cffi --auth anonymous
登录态：--auth authenticated（仅从环境变量读取 Cookie 字段，不自动刷新）
依赖：bilibili-api-python==17.4.2，以及所选客户端
客户端版本：aiohttp==3.13.3 / curl_cffi==0.17.0
只验证采集及固定样本，不代表已实现活动日期解析或多源合并。
"""
import argparse
import asyncio
from datetime import datetime, timezone
import importlib.metadata
import json
import os
import re
import sys
import unicodedata

from bilibili_api import Credential, opus, user, select_client, request_settings

UID = 6655514
OPUS_ID = 1274198612158447624
TIMEOUT_SECONDS = 35


class ProbeValidationError(Exception):
    """仅用于本脚本生成的固定诊断信息，可安全写入日志。"""


def emit(name, **fields):
    print(json.dumps({"check": name, **fields}, ensure_ascii=False), flush=True)


def error_diagnostic(error):
    """仅保留固定提示和数值错误码，绝不输出网络异常的原始正文。"""
    if isinstance(error, ProbeValidationError):
        return {"reason": str(error)}
    if isinstance(error, TimeoutError):
        return {"reason": "请求超时", "timeout_seconds": TIMEOUT_SECONDS}
    result = {}
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    if type(status) is int:
        result["http_status"] = status
    if type(code) is int:
        result["api_code"] = code
    if status == 412:
        result["reason"] = "HTTP 412，请求被拒绝；本次不自动重试"
    elif status == 429:
        result["reason"] = "HTTP 429，请求受到限流；本次不重试"
    return result


def build_credential(auth, environ=None):
    """显式选择登录态时才读取环境变量；不接收完整 Cookie 或刷新令牌。"""
    if auth == "anonymous":
        return None
    if auth != "authenticated":
        raise ProbeValidationError("不支持的认证模式")
    if environ is None:
        environ = os.environ
    fields = {
        "sessdata": "BILI_SESSDATA",
        "bili_jct": "BILI_JCT",
        "dedeuserid": "BILI_DEDEUSERID",
        "buvid3": "BILI_BUVID3",
        "buvid4": "BILI_BUVID4",
    }
    values = {}
    for field, name in fields.items():
        raw = environ.get(name, "")
        if any(character in raw for character in ("\r", "\n", ";")):
            raise ProbeValidationError("Secret 格式不正确：只填单个 Cookie 字段的值，不填整段 Cookie 或换行")
        value = raw.strip()
        if value:
            values[field] = value
    if not values.get("sessdata"):
        raise ProbeValidationError("登录模式缺少 BILI_SESSDATA；请在仓库 Actions Secrets 中配置")
    if values["sessdata"].lower().startswith("sessdata="):
        raise ProbeValidationError("BILI_SESSDATA 只填值，不包含 SESSDATA= 前缀")
    return Credential(**values)


def text_fragments(value):
    """只抽取 Opus 文本节点中的文字，不把链接、样式等元数据当正文。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("words", "text") and isinstance(child, str):
                yield child
            elif isinstance(child, (dict, list)):
                yield from text_fragments(child)
    elif isinstance(value, list):
        for child in value:
            yield from text_fragments(child)


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\u200b-\u200d\ufeff]", "", text)


def validate_feed(data):
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ProbeValidationError("动态列表结构不正确")
    items = data["items"]
    if not items:
        raise ProbeValidationError("动态列表为空，不能证明采集正常")
    for item in items:
        if not isinstance(item, dict) or not item.get("id_str"):
            raise ProbeValidationError("动态缺少 ID")
        author = item.get("modules", {}).get("module_author", {})
        if str(author.get("mid")) != str(UID):
            raise ProbeValidationError("动态列表出现非目标账号的作者")
    return items


async def test_feed(credential=None):
    account = user.User(UID, credential=credential)  # 不读取浏览器登录信息。
    first = await asyncio.wait_for(account.get_dynamics_new(), TIMEOUT_SECONDS)
    items = validate_feed(first)
    emit("feed_first_page", status="ok", uid=UID, item_count=len(items),
         has_more=bool(first.get("has_more")),
         sample_ids=[item["id_str"] for item in items[:3]])
    if first.get("has_more"):
        offset = first.get("offset")
        if not offset:
            raise ProbeValidationError("列表标记还有下一页，却没有分页游标")
        second = await asyncio.wait_for(account.get_dynamics_new(offset=str(offset)), TIMEOUT_SECONDS)
        next_items = validate_feed(second)
        old_ids = {item["id_str"] for item in items}
        new_count = sum(item["id_str"] not in old_ids for item in next_items)
        if not new_count:
            raise ProbeValidationError("第二页没有新动态，分页可能失效")
        emit("feed_second_page", status="ok", item_count=len(next_items), new_item_count=new_count)
    else:
        emit("feed_second_page", status="skipped", reason="来源没有下一页")


async def test_opus(credential=None):
    data = await asyncio.wait_for(opus.Opus(OPUS_ID, credential=credential).get_info(), TIMEOUT_SECONDS)
    item = data.get("item", {})
    if str(item.get("basic", {}).get("uid")) != str(UID):
        raise ProbeValidationError("示例图文发布者不是目标官方账号")
    modules = item.get("modules", [])
    content = [module["module_content"] for module in modules
               if isinstance(module, dict) and module.get("module_content")]
    text = normalize("\n".join(text_fragments(content)))
    checks = {
        "event_name": "莫古莫古★大收集" in text,
        "phase": "第2阶段" in text,
        "start_text": "2026年12月1日16:00" in text,
        "end_text": "2027年1月12日15:59" in text,
        "official_news_link": "https://ff.web.sdo.com/web8/index.html#/newstab/newscont/395921" in text,
    }
    emit("opus_content", status="ok" if all(checks.values()) else "failed",
         uid=UID, opus_id=str(OPUS_ID), text_length=len(text), checks=checks)
    if not all(checks.values()):
        raise ProbeValidationError("样本正文不完整或内容已变更，需要检查；不生成任何活动")


async def main(client="aiohttp", auth="anonymous"):
    select_client(client)
    # 使用上游文档支持的配置，不自行实现签名或轮换代理。
    if client == "curl_cffi":
        request_settings.set("impersonate", "chrome131")
    emit("environment", utc_time=datetime.now(timezone.utc).isoformat(),
         python=sys.version.split()[0],
         bilibili_api_python=importlib.metadata.version("bilibili-api-python"),
         client=client,
         client_version=importlib.metadata.version(client),
         browser_profile="chrome131" if client == "curl_cffi" else None,
         mode=auth + "_read_only")
    try:
        credential = build_credential(auth)
    except Exception as error:
        emit("credential_config", status="failed", error_type=type(error).__name__,
             **error_diagnostic(error))
        emit("result", status="failed", failed_checks=["credential_config"],
             calendar_files_modified=False)
        return 1
    emit("credential_config", status="ok", authentication_requested=auth == "authenticated")
    failures = []
    # 列表与正文分别检查，某一项失败也继续给出另一项的结果。
    for name, operation in (("feed", test_feed), ("opus", test_opus)):
        try:
            await operation(credential)
        except Exception as error:
            failures.append(name)
            # 不打印请求头、响应正文或可能包含凭据的异常文本。
            emit(name, status="failed", error_type=type(error).__name__, **error_diagnostic(error))
    emit("result", status="failed" if failures else "ok", failed_checks=failures,
         calendar_files_modified=False)
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", choices=("aiohttp", "curl_cffi"), default="curl_cffi")
    parser.add_argument("--auth", choices=("anonymous", "authenticated"), default="anonymous")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.client, args.auth)))
