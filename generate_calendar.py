import os
import json
import requests
import datetime
import time

from icalendar import Calendar, Event
import pytz


# =====================
# 配置
# =====================

YEAR = 2026

TZ = pytz.timezone("Asia/Shanghai")

API_PROXY = "https://ff14-api.eternalphilip.workers.dev"

CACHE_FILE = "data/api_cache.json"

BASE_ICS = "ff14_base_2026.ics"

MANUAL_FILE = "ff14_override.json"

OUTPUT_FILE = "ff14.ics"

JSON_OUTPUT_FILE = "calendar.json"


# =====================
# 工具
# =====================

def clean_url(url):
    if not url:
        return ""

    return (
        str(url)
        .replace("\n", "")
        .replace("\r", "")
        .strip()
    )


def timestamp_to_datetime(ts):
    return datetime.datetime.fromtimestamp(
        ts,
        tz=TZ
    )


# =====================
# API请求
# =====================

def fetch_api(year, month):
    url = f"{API_PROXY}?month={year}-{month:02d}"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    }

    for retry in range(3):
        try:
            print(
                f"请求API {year}-{month:02d} 第{retry + 1}次"
            )

            r = requests.get(
                url,
                headers=headers,
                timeout=20
            )

            print("HTTP:", r.status_code)

            if r.status_code != 200:
                continue

            data = r.json()

            if data.get("code") == 10000:
                return data.get("data", [])

        except Exception as e:
            print("API错误:", e)

        time.sleep(3)

    return None


# =====================
# 缓存
# =====================

