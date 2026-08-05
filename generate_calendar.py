import os
import json
import requests
import datetime
import time

from icalendar import Calendar, Event
import pytz


TZ = pytz.timezone("Asia/Shanghai")


API_CACHE = "data/api_cache.json"


OUTPUT = "ff14.ics"


YEAR = 2026


HEADERS = {

    "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",

    "Accept":
        "application/json,text/plain,*/*",

    "Referer":
        "https://ff.web.sdo.com/",

    "Origin":
        "https://ff.web.sdo.com"

}



# -------------------------
# API获取
# -------------------------

def fetch_api(year, month):

    url = (
        "https://apiff14risingstones.web.sdo.com/api/home/"
        "active/calendar/getActiveCalendarMonth"
        f"?month={year}-{month:02d}"
    )


    for retry in range(3):

        try:

            print(
                f"请求API {year}-{month:02d}"
                f" 第{retry+1}次"
            )


            r = requests.get(
                url,
                headers=HEADERS,
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
                "API错误:",
                e
            )


        time.sleep(3)


    return None





# -------------------------
# 读取缓存
# -------------------------

def load_cache():

    if not os.path.exists(API_CACHE):

        return {}


    with open(
        API_CACHE,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)





def save_cache(data):

    os.makedirs(
        "data",
        exist_ok=True
    )


    with open(
        API_CACHE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )





# -------------------------
# 获取全年API
# -------------------------

def get_api_events():

    cache = load_cache()


    result = []


    for month in range(1,13):

        key=f"{YEAR}-{month:02d}"


        data = fetch_api(
            YEAR,
            month
        )


        if data is None:

            print(
                "API失败，使用缓存:",
                key
            )

            data = cache.get(
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





# -------------------------
# 人工补充
# -------------------------

def load_override():

    file="ff14_override.json"


    if not os.path.exists(file):

        return []


    with open(
        file,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)





# -------------------------
# 转换事件
# -------------------------

def make_event(item, source):


    start=datetime.datetime.fromtimestamp(
        item["begin_time"],
        tz=TZ
    )


    end=datetime.datetime.fromtimestamp(
        item["end_time"],
        tz=TZ
    )


    return {

        "id":
            f"{source}-{item.get('id')}",


        "summary":
            item["name"],


        "start":
            start,


        "end":
            end,


        "url":
            item.get("url",""),


        "source":
            source
    }





# -------------------------
# 生成ICS
# -------------------------

def generate():


    events=[]


    api=get_api_events()


    for e in api:

        events.append(
            make_event(
                e,
                "api"
            )
        )



    manual=load_override()


    for e in manual:

        events.append(
            make_event(
                e,
                "manual"
            )
        )



    # 去重

    result={}


    for e in events:

        key=(

            e["summary"],

            e["start"].isoformat(),

            e["end"].isoformat()

        )


        result[key]=e



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
        "x-wr-calname",
        "FF14国服活动日历"
    )


    cal.add(
        "x-wr-timezone",
        "Asia/Shanghai"
    )



    for e in sorted(
        result.values(),
        key=lambda x:x["start"]
    ):


        event=Event()


        event.add(
            "uid",
            e["id"]
        )


        event.add(
            "summary",
            e["summary"]
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
        OUTPUT,
        "wb"
    ) as f:

        f.write(
            cal.to_ical()
        )


    print(
        "完成:",
        OUTPUT,
        "事件:",
        len(result)
    )





if __name__=="__main__":

    generate()