"""只读取显式指定的 Cookie 字段；不读取浏览器，不刷新或持久化凭据。"""
import os
from .common import SourceError


def credential_values(auth, environ=None):
    if auth == "anonymous":
        return {}
    if auth != "authenticated":
        raise SourceError("不支持的认证模式")
    if environ is None:
        environ = os.environ
    fields = {"sessdata": "BILI_SESSDATA", "bili_jct": "BILI_JCT",
              "dedeuserid": "BILI_DEDEUSERID", "buvid3": "BILI_BUVID3", "buvid4": "BILI_BUVID4"}
    values = {}
    for field, name in fields.items():
        raw = environ.get(name, "")
        if any(character in raw for character in ("\r", "\n", ";")):
            raise SourceError("Secret 格式不正确：只填单个 Cookie 字段的值，不填整段 Cookie 或换行")
        if raw.strip():
            values[field] = raw.strip()
    if not values.get("sessdata"):
        raise SourceError("登录模式缺少 BILI_SESSDATA；请在仓库 Actions Secrets 中配置")
    if values["sessdata"].lower().startswith("sessdata="):
        raise SourceError("BILI_SESSDATA 只填值，不包含 SESSDATA= 前缀")
    return values
