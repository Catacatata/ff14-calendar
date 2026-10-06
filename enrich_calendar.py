"""在原有日历生成之后运行；只补图片和简介，不创建事件或修改日程。"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, urljoin
from io import BytesIO
import hashlib
import json
import os
import re
import unicodedata
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
RSS_URL = os.environ.get('FF14_RSS_URL', 'https://rsshub.app/ff14/zh/events')
CALENDAR = ROOT / 'calendar.json'
RSS_CACHE = ROOT / 'data/rss_cache.json'
DETAILS_CACHE = ROOT / 'data/event_details_cache.json'
ALIASES = [
    ('降神节', 'heavensturn'), ('恋人节', 'valentionesday'),
    ('女儿节', 'littleladiesday'), ('彩蛋狩猎', '猎蛋节', 'hatchingtide'),
    ('金碟嘉年华', '金碟游乐场庆典'), ('红莲节', 'moonfirefaire'),
    ('新生庆典', 'therising'), ('守护天节', 'allsaintswake'),
    ('星芒节', 'starlightcelebration'), ('妖怪手表', 'yokaiwatch'),
    ('最终幻想15', '最终幻想xv', 'ff15', 'ffxv', '纵使前路狱火焰毒'),
    ('最终幻想16', '最终幻想xvi', 'ff16', 'ffxvi', '献给英雄的夜曲'),
    ('勇者斗恶龙', 'dq10', 'dqx', '黑色恶魔'), ('星歌异闻',),
    ('牙狼', 'garo'), ('怪物猎人荒野',), ('怪物猎人世界',),
    ('糖豆人', 'fallguys'), ('魔光键影',), ('拍立方',),
]
SEASONAL = {group[0] for group in ALIASES[:9]}
SHOP = ('商城', '上新', '月卡', '礼赠', '充值', '优惠', '折扣')
COMMUNITY = ('社区', '抽奖', '评论', '穿搭', '时装灵感季')
FOLLOWUP = ('获奖', '中奖', '名单公布', '倒计时', '即将结束', '活动结束', '攻略')


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def norm(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', str(text)).lower())


def themes(text):
    text = norm(text)
    return {g[0] for g in ALIASES if any(norm(a) in text for a in g)}


def channel(text):
    if any(w in text for w in SHOP): return 'shop'
    if any(w in text for w in COMMUNITY): return 'community'
    return 'event'


def safe_url(url, base=''):
    url = urljoin(base, str(url or '').strip())
    p = urlsplit(url)
    return url if p.scheme in ('https', 'http') and p.hostname else ''


def canonical(url):
    p = urlsplit(safe_url(url))
    # 保留 hash 新闻路由，防止所有新闻页被错误视为同一个链接。
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/'), p.query, p.fragment))


def date(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        try: result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError): return None
    # 原脚本的无时区日期按北京时间理解。
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone(timedelta(hours=8)))
    return result


def parse_feed(body):
    feed = feedparser.parse(body)
    if not feed.entries and feed.bozo:
        raise ValueError('RSS 内容无法解析')
    rows = []
    for entry in feed.entries:
        link = safe_url(entry.get('link', ''))
        content = entry.get('summary', '')
        if not content and entry.get('content'):
            content = entry.content[0].get('value', '')
        soup = BeautifulSoup(content, 'html.parser')
        for tag in soup(['script', 'style']): tag.decompose()
        images = [safe_url(img.get('src') or img.get('data-src'), link) for img in soup.find_all('img')]
        images += [safe_url(m.get('url'), link) for m in entry.get('media_content', [])]
        images = [u for u in images if u and not re.search(r'(?:logo|icon|avatar|spacer)(?:[._/-])', u, re.I)]
        rows.append({'title':entry.get('title',''), 'link':link,
                     'links':[link]+[safe_url(a.get('href'),link) for a in soup.find_all('a')],
                     'image':images[0] if images else '',
                     'description':soup.get_text(' ', strip=True)[:240],
                     'published':entry.get('published','')})
    return rows


def fetch_rows(session):
    cached = read_json(RSS_CACHE, {'items':[]})
    old = cached.get('items', []) if isinstance(cached, dict) else []
    try:
        response = session.get(RSS_URL, timeout=(10, 25))
        response.raise_for_status()
        fresh = parse_feed(response.content)
        merged = {r.get('link') or r.get('title'):r for r in old}
        merged.update({r.get('link') or r.get('title'):r for r in fresh})
        rows = list(merged.values())[-2000:]
        write_json(RSS_CACHE, {'fetched_at':datetime.now(timezone.utc).isoformat(), 'items':rows})
        print(f'RSS：本次 {len(fresh)} 条，缓存共 {len(rows)} 条')
        return rows
    except Exception as error:
        print(f'RSS 获取失败，保留缓存：{type(error).__name__}')
        return old


def score(event, row):
    url = canonical(event.get('url',''))
    if url and any(canonical(u)==url for u in row.get('links',[]) if u):
        return 100
    name, title = str(event.get('name','')), row.get('title','')
    if channel(name)!=channel(title): return 0
    if any(w in title for w in FOLLOWUP) and not any(w in name for w in FOLLOWUP): return 0
    start, published = date(event.get('start')), date(row.get('published'))
    if start is None or published is None: return 0
    gap=(start-published).total_seconds()/86400
    if not -21 <= gap <= 120: return 0
    years=set(re.findall(r'20\d{2}', title))
    if years and str(start.astimezone(timezone(timedelta(hours=8))).year) not in years: return 0
    name_period=re.findall(r'第[一二三四五六七八九十\d]+期',name)
    if name_period and not all(p in title for p in name_period): return 0
    left, right = themes(name), themes(title)
    if left:
        if left != right: return 0
        # 季节活动仅匹配真正的活动公告，排除往年道具等新闻。
        if left & SEASONAL and ('往年' in title or '道具' in title): return 0
        return 80
    clean_title = re.sub(r'【[^】]*】|\[[^\]]*\]', '',title)
    left, right = norm(name), norm(clean_title)
    if len(left)<5: return 0
    if left==right: return 90
    if left in right: return 75
    return 0


def match(event, rows):
    ranked=[(score(event,r),r) for r in rows if r.get('image') or r.get('description')]
    ranked=[(s,r) for s,r in ranked if s]
    if not ranked: return None
    best=max(s for s,_ in ranked)
    candidates={r.get('link') or r['title']:r for s,r in ranked if s==best}
    # 多个不同公告同分时，不用“取第一条”来猜。
    return next(iter(candidates.values())) if len(candidates)==1 else None


def download_image(session, url, key, old):
    if old.get('image_url')==url and old.get('image') and (ROOT/old['image']).is_file():
        return old['image']
    try:
        with session.get(url,timeout=(10,20),stream=True) as r:
            r.raise_for_status()
            chunks=[]; size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk)
                if size>8*1024*1024: raise ValueError('图片过大')
                chunks.append(chunk)
        with Image.open(BytesIO(b''.join(chunks))) as source:
            if source.width*source.height>25000000: raise ValueError('图片尺寸过大')
            image=ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((960,960))
            if image.width<80 or image.height<40: raise ValueError('非活动缩略图')
            digest=hashlib.sha256((key+url).encode()).hexdigest()[:20]
            relative=f'images/events/{digest}.webp'
            target=ROOT/relative;target.parent.mkdir(parents=True,exist_ok=True)
            image.save(target,'WEBP',quality=85,method=4)
            return relative
    except Exception as error:
        print(f'活动图片下载失败，保留旧图：{type(error).__name__}')
        return old.get('image','') if old.get('image') and (ROOT/old['image']).is_file() else ''


def enrich():
    data=read_json(CALENDAR,None)
    if not isinstance(data,dict) or not isinstance(data.get('events'),list):
        raise ValueError('原生成脚本必须先生成含 events 数组的 calendar.json')
    details=read_json(DETAILS_CACHE,{})
    session=requests.Session()
    session.headers.update({'User-Agent':'Mozilla/5.0 FF14Calendar/1.0','Accept':'*/*'})
    api_cache=read_json(ROOT/'data/api_cache.json',{})
    api_images={}
    if isinstance(api_cache,dict):
        for month in api_cache.values():
            if isinstance(month,list):
                for item in month:
                    if isinstance(item,dict) and item.get('banner_url'):
                        api_images[str(item.get('id'))]=safe_url(item['banner_url'])
    rows=fetch_rows(session)
    matched=0;images=0
    for event in data['events']:
        key=str(event.get('id') or event.get('name'))+'|'+str(event.get('start'))
        old=details.get(key,{})
        row=match(event,rows)
        current=dict(old)
        if row:
            matched+=1
            if row.get('description'): current['description']=row['description']
            current['rss_link']=row['link']
            current['rss_title']=row['title']
        api_id=str(event.get('id','')).removeprefix('api-')
        image_url=safe_url(event.get('banner_url')) or api_images.get(api_id,'') or (row.get('image','') if row else '') or old.get('image_url','')
        if image_url:
            image=download_image(session,image_url,key,old)
            if image:
                current['image']=image
                # 只有确实下载了新图，才标记新 URL 为已下载。
                if image!=old.get('image') or image_url==old.get('image_url'):
                    current['image_url']=image_url
        if current.get('image') and (ROOT/current['image']).is_file():
            event['image']=current['image'];images+=1
        if current.get('description'): event['description']=current['description']
        if current.get('rss_link'): event['details_source']=current['rss_link']
        if current: details[key]=current
    write_json(DETAILS_CACHE,details)
    write_json(CALENDAR,data)
    print(f'补充完成：RSS 匹配 {matched} 个，图片 {images} 个，日程 {len(data["events"])} 个（未新增事件）')


if __name__=='__main__': enrich()