def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}

    try:
        with open(
            CACHE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except:
        return {}


def save_cache(data):
    os.makedirs(
        "data",
        exist_ok=True
    )

    with open(
        CACHE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def get_api_events():
    cache = load_cache()
    result = []

    for month in range(1, 13):
        key = f"{YEAR}-{month:02d}"

        data = fetch_api(
            YEAR,
            month
        )

        if data is None:
            print(
                "API失败 使用缓存:",
                key
            )

            data = cache.get(key, [])

        else:
            cache[key] = data

        result.extend(data)

    save_cache(cache)

    print(
        "API活动数量:",
        len(result)
    )

    return result


# =====================
# 基础ICS
# =====================

def load_base_ics():
    if not os.path.exists(BASE_ICS):
        return []

    result = []

    with open(BASE_ICS, "rb") as f:
        cal = Calendar.from_ical(f.read())

    for item in cal.walk("VEVENT"):
        name = str(
            item.get("SUMMARY", "")
        )

        dt = item.get("DTSTART")

        if not dt:
            continue

        start = dt.dt

        dtend = item.get("DTEND")
        end = dtend.dt if dtend else start

        if isinstance(
            start,
            datetime.date
        ) and not isinstance(
            start,
            datetime.datetime
        ):
            start = datetime.datetime.combine(
                start,
                datetime.time()
            )

            start = TZ.localize(start)

        if (
            isinstance(end, datetime.date)
            and not isinstance(end, datetime.datetime)
        ):
            end = datetime.datetime.combine(
                end,
                datetime.time()
            )
            end = TZ.localize(end)

        elif (
            isinstance(end, datetime.datetime)
            and end.tzinfo is None
        ):
            end = TZ.localize(end)

        result.append({
            "id": "base-" + name,
            "name": name,
            "start": start,
            "end": end,
            "url": clean_url(
                item.get("URL", "")
            )
        })

    print(
        "基础ICS:",
        len(result)
    )

    return result


# =====================
# 人工补充
# =====================

def load_manual():
    if not os.path.exists(MANUAL_FILE):
        return []

    try:
        with open(
            MANUAL_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception as e:
        print(
            "人工数据错误:",
            e
        )
        return []


# =====================
# 分类
# =====================

def get_category(name):
    name = str(name or "")

    seasonal = [
        "降神节",
        "降神祭",
        "恋人节",
        "女儿节",
        "彩蛋狩猎",
        "金碟嘉年华",
        "金碟游乐场大庆典",
        "红莲节",
        "新生庆典",
        "守护天节",
        "星芒节"
    ]

    limited_collab = [
        "妖怪手表",
        "勇者斗恶龙X",
        "勇者斗恶龙",
        "星歌异闻",
        "纵使前路狱火焰毒",
        "献给英雄的夜曲",
        "黑色恶魔",
        "雷光降世",
        "糖豆人"
    ]

    long_collab = [
        "牙狼",
        "怪物猎人 世界",
        "怪物猎人世界",
        "怪物猎人 荒野",
        "怪物猎人荒野",
        "魔光键影"
    ]

    if any(word in name for word in seasonal):
        return "季节活动"

    if any(word in name for word in long_collab):
        return "长效联动"

    if (
        any(word in name for word in limited_collab)
        or "联动" in name
    ):
        return "限时联动"

    if "莫古莫古" in name or "大收集" in name:
        return "莫古莫古大收集"

    if "艾欧泽亚通行证" in name or "通行证" in name:
        return "通行证"

    if any(
        word in name
        for word in [
            "PLL",
            "Fan Festival",
            "FANFEST",
            "FanFest",
            "官方转播",
            "直播"
        ]
    ):
        return "直播/FanFest"

    if any(
        word in name
        for word in [
            "月卡",
            "商城",
            "优惠",
            "礼赠",
            "礼包"
        ]
    ):
        return "月卡/商城"

    if "版本" in name or name.startswith("7."):
        return "版本更新"

    return "其他活动"


# =====================
# API转换
# =====================

def convert_api(item):
    return {
        "id": "api-" + str(item["id"]),
        "name": item["name"],
        "start": timestamp_to_datetime(
            item["begin_time"]
        ),
        "end": timestamp_to_datetime(
            item["end_time"]
        ),
        "url": clean_url(
            item.get("url", "")
        )
    }


# =====================
# 主生成
# =====================

def generate():
    events = []

    events.extend(
        load_base_ics()
    )

    for item in get_api_events():
        events.append(
            convert_api(item)
        )

    events.extend(
        load_manual()
    )

    unique = {}

    for e in events:
        key = (
            e["name"],
            e["start"].isoformat()
        )

        unique[key] = e

    print(
        "最终事件:",
        len(unique)
    )

    cal = Calendar()

    cal.add(
        "prodid",
        "-//FF14 CN Calendar//"
    )

    cal.add(
        "version",
        "2.0"
    )

    cal.add(
        "calscale",
        "GREGORIAN"
    )

    cal.add(
        "x-wr-calname",
        "FF14国服活动日历"
    )

    cal.add(
        "x-wr-timezone",
        "Asia/Shanghai"
    )

    for e in sorted(
        unique.values(),
        key=lambda x: x["start"]
    ):
        event = Event()

        event.add(
            "uid",
            e["id"]
        )

        event.add(
            "summary",
            f"[{get_category(e['name'])}] {e['name']}"
        )

        event.add(
            "dtstart",
            e["start"]
        )

        event.add(
            "dtend",
            e["end"]
        )

        event.add(
            "dtstamp",
            datetime.datetime.now(tz=TZ)
        )

        # 不使用URL字段，改用DESCRIPTION。
        if e.get("url"):
            event.add(
                "description",
                "官方活动地址:\n"
                + clean_url(e["url"])
            )

        event.add(
            "status",
            "CONFIRMED"
        )

        event.add(
            "transp",
            "OPAQUE"
        )

        cal.add_component(event)

    # 输出ICS。
    ics = cal.to_ical().decode("utf-8")

    # 去除ICS折行。
    ics = ics.replace("\r\n ", "")

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        f.write(ics)

    now = datetime.datetime.now(tz=TZ)
    web_events = []

    for e in sorted(
        unique.values(),
        key=lambda x: x["start"]
    ):
        start = e["start"]
        end = e.get("end") or start

        if start.tzinfo is None:
            start = TZ.localize(start)

        if end.tzinfo is None:
            end = TZ.localize(end)

        web_events.append({
            "id": str(e.get("id", "")),
            "name": e["name"],
            "category": get_category(e["name"]),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "url": clean_url(
                e.get("url", "")
            )
        })

    payload = {
        "generated_at": now.isoformat(),
        "generated_at_text": now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "timezone": "Asia/Shanghai",
        "event_count": len(web_events),
        "events": web_events
    }

    with open(
        JSON_OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            payload,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("完成:", OUTPUT_FILE)
    print(
        "完成:",
        JSON_OUTPUT_FILE,
        "事件:",
        len(web_events)
    )


if __name__ == "__main__":
    generate()