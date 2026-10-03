# 🎮 FF14 国服活动日历

一个面向《最终幻想14》国服玩家的**自动更新活动日历**。

项目通过自动程序获取 FF14 国服官方活动数据，整理活动开始/结束时间，并生成标准 iCalendar (`.ics`) 订阅文件，同时提供网页端活动日历。

目标是尽可能减少人工维护，让日历能够长期、稳定、自动运行。

---

## 🌐 在线访问

### 活动日历网页

**https://ff14-calendar.pages.dev/**

网页可以查看：

- 📅 当前月份活动日历
- 🔥 当前正在进行的活动
- ⏳ 即将开始的活动
- ⚠️ 即将结束的活动
- 🕐 最近更新时间
- 🔗 官方活动详情

### 日历订阅

```text
https://ff14-calendar.pages.dev/ff14.ics
```

可以添加到支持 iCalendar 的日历软件中，例如：

- Apple 日历
- Google Calendar
- Outlook
- iOS / Android 系统日历
- 其他支持 ICS 网络订阅的客户端

订阅后，当项目自动更新 `ff14.ics` 时，日历客户端会按照自身刷新机制获取最新活动。

---

# ✨ 项目特点

### 🤖 自动获取官方活动

项目通过 FF14 国服官方活动数据接口获取：

- 活动名称
- 开始时间
- 结束时间
- 官方活动页面
- 活动 ID

并自动转换为日历事件。

---

### 📅 自动生成 ICS

程序会生成：

```text
ff14.ics
```

采用标准 iCalendar 格式，可直接用于日历订阅。

---

### 🌐 网页活动日历

除 ICS 订阅外，项目还会生成：

```text
calendar.json
```

网页通过该文件自动展示当前活动信息。

无需手工修改网页中的活动内容。

---

### 🔄 每日自动更新

GitHub Actions 每日自动运行：

```text
官方活动数据
       ↓
generate_calendar.py
       ↓
数据整理 / 去重 / 分类
       ↓
┌──────────────┬──────────────┐
│              │              │
ff14.ics    calendar.json   API Cache
│              │
↓              ↓
日历订阅      网页展示
```

正常情况下无需人工执行更新。

---

# 🏷️ 活动分类

项目会根据活动名称自动进行分类。

## 🌸 季节活动

包括：

- 降神节（Heavensturn）
- 恋人节（Valentione's Day）
- 女儿节（Little Ladies' Day）
- 彩蛋狩猎（Hatching-tide）
- 金碟嘉年华（The Make It Rain Campaign）
- 红莲节（Moonfire Faire）
- 新生庆典（The Rising）
- 守护天节（All Saints' Wake）
- 星芒节（Starlight Celebration）

> 活动惯例月份仅用于活动识别和分类，项目不会根据往年时间预测尚未公布的活动。

---

## 🤝 限时联动

例如：

- 妖怪手表
- 勇者斗恶龙 X
- 星歌异闻
- 纵使前路狱火焰毒
- 献给英雄的夜曲
- 黑色恶魔
- 雷光降世

包括首次举办以及后续复刻活动。

---

## ♾️ 长效联动

例如：

- 牙狼
- 怪物猎人：世界
- 怪物猎人：荒野
- 糖豆人
- 魔光键影

对于长期开放内容，网页会与普通限时活动进行区分。

---

## 🧩 其他活动

同时支持识别：

- 版本更新
- PLL / 官方直播
- Fan Festival
- 莫古莫古★大收集
- 艾欧泽亚通行证
- 月卡活动
- 商城活动
- 其他官方活动

---

# 🛡️ 数据原则

本项目遵循一个重要原则：

> **不预测尚未由官方公布的活动。**

即使某项季节活动按照往年惯例通常会在某个月举行，只要当前数据源没有正式活动信息，就不会自动创建日历事件。

例如：

```text
往年惯例：
10月 → 守护天节
```

并不意味着程序会自动生成：

```text
2026年10月 → 守护天节
```

只有获得实际活动数据后才会加入日历。

这样可以避免预测日期污染用户的日历。

---

# 📡 数据来源

当前项目主要使用：

### FF14 国服官方活动数据

用于获取实际活动的：

```text
活动名称
开始时间
结束时间
活动链接
活动 ID
```

这是自动日历的主要数据来源。

### 基础活动数据

项目可以读取基础 ICS 数据，用于已有活动记录的补充。

### 人工补充

特殊情况下可以通过：

```text
ff14_override.json
```

补充自动数据源未覆盖的活动。

人工数据仅作为补充机制，不是项目日常运行所必需的部分。

---

# 📁 项目结构

```text
ff14-calendar/
│
├── .github/
│   └── workflows/
│       └── update_calendar.yml
│
├── data/
│   └── api_cache.json
│
├── .gitignore
├── _headers
│
├── ff14.ics
├── calendar.json
├── ff14_override.json
│
├── generate_calendar.py
├── index.html
├── requirements.txt
│
└── README.md
```

主要文件说明：

| 文件 | 作用 |
|---|---|
| `generate_calendar.py` | 获取、整理活动并生成日历数据 |
| `ff14.ics` | iCalendar 日历订阅 |
| `calendar.json` | 网页活动数据 |
| `index.html` | 活动日历网页 |
| `ff14_override.json` | 人工补充活动 |
| `data/api_cache.json` | 官方 API 数据缓存 |
| `update_calendar.yml` | GitHub Actions 自动更新任务 |
| `_headers` | Cloudflare Pages 响应头配置 |

---

# ⚙️ 自动更新机制

GitHub Actions 定时运行：

```text
每天 UTC 01:00
```

对应：

```text
北京时间 09:00
```

更新流程：

```text
1. 获取 FF14 国服官方活动数据

2. API 请求失败时尝试使用缓存数据

3. 合并基础数据及人工补充数据

4. 对活动进行去重和分类

5. 生成 ff14.ics

6. 生成 calendar.json

7. 自动提交更新结果

8. Cloudflare Pages 自动部署最新网页
```

即使官方接口出现短时间连接异常，也可以利用缓存降低日历数据突然消失的风险。

---

# 🖥️ 本地运行

需要 Python 3。

安装依赖：

```bash
pip install -r requirements.txt
```

运行：

```bash
python generate_calendar.py
```

运行成功后将生成或更新：

```text
ff14.ics
calendar.json
data/api_cache.json
```

---

# ☁️ 部署

本项目为静态网站，可以部署在：

- Cloudflare Pages
- GitHub Pages
- Vercel
- Netlify
- 其他静态网站托管平台

当前项目使用 **GitHub Actions + Cloudflare Pages**。

---

# ⚠️ 免责声明

本项目为玩家制作的非官方工具，与 Square Enix、盛趣游戏及《最终幻想14》官方无隶属关系。

活动信息以《最终幻想14》国服官方最终公告为准。

如果自动获取的数据与官方公告存在差异，请以官方信息为准。

FINAL FANTASY XIV © SQUARE ENIX

---

# ❤️ 项目目标

这个项目最重要的目标不是预测 FF14 下一次会举办什么活动，而是：

> **把已经公布的活动可靠地整理成一个可以长期订阅的日历。**

尽量通过自动化降低维护成本，使项目不会因为个人玩家暂时离开游戏或停止维护而立即失效。
