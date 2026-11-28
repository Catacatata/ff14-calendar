# 多源汇总第一步：公开来源采集（影子运行）

## 本阶段边界

本阶段仅发现并缓存 B站官方动态、取得完整正文和关联新闻，记录成功/失败状态。
**不解析为活动起止时间，不创建事件，不修改 `calendar.json` 或 `ff14.ics`。**
现有 `update_calendar.yml`、网页、节假日、API代理与分类规则不变。
RSSHub 不在新增采集链路中。

B站账号固定为官方 UID `6655514`。默认 `authenticated`，复用已通过 Actions 测试的
`bilibili-api-python==17.4.2`、`curl_cffi==0.17.0` 和 `chrome131` 配置。
登录态只是 API 访问凭据，Cookie 本身没有只读权限；本项目只发起读取请求。

## 文件与运行

- `collect_sources.py`：独立入口。
- `source_collectors/bilibili.py`：列表增量扫描及 Opus 正文采集。
- `source_collectors/official_news.py`：官方新闻正文接口与公开内容提取。
- `source_collectors/bilibili_auth.py`：诊断脚本和采集器共用的凭据字段校验。
- `source_collectors/common.py`：原子缓存写入、状态、公开链接及内容版本。
- `requirements-sources.txt`：只供独立来源采集使用，不改变主更新依赖。
- `.github/workflows/collect_sources.yml`：手动影子采集，仅在 main 执行。

安装后运行：

```text
python -m pip install -r requirements-sources.txt
python collect_sources.py --news-id 395921
```

默认输出：

```text
data/sources/bilibili.json
data/sources/official_news.json
```

这些文件保存的是可公开的来源内容，会随 Pages 发布而可公开访问。
**不得在其中保存 Cookie、完整响应对象、个人登录资料或刷新令牌。**
库仅在内存中使用环境变量提供的凭据，脚本不刷新凭据，不更新 Secrets。

## Actions 使用

提交本阶段代码后，运行：

**Actions → Collect Official Sources (Shadow Only) → Run workflow → main**

沿用已配置的 `BILI_SESSDATA`，可选 `BILI_BUVID3`、`BILI_BUVID4`、
`BILI_DEDEUSERID`、`BILI_JCT`，无需再复制到新 Secret 名称。
凭据只传给采集步骤，不传给依赖安装、测试或提交步骤。

工作流会：

1. 安装独立依赖；
2. 执行模拟采集及凭据安全测试，不发网络请求；
3. 采集 B站公开内容和关联新闻，并补取调查样本新闻 `395921`；
4. 校验两个缓存，检查除这两个指定文件外的已跟踪/未跟踪文件没有变化；
5. 校验整个暂存区的严格文件白名单，**只提交上述两个公开缓存文件到 main**；
6. 若采集降级或因预算仍有待补工作，在保存状态后将任务标为失败，不以绿色掩盖不完整。

首次正文数量超过单轮限额时，可能连续数轮显示 `incomplete`；重复手动运行即可续补，
不必人工补正文。`incomplete` 与鉴权失败不同，应结合 `request_outcome`、`errors` 和待补指标判断。

本阶段没有新增 schedule，也没有把采集接入原日历更新工作流。
与主更新共用 concurrency 锁，避免两个自动任务同时向 main 推送。
仍可能与人工推送冲突；发生时推送失败会明确显示，不强制覆盖远端。

## 增量、补取与限制

- 默认每轮最多请求 4 页。从最新页开始，至少重叠扫描两页；置顶不作为停止边界。
- 新增动态超过本轮页数预算时，在 `scan_queue` 保存后续游标和扫描开始前的 ID 边界。
  下轮给最新页与旧缺口分配预算（默认各最多 2 页），旧缺口不能被刚缓存的新前缀遮住。
  续扫直到遇到该次扫描原有的非置顶边界，或来源明确无后续页。
- 历史缓存不因本轮未出现、删除或不可见而删除。
- 不是全量历史回填；首次默认基线为近期最多 4 页，标记 `initial_history_limited`。
  其后的覆盖目标是“自首次近期窗口起”，见 `coverage_scope`，不承诺补齐此前全部历史。
  首次基线请求中途失败时，也会保留游标和剩余基线页数。可用 `--max-pages` 调为 2～6。
- 支持文字、图文、图文型专栏的 Opus 正文；转发/视频等类型只保留官方列表元数据，
  标记 `unsupported`，不会把被转发作者正文冒认为官方发布。
- 每次最多补取 20 条正文，可用 `--max-details` 调为 1～30。
- 新记录和待更新记录优先。即使已有旧正文，发现摘要变化、正文失败或受限额延期后，
  也会持续排队补取，不要求该动态下轮仍出现在最新列表中。
- 已完成且本轮可见的正文，每 24 小时复查；列表摘要变化可提前触发复查。
- 正文失败至少间隔 6 小时再试，保留旧正文。
- 412/429/明确鉴权拒绝时，不连续重试 B站接口。
- B站单轮时间预算 180 秒；新闻预算 60 秒，新闻单轮最多 10 条。
- 本阶段只记录图片 URL，不下载图片。后续图文合并时复用本地 WebP 下载能力。

