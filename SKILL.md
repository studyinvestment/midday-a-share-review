---
name: midday-a-share-review
description: 生成 A股午间复盘自包含 HTML 报告。固化 8 大模块结构（指数行情/走势叙事/板块TOP5/量能/资讯/涨停/持仓诊断/下午推演）、暗色渲染模板与中国配色。当 westock-mcp 与 tdx-connector 不可用时，自动降级到公开行情 API（腾讯财经 qt.gtimg.cn + 东方财富 push2/push2delay/push2ex）。适用于午间 12:00 定时复盘自动化，输出可直接推送手机端。
metadata:
  agent_created: true
  version: 2
  trust: "时间一致性 / 数据可审计 / 叙事有条件"
---

# 午间 A股 复盘（结构固化版）

把"午间复盘"做成**可复用的标准件**：无论哪一天运行，都产出结构一致、渲染一致、逻辑一致的 8 模块 HTML 报告。
本 skill 包含两个脚本：`collect_midday_data.py`（采集）与 `generate_midday_review.py`（渲染），均为 **date-agnostic**（读 `--date`，默认今天）。

## 何时使用
- 用户要求"午间复盘 / 午盘复盘 / 中午盘面总结"，或触发"午间早盘复盘"自动化。
- 需要一份**自包含、无外部依赖**的 HTML（内联 SVG 分时图/条形图，CSS 暗色主题），便于推送与归档。
- 当 westock-mcp / tdx-connector 不可用时，本 skill 已内置公开行情 API 降级方案，无需另行处理。

## 8 大模块结构（已固化，顺序不可随意变动）
1. **上午主要指数行情** — 7 大指数表（上午收盘/涨跌幅/开盘/最高/最低/振幅/现价）+ 结构特征点评 + 各指数分时卡。
2. **上午走势叙事与阶段驱动** — 按时间轴分 4–5 阶段（开盘→早盘→盘中→尾盘→主线），每阶段标注驱动因素。数据驱动自动生成。
3. **领涨/领跌板块 TOP5 与盘中强度评分** — 板块卡含主力净额/涨跌比/龙头 + 强度标签（高/中/弱，基于 `persist()` 盘中强度打分，**不承诺预测性**）+ 行业涨幅/跌幅 TOP10 条形图 + 概念 TOP8 表。
4. **成交额与量能评估** — 两市成交额、沪/深上午成交量 vs 前5日全日均量、推算全日量能；量价背离警示。
5. **上午重要资讯与市场影响** — 按关键词筛选财经要闻，标注影响板块与方向（偏多/偏空/中性）。
6. **连板/涨停情况速览** — 涨停/跌停数、连板数、最高板、上涨占比、连板梯队表、情绪判读。
7. **持仓诊断** — 每张持仓卡含现价/浮亏率/分时/均线位置/主力资金/板块共振 + 数据驱动点评（强弱/驱动/资金/技术/操作）。持仓由外部 `holdings.json` 提供（**不固化进 Skill**）。
8. **下午推演：风险点与机会点** — 风险/机会双栏清单（带高/中/低等级标签），末附"操作总纲"三条。

## 渲染与配色规范（已固化）
- 暗色主题：背景 `#0f172a`、面板 `#1e293b`、卡片 `#263348`。
- **中国股市配色**：涨红 `#ef4444` / 跌绿 `#22c55e` / 平 `#94a3b8`。class 为 `up/down/flat`。
- 所有图表为**内联 SVG**（分时 `spark()`、板块条形 `barchart()`），无 CDN/外部依赖。
- 响应式：`@media(max-width:640px)` 已处理手机端。
- 报告中**必须标注真实数据来源**（文末 `.src` 区块）——记录实际使用的公开行情接口，**不虚构 MCP/连接器状态**。

