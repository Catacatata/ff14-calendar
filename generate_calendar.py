import requests
import datetime
import json
import os

from icalendar import Calendar, Event
import pytz


API_URL = (
    "https://apiff14risingstones.web.sdo.com/"
    "api/home/active/calendar/getActiveCalendarMonth"
)

TZ = pytz.timezone("Asia/Shanghai")


YEAR = datetime.datetime.now().year


def fetch_month(year, month):

    url = API_URL

    params = {
        "month": f"{year}-{month:02d}"
    }

    try:

        r = requests.get(
            url,
            params=params,
            timeout=10
        )

        data = r.json()

        if data.get("code") == 10000:

            return data.get("data", [])

    except Exception as e:

        print(
            "API错误:",
            e
        )

    return []



def load_override():

    path = "ff14_override.json"

    if not os.path.exists(path):

        return {}

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)



def timestamp_to_datetime(ts):

    return datetime.datetime.fromtimestamp(
        ts,
        tz=TZ
    )



def generate():

    events = {}


    # 获取全年API

    for month in range(1,13):

        print(
            "获取月份:",
            month
        )

        data = fetch_month(
            YEAR,
            month
        )


        for item in data:

            events[item["id"]] = item



    print(
        "API活动数量:",
        len(events)
    )


    override = load_override()



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
        "FF14 国服活动日历"
    )

    cal.add(
        "x-wr-timezone",
        "Asia/Shanghai"
    )



    for eid,item in sorted(
        events.items()
    ):


        event = Event()


        info = override.get(
            str(eid),
            {}
        )


        name = info.get(
            "summary",
            item["name"]
        )


        event.add(
            "summary",
            name
        )


        start = timestamp_to_datetime(
            item["begin_time"]
        )

        end = timestamp_to_datetime(
            item["end_time"]
        )


        event.add(
            "dtstart",
            start
        )

        event.add(
            "dtend",
            end
        )


        event.add(
            "uid",
            f"sdo-{eid}@ff14-calendar"
        )


        event.add(
            "dtstamp",
            datetime.datetime.now(
                tz=TZ
            )
        )


        if item.get("url"):

            event.add(
                "url",
                item["url"]
            )


        event.add(
            "status",
            "CONFIRMED"
        )


        event.add(
            "transp",
            "OPAQUE"
        )


        cal.add_component(
            event
        )



    with open(
        "ff14.ics",
        "wb"
    ) as f:

        f.write(
            cal.to_ical()
        )


    print(
        "生成完成"
    )



if __name__ == "__main__":

    generate()