## 缓存结构

两个文件都有：

```text
schema_version
source
status
items（以来源原生 ID 为键）
```

状态包括：

- `outcome`：`ok`（定义范围内完整）、`incomplete`（仍有扫描/正文待补）、
  `degraded`（本轮出错或仍有未解决失败）；
- `request_outcome`：仅表示本轮请求结果，`ok`、`degraded` 或 `skipped`；
- `last_attempt_at`、`last_success_at`：尝试与最近实际获取且整体恢复完整的时间；
- `consecutive_failures`、`used_cache`、`errors`、`unresolved_error_count`；
- `coverage_complete`、`content_complete`：扫描与正文完整性；
- `scan_backlog`、`pending_count`、`cooldown_count`、`deferred_details` 等待补指标。

冷却跳过不清零失败计数、不推进成功时间，未解决的记录错误保留在单条 `detail_error` 中。
`deferred_details` 包含冷却中的工作；正文即使仍保留旧内容，更新待补也计入 `pending_count`。
`ok` **不表示补齐首次基线之前的全部历史，也不表示已解析成正确活动日期**。

单条 B站记录保留官方作者、原生 ID、动态类型、发布时间、首次发现/最近看见时间、
列表摘要、完整正文、公开图片/链接、关联官方新闻 ID。
`content_hash` 只对公开正文内容计算；内容改变时记录 `revision_count` 和最多 5 份旧内容。
发布时间与抓取时间均不是活动日期。

退出码：`0` 正常；`2` 来源降级或尚未完整，已保存公开缓存/状态；`1` 文件或缓存处理异常。
各来源的加载、初始化、采集和保存分别隔离；B站缓存损坏不阻止显式新闻 ID 或新闻自身待补工作。
损坏文件不会被当成空缓存重写。本地文件错误会让 Actions 的缓存校验失败，避免提交损坏数据。

## 官方新闻正文调查：395921

调查时从官网 HTML 的 JS 引用进入新闻详情组件，确认其真实请求为：

```text
GET https://cqnews.web.sdo.com/api/news/newsDetail?gameCode=ff&id=395921
```

响应为 `Code`、`Message`、`Data`，实测成功码 `Code` 是字符串 `"0"`；
`Data` 包含 `Id`、`CategoryCode`、`Title`、`KeyWord`、`Summary`、`Author`、
`PublishDate`、`OutLink`、`HomeImagePath`、`Content`。
`Content` 是 HTML。脚本提取可读正文、链接和图片，不执行 HTML/JS。
`PublishDate` 是文章发布时间，样本为 `2026/11/24 16:55:29`。

样本正文明确包含：

```text
活动时间：2026年12月1日16:00~2027年1月12日15:59
```

并链接到：

```text
https://actff1.web.sdo.com/project/20261118mogmog/59xh7egc29ya/index.html
```

专题页实测也包含可直接提取的 HTML 日期文字，且同一组日期因页面布局重复出现。
因此后续日期解析应去重相同时间范围，而不是把每次出现当作不同事件；
无需依靠 OCR。专题页采集尚未泛化接入，本阶段仅保存正文中的专题链接。

真实公开接口响应保存在 `tests/fixtures/official_news_395921.json`，包含取样 URL、时间和用途。
该 fixture 仅供解析回归，不是运行时日期补丁，不会为日历添加或纠正任何事件。
本阶段不批量扫描新闻列表；新闻 ID 来自经官方作者校验的动态列表链接、正文链接及显式指定的调查样本。
正文显示为“网页链接”等文字时，也提取富文本节点的 `jump_url`，不依赖裸 URL。
现有图文脚本的新闻列表获取保持原状。

## Opus 真实样本与边界回归

`tests/fixtures/bilibili_opus_1274198612158447624.json` 保存匿名读取的真实样本，
只选取公开正文/标题模块、原生 ID 和官方 UID；没有完整 API 响应、用户登录状态或凭据。
真实样本中的新闻链接恰好是裸 URL，主题链接使用富文本 `rich.jump_url`。
测试既检查该真实结构，也用相同节点形状构造“网页链接”显示名变体，明确区分真实样本与模拟变体。

`tests/test_source_continuity.py` 覆盖扫描超限后多轮补齐、已有正文更新离开列表后重试、
冷却不误报恢复、待补状态和真实正文结构；`tests/test_source_runner.py` 覆盖坏缓存隔离、
来源初始化/保存失败以及官方列表新闻链接发现。

## 尚未实施

- 通用日期解析及日期精度规则；
- 活动身份、稳定 UID 与跨来源合并；
- 冲突决策、延期/取消处理；
- 来源缓存参与正式 JSON/ICS 输出；
- Cookie 自动刷新与安全持久化；
- 正式更新工作流的每日来源采集。

不要把本阶段的公开内容缓存或抓取成功状态解释为已完成上述功能。