## 数据 schema（generate 脚本的输入契约）
合并 JSON（`midday_merged_{date}.json`）需含：
```
snapshot        dict[code] -> {code,name,price,prev_close,open,high,low,pct,amount_wan,...}
minutes         dict[code] -> [{t,p,v,amt}]   (仅 0930-1130)
kline           dict[code] -> [{date,open,close,high,low,vol}]  (>=70根日K)
sector_industry list[{name,code,pct,netflow,up,down,lead,lead_pct}]
sector_concept  list[同上]
zt_pool         list[{code,name,pct,first_time,open_times,streak,industry}]
dt_pool         list[{code,name,pct,industry}]
news            list[{title,time,media,summary,url}]
fundflow        dict[code] -> {main,small,mid,big,huge,time}  (元)
meta            {schema_version:2, report_date, collected_at, mode}
quality         {level: pass|warn|fail, errors[], warnings[]}   # 核心数据缺失→fail→采集器非零退出、不生成报告
sources         {minutes/breadth/news: {status, as_of, coverage?}}
```
涨跌家数：优先单独的 `breadth_{date}.json`，其次合并体内的 `breadth`。结构：
`{listed_total, valid_total, missing, up, down, flat, up_ratio_valid}`；
`up_ratio` 以 `valid_total`（剔除无报价/停牌）为分母，不直接用 listed_total。

## 采集管线（collect 脚本）+ 关键踩坑（务必保留）
数据源：腾讯财经 `qt.gtimg.cn`（快照）、`web.ifzq.gtimg.cn`（分时/日K）、东方财富 `push2`/`push2delay`（板块/涨跌家数/资金流）、`push2ex`（涨停/跌停池）、东方财富要闻流。
**三个硬坑（已固化进 collect 脚本，勿删）：**
1. **东财 push2 的 IPv6 路由不通 → 强制 IPv4**：`socket.getaddrinfo` 覆写为 `AF_INET`，否则 `HTTP 000`。
2. **`fs` 参数空格必须编码为 `+`**（用 `%20` 会被拒返回空响应）；用 `urllib.parse.quote(fs, safe="+")`。
3. **大 `pz` 易被拒 → 分页 `pz<=100`**；并在 `push2.eastmoney.com` 与 `push2delay.eastmoney.com` 两 host 间兜底重试。
**涨跌家数**：用"全A逐页精确计数（pz=100）"，**不要用行业板块成分股求和**（跨层级重复计数不可靠）。
**分时**：`minute/query` 返回的是**当日**数据（与传入 date 无关），故采集脚本必须在**交易日当天 12:00 前后**运行，不能回补历史。

## 运行方式
```bash
# 1) 采集（交易日当天运行；产出 midday_merged_{date}.json + breadth_{date}.json）
python collect_midday_data.py --date 2026-08-21

# 2) 渲染（date 决定输出文件名 午间复盘_{date}.html）
python generate_midday_review.py --date 2026-08-21 \
    --merged midday_merged_20260821.json --breadth breadth_20260821.json

# 3) 交付（按当前环境能力：WorkBuddy 用 present_files + cwd；其他环境各自交付）
```

### 运行模式（时间一致性，v2 新增）
采集器 `--mode` 明确三态，缺失时按当前时刻自动判定（11:30–13:00→`strict-midday`，其余→`late-snapshot`）：
- **`strict-midday`**（默认，仅 11:30–13:00 允许采集）：全模块以上午 11:30 为统一基准。
- **`late-snapshot`**（13:00 后运行）：仅指数"上午收"还原至 11:30；板块/广度/资金流/涨跌停/资讯标为**「当前快照」**，严禁叫"上午"。
- **`render-archive`**：用中午已保存的 `midday_merged_{date}.json` 直接重渲，是**唯一可信的历史补跑**方式。

### 数据质量闸门（v2 新增）
采集器产出 `meta(schema_version=2/mode/collected_at)` + `quality(level/errors/warnings)` + `sources(status/as_of)`。
最低校验：`--date` 必须等于采集当日；7 指数齐全且分钟末点 11:30；`up+down+flat+missing==listed_total`；
资讯属报告当日且不晚于 11:30（strict 模式）。**核心数据缺失→非零退出且不生成报告**；可选数据缺失→标注"数据不可用"而非 0。

