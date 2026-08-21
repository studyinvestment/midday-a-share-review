---
name: midday-a-share-review
description: 生成 A股午间复盘自包含 HTML 报告。固化 8 大模块结构（指数行情/走势叙事/板块TOP5/量能/资讯/涨停/持仓诊断/下午推演）、暗色渲染模板与中国配色。当 westock-mcp 与 tdx-connector 不可用时，自动降级到公开行情 API（腾讯财经 qt.gtimg.cn + 东方财富 push2/push2delay/push2ex）。适用于午间 12:00 定时复盘自动化，输出可直接推送手机端（present_files，cwd 指向推送目录）。
agent_created: true
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
3. **领涨/领跌板块 TOP5 与持续性判断** — 板块卡含主力净额/涨跌比/龙头 + 持续性标签（强/中/弱，基于 `persist()` 打分）+ 行业涨幅/跌幅 TOP10 条形图 + 概念 TOP8 表。
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
- 报告中**必须标注数据来源**（文末 `.src` 区块），并声明 MCP 不可用时的降级方案。

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
meta            {date, gen_time}
```
涨跌家数：优先单独的 `breadth_{date}.json` = `{up,down,flat,total}`，其次合并体内的 `breadth` / `breadth_exact` / `updown`。

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

# 3) 推送手机端（自动化必做）
present_files(files=[".../daily-review/午间复盘_20260821.html"],
              cwd="推送目录")   # cwd 指向 present_files 要求的推送 cwd
```

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
- 自动化必须调用 `present_files` 并设 `cwd` 为推送目录，否则用户手机端收不到。
- 时间错位处理：若任务实际在 13:00 后（午后已开盘）触发，报告中"现价"列反映最新快照、"上午收"列反映 11:30 分时还原值，并在顶部 `.warn` 框说明口径。
