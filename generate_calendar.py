import os
import json
import requests
import datetime
import time

from icalendar import Calendar, Event
import pytz


# =========================
# 配置
# =========================

YEAR = 2026

TZ = pytz.timezone(
    "Asia/Shanghai"
)


# 你的 Cloudflare Worker

API_PROXY = (
    "https://ff14-api.eternalphilip.workers.dev"
)


CACHE_FILE = (
    "data/api_cache.json"
)


OUTPUT_FILE = (
    "ff14.ics"
)


# =========================
# 请求API
# =========================


def fetch_api(year, month):

    url = (
        API_PROXY
        +
        f"?month={year}-{month:02d}"
    )


    headers = {

        "User-Agent":
        "Mozilla/5.0",

        "Accept":
        "application/json"

    }


    for retry in range(3):

        try:

            print(
                f"请求API {year}-{month:02d}"
                f" 第{retry+1}次"
            )


            r = requests.get(
                url,
                headers=headers,
                timeout=20
            )


            print(
                "HTTP:",
                r.status_code
            )


            if r.status_code != 200:

                continue



            data = r.json()



            if data.get("code") == 10000:

                return data.get(
                    "data",
                    []
                )


        except Exception as e:

            print(
                "API异常:",
                e
            )


        time.sleep(3)



    return None





# =========================
# 缓存
# =========================


def load_cache():

    if not os.path.exists(
        CACHE_FILE
    ):

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





# =========================
# 获取全年活动
# =========================


def get_api_events():


    cache = load_cache()


    result = []


    for month in range(1,13):


        key = (
            f"{YEAR}-{month:02d}"
        )


        data = fetch_api(
            YEAR,
            month
        )


        if data is None:


            print(
                "API失败 使用缓存:",
                key
            )


            data = cache.get(
                key,
                []
            )


        else:


            cache[key]=data



        result.extend(
            data
        )



    save_cache(
        cache
    )


    print(
        "API活动数量:",
        len(result)
    )


    return result





# =========================
# 人工补充
# =========================


def load_manual():


    file="ff14_override.json"



    if not os.path.exists(
        file
    ):

        return []



    try:


        with open(
            file,
            "r",
            encoding="utf-8"
        ) as f:


            data=json.load(f)



            if isinstance(
                data,
                list
            ):

                return data


    except Exception as e:

        print(
            "人工数据错误:",
            e
        )


    return []





# =========================
# 分类标签
# =========================


def get_category(name):


    if (
        "版本" in name
        or "7." in name
    ):

        return "版本更新"



    if (
        "PLL" in name
        or "Fan" in name
    ):

        return "直播活动"



    if (
        "联动" in name
    ):

        return "联动活动"



    if (
        "月卡" in name
    ):

        return "商城活动"



    if (
        "季节" in name
        or
        name in [
            "红莲节",
            "恋人节",
            "女儿节"
        ]
    ):

        return "季节活动"



    return "其他活动"





# =========================
# 转换
# =========================


def convert(item, source):


    start = datetime.datetime.fromtimestamp(
        item["begin_time"],
        tz=TZ
    )


    end = datetime.datetime.fromtimestamp(
        item["end_time"],
        tz=TZ
    )


    return {

        "uid":
        f"{source}-{item.get('id')}",


        "name":
        item["name"],


        "start":
        start,


        "end":
        end,


        "url":
        item.get(
            "url",
            ""
        ),


        "category":
        get_category(
            item["name"]
        )

    }





# =========================
# 生成ICS
# =========================


def generate():


    events=[]


    # API

    for item in get_api_events():


        events.append(
            convert(
                item,
                "api"
            )
        )



    # 人工

    for item in load_manual():


        events.append(
            convert(
                item,
                "manual"
            )
        )



    # 去重

    unique={}



    for e in events:


        key=(

            e["name"],

            e["start"].isoformat(),

            e["end"].isoformat()

        )


        unique[key]=e





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
        key=lambda x:x["start"]
    ):


        event=Event()


        event.add(
            "uid",
            e["uid"]
        )


        event.add(
            "summary",
            f"[{e['category']}] {e['name']}"
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
            datetime.datetime.now(
                tz=TZ
            )
        )


        if e["url"]:


            event.add(
                "url",
                e["url"]
            )


        event.add(
            "status",
            "CONFIRMED"
        )


        cal.add_component(
            event
        )



    with open(
        OUTPUT_FILE,
        "wb"
    ) as f:


        f.write(
            cal.to_ical()
        )


    print(
        "完成:",
        OUTPUT_FILE,
        "事件:",
        len(unique)
    )




if __name__=="__main__":

    generate()