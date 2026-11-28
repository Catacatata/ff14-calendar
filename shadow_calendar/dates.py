"""Conservative labelled-date extraction, using only the Python standard library.

Offsets are Python character offsets in the original body (or title for title
signals). No clock, publication metadata, network, or filesystem is consulted.
Unknown syntax is retained as an unresolved claim, not partially accepted.
"""

import re
from datetime import date, datetime, timedelta, timezone

_LABELS = {
    "活动时间": "activity", "预计维护时间": "maintenance", "维护时间": "maintenance",
    "直播时间": "livestream", "更新时间": "version", "版本更新时间": "version", "版本上线时间": "version",
    "上架时间": "sale", "报名时间": "registration",
    "兑换时间": "redemption", "优惠券使用期限": "coupon_use",
    "典藏包CDK限时折扣时间": "sale_discount", "结果公布时间": "results", "结果公布": "results",
}
_LABEL = re.compile(r"^[\s※☆★【\[]*(" + "|".join(sorted(_LABELS, key=len, reverse=True)) + r")(?=[:：\s】\]]|$)[】\]]*\s*[:：]?[ \t]*", re.I)
_TRANS = str.maketrans("０１２３４５６７８９：／－～（）", "0123456789:/-~()")
_DATE = r"(?:(?P<y>\d{4})年)?(?P<mo>\d{1,2})月(?P<d>\d{1,2})日|(?:(?P<yn>\d{4})[/-])?(?P<mn>\d{1,2})[/-](?P<dn>\d{1,2})|(?P<day>\d{1,2})日"
_TIME = r"(?P<h>\d{1,2})(?::(?P<mi>\d{2})(?::(?P<s>\d{2}))?|[点时](?:(?P<mc>\d{1,2})分(?:(?P<sc>\d{1,2})秒)?)?)"
_ENDPOINT = re.compile(r"(?:(?:" + _DATE + r")\s*(?:" + _TIME + r")?|" + _TIME.replace("?P<", "?P<t_") + r")")
_SEP = re.compile(r"\s*(?:~|至|—+|–|-)\s*")
_ZONE = re.compile(r"日本时间|日本時間|东京时间|東京時間|(?<![A-Za-z])(?:UTC|GMT|JST|KST|PST|PDT|EST|EDT|CET|CEST)(?![A-Za-z])|英国时间|美国时间|韩国时间|太平洋时间|东部时间|\+\d{2}:?\d{2}|国际服时间", re.I)
_CONDITION = re.compile(r"(?:即刻起|即日起|即日起生效|上架后|上线后|.*版本.*维护(?:开始|结束)?前)")
_TZ = timezone(timedelta(hours=8))


def _evidence(text, start, end, origin="body"):
    result = {"text": text[start:end], "start": start, "end": end}
    if origin != "body":
        result["origin"] = origin
    return result


def _endpoint(raw, previous=None):
    """Return endpoint, error; inheritance is allowed only for range RHS."""
    normalized = raw.translate(_TRANS).strip()
    match = _ENDPOINT.fullmatch(normalized)
    if not match:
        return None, "unsupported_format"
    g = match.groupdict()
    year = g["y"] or g["yn"]
    month = g["mo"] or g["mn"]
    day = g["d"] or g["dn"] or g["day"]
    h = g["h"] or g["t_h"]
    minute = g["mi"] or g["mc"] or g["t_mi"] or g["t_mc"]
    second = g["s"] or g["sc"] or g["t_s"] or g["t_sc"]
    explicit_year, explicit_month, explicit_day = bool(year), bool(month), bool(day)
    basis = "explicit"
    if not year:
        if previous is None:
            return None, "missing_year"
        base = datetime.fromisoformat(previous["value"]) if previous["precision"] != "day" else datetime.combine(date.fromisoformat(previous["value"]), datetime.min.time(), _TZ)
        year = base.year
        month = int(month) if month else base.month
        day = int(day) if day else base.day
        basis = "range_inherited"
    if not month or not day:
        return None, "unsupported_format"
    year, month, day = int(year), int(month), int(day)
    try:
        # Compare components before constructing a date: a February 29 RHS
        # may become valid only after the inherited year rolls into a leap year.
        if previous is not None and not explicit_year:
            prior = date.fromisoformat(previous["value"][:10])
            if (year, month, day) < (prior.year, prior.month, prior.day):
                if explicit_month:
                    year += 1
                elif explicit_day:
                    month += 1
                    year += month > 12
                    month = (month - 1) % 12 + 1
                basis = "range_rollover"
        d = date(year, month, day)
        precision = "day"
        normalizations = []
        if h is not None:
            hh, mm, ss = int(h), int(minute or 0), int(second or 0)
            if mm > 59 or ss > 59 or hh > 24 or (hh == 24 and (mm or ss)):
                return None, "invalid_time"
            precision = "second" if second is not None else "minute" if minute is not None else "hour"
            if hh == 24:
                d += timedelta(days=1)
                hh = 0
                normalizations.append("midnight_24_to_next_day")
            value = datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=_TZ)
            if previous and not explicit_day and not explicit_year and previous["precision"] != "day" and value < datetime.fromisoformat(previous["value"]):
                value += timedelta(days=1)
                basis = "range_rollover"
            value = value.isoformat(timespec="seconds")
        else:
            value = d.isoformat()
    except (ValueError, OverflowError):
        return None, "invalid_date"
    result = {"value": value, "precision": precision, "basis": basis, "raw": raw}
    if normalizations:
        result["normalizations"] = normalizations
    return result, None