## 事实层与条件叙事（v2 新增，P2）
所有结论句必须由 `compute_facts()` 推导的**事实层**驱动，禁止无条件硬编码。事实层字段（generate 脚本内）：
`breadth_regime`（<40弱/40-55中性/>55强）、`volume_regime`（推算全日量能<100缩量/100-115持平/>115放量）、
`index_pattern`（冲高回落/探低回升/单边）、`leader_concentration`（龙头资金占比>=40%为高集中，相关性）、
`news_confidence`、`zt_regime`、`weekday`（仅真实周五才提示"周末窗口"）、`ff_rel_rank`（板块资金流**排名**而非绝对额）。

**叙事红线（务必遵守）：**
- 每句判断可追溯到：用了什么指标、阈值多少、数据缺失时是否停判（`missing`→不输出该结论）、属于 fact/correlation/speculation。
- 去因果化：不用"核心原因""直接压制""派发、散户接盘""下方空间有限""确定性最高"等因果/承诺式措辞；改"同时出现""可能反映""需进一步验证""盘中强度居前""相关性"。
- 节奏词不硬编码："周五效应"严禁；"周末窗口"仅在 `weekday==4` 时由数据触发。"平开后/高开后/低开后"按开盘涨跌幅条件选择。"距成本"按盈亏分档（深套/小幅低于/接近/扭亏）。
- 资金流相对强弱用**排名/分位数**，避免"净流入 10 亿"类绝对阈值对不同体量板块不公平。

## 配置点
- `IDX`（指数）：7 大指数 `(code, 名称)`，**已设为合理默认值**，一般无需改。
- **持仓（重要）：持仓是【个人私有配置】，刻意不固化进本 Skill**，避免把真实持仓写进可分享模板（含 WorkBuddy/Codex 通用模板）。
  - 提供方式（任选其一）：
    1. 在运行目录或本 skill 的 `scripts/` 同级放置 `holdings.json`（自动加载）；
    2. 运行脚本时传 `--holdings 路径.json`（采集与渲染两个脚本都支持）。
  - 格式见同目录 `holdings.example.json`：
    ```json
    {
      "holdings": [["sh600519", "贵州茅台", 1500.00], ["sz159915", "创业板ETF", null]],
      "hold_ctx": {"sh600519": {"sector": "白酒", "note": "白酒龙头"},
                   "sz159915": {"sector": null, "note": "宽基ETF"}}
    }
    ```
  - 未提供时，报告"持仓诊断"模块显示占位提示，其余模块照常生成。
  - `holdings` 每行：`(代码, 名称, 成本)`，成本未知填 `null`/`None`（报告自动标注"成本未记录"并跳过浮亏率）。
  - `hold_ctx`：每只持仓关联的"所属板块"名称（用于共振判断，如 `"钨"`/`"半导体"`；ETF/宽基填 `null`）。
  - `.gitignore` 已忽略 `holdings.json`，切勿误提交真实持仓。

## 叙事精修（可选）
报告所有文案默认**由数据驱动自动生成**（保证自动化无人值守可跑）。若某天想注入人工精修的高质量文案，
可准备一个 JSON（`--narrative path.json`），键名：`s01/s02/s03/s05/s06/s07`（模块点评）与 `s08_risk/s08_opp`（下午推演两栏）。
提供即覆盖，不提供则自动生成。

## 输出与推送约定
- 报告写入 `daily-review/午间复盘_{date}.html`。
- 交付：使用**当前环境可用的文件交付能力**（WorkBuddy 用 `present_files` 并设 `cwd`；Codex 等其他环境按各自能力交付，不硬编码单一工具）。
- 时间一致性：顶部 `.warn` 框按运行模式统一声明口径——`strict-midday` 全模块为上午基准；`late-snapshot` 仅指数"上午收"还原至 11:30，板块/广度/资讯/涨跌停标为"当前快照"。**禁止把午后数据标成"上午"**。
