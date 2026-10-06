"""获取 holiday-cn 数据，保留年度缓存，生成网页使用的 holidays.json。"""

import json
import re
from datetime import date, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "holiday-cn"

SOURCES = [
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master",
    "https://cdn.jsdelivr.net/gh/NateScarlet/holiday-cn@master",
]


def validate(data, year):
    if not isinstance(data, dict):
        raise ValueError("数据不是对象")

    if data.get("year") != year:
        raise ValueError("年份不匹配")

    if not isinstance(data.get("days"), list):
        raise ValueError("缺少 days 数组")

    for item in data["days"]:
        if not isinstance(item, dict):
            raise ValueError("日期记录格式错误")

        date_string = item.get("date", "")

        if not isinstance(date_string, str):
            raise ValueError("日期不是字符串")

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_string):
            raise ValueError("日期格式错误")

        date.fromisoformat(date_string)

        if type(item.get("isOffDay")) is not bool:
            raise ValueError("isOffDay 必须是布尔值")

        if not isinstance(item.get("name"), str):
            raise ValueError("缺少节日名称")

    return data


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_year(year):
    for source in SOURCES:
        url = f"{source}/{year}.json"

        try:
            request = Request(
                url,
                headers={"User-Agent": "FF14-Calendar/1.0"},
            )

            with urlopen(request, timeout=15) as response:
                data = validate(json.load(response), year)

            # 未公布的年份可能是空数组，不覆盖已有非空缓存。
            if data["days"]:
                write_json(CACHE / f"{year}.json", data)

            print(f"holiday-cn {year}：{len(data['days'])} 条记录")
            return

        except Exception as error:
            print(
                f"holiday-cn {year} 获取失败："
                f"{type(error).__name__}，尝试备用源或保留缓存"
            )


def main():
    current_year = datetime.now(ZoneInfo("Asia/Shanghai")).year

    # 同时检查相邻年度公告，覆盖跨年安排。
    for year in range(current_year - 1, current_year + 3):
        fetch_year(year)

    merged = {}

    for path in sorted(CACHE.glob("*.json")):
        try:
            year = int(path.stem)
            data = validate(
                json.loads(path.read_text(encoding="utf-8")),
                year,
            )

            # 较新年度公告优先，可能涉及上一年12月。
            for item in data["days"]:
                merged[item["date"]] = item

        except Exception as error:
            print(
                f"跳过无效缓存 {path.name}："
                f"{type(error).__name__}"
            )

    output = ROOT / "holidays.json"

    if merged:
        write_json(
            output,
            {
                "source": "holiday-cn",
                "days": sorted(
                    merged.values(),
                    key=lambda item: item["date"],
                ),
            },
        )

    elif not output.exists():
        raise RuntimeError("未获取到节假日数据，且没有可用缓存")

    else:
        print("本次未获取到数据，保留原 holidays.json")

    print(f"节假日处理完成，合并缓存记录：{len(merged)} 条")


if __name__ == "__main__":
    main()