def _parse_value(value, claim):
    value = value.strip().strip("☆★").strip()
    normalized = value.translate(_TRANS)
    if _ZONE.search(normalized):
        claim["warnings"].append("unsupported_timezone")
        return
    # Beijing is the only accepted explicit timezone. Preserve date raw strings.
    value = re.sub(r"^(?:北京时间|中国标准时间)\s*", "", value)
    normalized = value.translate(_TRANS)
    if not normalized:
        claim["warnings"].append("missing_date")
        return
    if re.search(r"每周|每星期|每月|每天", normalized):
        claim["warnings"].append("recurring_only")
        return
    first = _ENDPOINT.match(normalized)
    if first:
        pos = first.end()
        separator = _SEP.match(normalized, pos)
        if separator:
            left, right = value[:pos].strip(), value[separator.end():].strip()
        elif pos == len(normalized):
            left, right = value, None
        else:
            claim["warnings"].append("unsupported_format")
            return
    else:
        conditional = re.match(r"^(即刻起|即日起|上架后|上线后)\s*(?:~|至|—+|-)\s*", normalized)
        if not conditional:
            claim["warnings"].append("missing_year" if re.search(r"\d{1,2}月|\d{1,2}/\d{1,2}", normalized) else "unsupported_format")
            return
        left, right = value[:conditional.end()].strip(" ~至—-\t"), value[conditional.end():].strip()
    for key, raw in (("start", left), ("end", right)):
        if raw is None:
            continue
        if not raw:
            claim["warnings"].append("open_" + key)
        elif _CONDITION.fullmatch(raw.translate(_TRANS)):
            claim["conditions"][key] = raw
        else:
            endpoint, error = _endpoint(raw, claim["start"] if key == "end" else None)
            claim[key] = endpoint
            if error:
                claim["warnings"].append(error)
    if claim["start"] and claim["end"]:
        day_precision = "day" in (claim["start"]["precision"], claim["end"]["precision"])
        length = 10 if day_precision else 19
        if claim["end"]["value"][:length] < claim["start"]["value"][:length]:
            claim["warnings"].append("reversed_range")
    errors = set(claim["warnings"]) - {"open_start", "open_end"}
    if errors:
        claim["status"] = "unresolved"
    elif claim["start"] and (right is None or claim["end"]):
        claim["status"] = "complete"
    elif claim["start"] or claim["end"]:
        claim["status"] = "partial"


