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
        "User-Agent":"Mozilla/5.0",
        "Accept":"application/json"
    }


    for retry in range(3):

        try:

            print(
                f"请求API {year}-{month:02d} 第{retry+1}次"
            )


            r=requests.get(
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


            data=r.json()


            if data.get("code")==10000:

                return data.get(
                    "data",
                    []
                )


        except Exception as e:

            print(
                "API错误:",
                e
            )


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

    cache=load_cache()

    result=[]


    for month in range(1,13):

        key=f"{YEAR}-{month:02d}"


        data=fetch_api(
            YEAR,
            month
        )


        if data is None:

            print(
                "API失败 使用缓存:",
                key
            )

            data=cache.get(
                key,
                []
            )

        else:

            cache[key]=data


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


    result=[]


    with open(
        BASE_ICS,
        "rb"
    ) as f:

        cal=Calendar.from_ical(
            f.read()
        )


    for item in cal.walk("VEVENT"):


        name=str(
            item.get(
                "SUMMARY",
                ""
            )
        )


        dt=item.get("DTSTART")


        if not dt:
            continue


        start=dt.dt


        if isinstance(
            start,
            datetime.date
        ) and not isinstance(
            start,
            datetime.datetime
        ):

            start=datetime.datetime.combine(
                start,
                datetime.time()
            )

            start=TZ.localize(start)



        result.append({

            "id":
            "base-"+name,

            "name":
            name,

            "start":
            start,

            "end":
            start,

            "url":
            clean_url(
                item.get("URL","")
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

    if not os.path.exists(
        MANUAL_FILE
    ):

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

    rules={

        "版本":[
            "7.",
            "版本"
        ],

        "直播":[
            "PLL",
            "Fan"
        ],

        "联动":[
            "联动"
        ],

        "商城":[
            "月卡",
            "优惠"
        ],

        "季节":[
            "红莲",
            "恋人",
            "女儿",
            "金碟",
            "猎蛋",
            "降神"
        ]
    }


    for c,words in rules.items():

        for w in words:

            if w in name:

                return c


    return "其他"




# =====================
# API转换
# =====================

def convert_api(item):

    return {

        "id":
        "api-"+str(item["id"]),

        "name":
        item["name"],

        "start":
        timestamp_to_datetime(
            item["begin_time"]
        ),

        "end":
        timestamp_to_datetime(
            item["end_time"]
        ),

        "url":
        clean_url(
            item.get("url","")
        )

    }





# =====================
# 主生成
# =====================

def generate():


    events=[]


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



    unique={}


    for e in events:

        key=(

            e["name"],

            e["start"].isoformat()

        )

        unique[key]=e




    print(
        "最终事件:",
        len(unique)
    )



    cal=Calendar()

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
            datetime.datetime.now(
                tz=TZ
            )
        )



        # 不使用URL字段
        # 改用DESCRIPTION

        if e.get("url"):

            event.add(
                "description",
                "官方活动地址:\n"
                +
                clean_url(e["url"])
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



    # 输出
    ics=cal.to_ical().decode(
        "utf-8"
    )


    # 去除ICS折行
    ics=ics.replace(
        "\r\n ",
        ""
    )



    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(ics)



    print(
        "完成:",
        OUTPUT_FILE
    )




if __name__=="__main__":

    generate() 