def _signals(text, origin):
    result = []
    for sentence in re.finditer(r"[^\n。！？!?]+[。！？!?]?", text):
        emitted = set()
        carry_condition = False
        for clause in re.finditer(r"[^，,；;]+", sentence.group()):
            value = clause.group()
            local_uncertain = bool(re.search(r"可能|或将|视.*情况|根据.*情况|若|如有|如遇", value))
            if re.search(r"若|如遇|如有", value) and re.search(r"维护|天气|情况|不可抗力|变动|延期", value):
                carry_condition = True
            definite = bool(re.search(r"已经取消|已决定|决定取消|已确定|现已取消", value)) and not local_uncertain
            uncertain = local_uncertain or (carry_condition and not definite)
            types = []
            if uncertain and re.search(r"延迟|延期|延后|提前|调整|更改|取消|维护|延长", value):
                types.append("disclaimer")
            if not uncertain:
                temporal_change = bool(re.search(r"延期|延长|推迟|改期|延后", value) or (
                    re.search(r"调整(?:为|至)|变更(?:为|至)", value)
                    and re.search(r"时间|日期|日程|排期|时段|开始|结束|\d{4}年|\d{1,2}月\d{1,2}日|\d{1,2}[:：]\d{2}", value)))
                if temporal_change and not re.search(r"未延期|未延长|不延期|不延长", value):
                    types.append("schedule_change")
                target = r"(?:活动(?!限制|上限)|直播|维护|演出|赛事|节目|报名)"
                nearby = r"[^\n，,。；;]{0,12}"
                cancelled = re.search(target + nearby + r"(?:取消|中止|终止)|(?:取消|中止|终止)" + nearby + target, value)
                if cancelled and not re.search(r"未取消|不取消|不会取消", value):
                    types.append("cancellation")
            if re.search(r"每(?:周|星期|月|天).*(?:重置|刷新|开放|\d{1,2}[:：点时])", value):
                types.append("recurring")
            for kind in types:
                if kind not in emitted:
                    start = sentence.start() + clause.start()
                    result.append({"type": kind, "evidence": _evidence(text, start, sentence.start() + clause.end(), origin)})
                    emitted.add(kind)
    return result

def parse_document(text, title=""):
    """Return {'claims': [...], 'signals': [...], 'notes': [...]}.

    Only line-leading allowlisted labels and their immediate date continuation
    (up to two following lines) are candidates. A labelled single date is a
    complete point claim; a trailing range delimiter is explicitly partial.
    Unresolved claims may retain individually valid endpoints for diagnostics;
    consumers must check status. Duplicate claims are intentionally preserved.
    """
    result = {"claims": [], "signals": _signals(text, "body") + _signals(title, "title"), "notes": []}
    # A timezone declared in a heading/title also applies to nearby labels.
    # Conservatively hold all candidates if the document declares a foreign
    # timezone; mixed-zone documents need human review rather than guessing.
    foreign_zone = bool(_ZONE.search((text + "\n" + title).translate(_TRANS)))
    lines = list(re.finditer(r"[^\n]*(?:\n|$)", text))
    for i, line in enumerate(lines):
        content = line.group().rstrip("\r\n")
        label = _LABEL.match(content)
        if not label:
            continue
        # A prose disclaimer mentioning a label is a signal, not a date claim.
        remainder = content[label.end():]
        if re.match(r"可能|或将|将根据|会根据", remainder):
            continue
        end = line.start() + len(content)
        parts = [remainder]
        for following in lines[i + 1:i + 3]:
            nxt = following.group().strip()
            accumulated = " ".join(parts).strip()
            if not nxt or _LABEL.match(nxt):
                break
            wants_more = not accumulated or bool(re.search(r"[~～至—-]\s*$", accumulated))
            starts_separator = bool(re.match(r"^[~～至—–-]", nxt))
            # A second adjacent date/time line must not be silently ignored:
            # either it completes the range or the entire label is unresolved.
            temporal_continuation = bool(re.match(r"^(?:\d{4}[年/-]|\d{1,2}[月/:：点时]|[０-９])", nxt))
            if not (wants_more or starts_separator or temporal_continuation) or not re.match(r"^(?:北京时间|中国标准时间|日本时间|UTC|GMT|\d|[０-９~～至—–-]|即日|即刻|上架后)", nxt):
                break
            parts.append(nxt)
            end = following.start() + len(following.group().rstrip("\r\n"))
        claim = {"kind": _LABELS[label.group(1)], "label": label.group(1), "start": None, "end": None,
                 "conditions": {"start": None, "end": None}, "status": "unresolved", "warnings": [],
                 "evidence": _evidence(text, line.start(), end)}
        if foreign_zone:
            claim["warnings"].append("unsupported_timezone")
        else:
            _parse_value(text[line.start() + label.end():end], claim)
        claim["warnings"] = list(dict.fromkeys(claim["warnings"]))
        result["claims"].append(claim)
    return result
