---
name: midday-a-share-review
description: 生成 A股午间复盘自包含 HTML 报告。固化 8 大模块结构（指数行情/走势叙事/板块TOP5/量能/资讯/涨停/持仓诊断/下午推演）、暗色渲染模板与中国配色。数据源四级降级链：westock-mcp / tdx-connector → hithink-finance MCP（同花顺）→ 腾讯财经 + 东方财富公开 API。适用于午间 12:00 定时复盘自动化，输出可直接推送手机端。
metadata:
  agent_created: true
  version: 4
  trust: "时间一致性 / 数据可审计 / 叙事有条件"
---

# 午间 A股 复盘（结构固化版）

把"午间复盘"做成**可复用的标准件**：无论哪一天运行，都产出结构一致、渲染一致、逻辑一致的 8 模块 HTML 报告。
本 skill 包含两个脚本：`collect_midday_data.py`（采集）与 `generate_midday_review.py`（渲染），均为 **date-agnostic**（读 `--date`，默认今天）。

## 何时使用
- 用户要求"午间复盘 / 午盘复盘 / 中午盘面总结"，或触发"午间早盘复盘"自动化。
- 需要一份**自包含、无外部依赖**的 HTML（内联 SVG 分时图/条形图，CSS 暗色主题），便于推送与归档。
- 当 westock-mcp / tdx-connector 不可用时，本 skill 已内置**四级降级链**（见下节），无需另行处理。

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
- 报告中**必须标注真实数据来源**（文末 `.src` 区块）——记录实际使用的接口，**不虚构 MCP/连接器状态**。
- **降级必须显式披露**：若 westock-mcp / tdx-connector 掉线而走了 hithink 或公开 API，报告内要用醒目提示写明「主数据源不可用，已降级至 X」，并列出降级后**哪些模块精度下降**（如：板块资金流缺失、概念板块只剩涨幅榜）。老板要靠这个判断结论可信度。

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

## ⚠️ 先重试，再降级（v3.1，2026-09-01 血的教训）
**MCP 掉线是「间歇性」的，不是「持续性」的。** 实测：12:05 采集时 westock-mcp + tdx-connector 同时断开，
13:01 westock **自己恢复**。本期因此白跑约 40 分钟探测 + 少了一个模块。

**正确顺序（务必遵守）：**
1. 先调一次目标 MCP 做连通性探测（用最轻量的工具，如 `data_quote` 只传 1 个代码）
2. 失败 → **等 3–5 分钟重试，最多 3 次**（大概率第 2 次就通）
3. 3 次仍失败 → 才降级，并在报告中写明"已重试 3 次仍不可用"
4. **降级后仍要回补**：报告发布后若有连接器恢复，用独立 patch 脚本回补缺失模块 + 做交叉复测
   （参考 `patch_midday_20260901.py`：正则从原报告 HTML 提取数值 → 与新源对比 → 追加「复测校验」章节）

## 数据源四级降级链（v3，2026-09-01 实战验证）
MCP 连接器**会整条掉线**（westock-mcp / tdx-connector 同时不可用发生过）。按序尝试，成功即用：

| 层级 | 数据源 | 覆盖 | 关键用法与坑 |
|---|---|---|---|
| 1 | **westock-mcp** | 全量，**含板块主力资金流（东财没有的）** | 首选。工具名 `mcp__westock-mcp__data_*`，直接调用参数会被序列化成 string 报 `Expected object` → **必须用 `DeferExecuteTool` 包装**。板块资金流：`data_sector(mode="ranking", kind="industry", type="mainNetInflow", order="desc")` 一次返回全部行业，字段 `mainNetInflow`/`mainNetInflow5d`/`mainNetInflow20d`/`upCount`/`leader`，**单位是万元** |
| 2 | **tdx-connector** | 全量 | `tdx_quotes` 必须传 `code` + `setcode`（0=深A/1=沪A/33=基金）；深层查询用 `tdx_security_deep_info` 的 `entity_type="A股代码\|港股代码\|美股代码\|基金代码\|指数代码"`（不支持「ETF代码」「A股指数」这类值）。超大结果自动落 `tool-results/` |
| 3 | **hithink-finance MCP（同花顺）** | A股/ETF/指数/行业指数，**不含可转债** | 见下方三条实测要点 |
| 4 | **公开 API** | 腾讯财经（`qt.gtimg.cn` 快照含**可转债**、`web.ifzq.gtimg.cn` 日K/分时）+ 东财（`push2`/`push2ex`） | 见「采集管线 + 硬坑」 |

### hithink-finance MCP 实测要点（v3 新增，务必保留）
1. **指数快照要用 A股版**：`get_a_share_prices_snapshot` 传 `000001.SH,399001.SZ,399006.SZ,...`。
   ⚠️ 传 index 版 `get_a_share_index_prices_snapshot` 且代码带 `.SH` → 报
   `Structured content does not match the tool's output schema: data/data must be object`。
2. **行业指数要用 index 版 + `.TI` 代码**：先 `get_a_share_index_catalog_ths_index_list(tag="industry")` 拿约 250 个 `.TI` 代码表，
   再批量喂给 `get_a_share_index_prices_snapshot`（一次约 30 个）。`.TI` 代码在该工具下**正常工作**。
   → 这是**拿真实领跌板块的最可靠途径**：东财板块榜只能拿到涨幅前 N，拿不到跌幅榜（见硬坑 3）。
3. **不含可转债**：传任意沪/深转债（如 `sh113050`/`sz128101`）报同样的 schema 错。可转债走腾讯 `qt.gtimg.cn/q=sh113050,sz128101`，
   返回 **GBK 编码**，需 `iconv -f GBK -t UTF-8`（或 Python `decode("gbk")`）。

## 采集管线（collect 脚本）+ 关键踩坑（务必保留）
数据源：腾讯财经 `qt.gtimg.cn`（快照）、`web.ifzq.gtimg.cn`（分时/日K）、东方财富 `push2`/`push2delay`（板块/涨跌家数/资金流）、`push2ex`（涨停/跌停池）、东方财富要闻流。
**三个硬坑（已固化进 collect 脚本，勿删）：**
1. **东财 push2 的 IPv6 路由不通 → 强制 IPv4**：`socket.getaddrinfo` 覆写为 `AF_INET`，否则 `HTTP 000`。
2. **`fs` 参数空格必须编码为 `+`**（用 `%20` 会被拒返回空响应）；用 `urllib.parse.quote(fs, safe="+")`。
3. **限流远比文档严重 → `pz<=90` 且只用第 1 页**：实测 `pz=200` 被拒、`pn>=2` 被拒、`po=0`（升序）被拒。
   → 后果：**东财板块榜拿不到真实领跌板块**（总共 496 个行业/概念，只能拿到涨幅前 90）。
   → 解法：**领跌板块改从 hithink 行业指数 `.TI` 全量拉取后在本地排序**（67 个行业指数足够覆盖），涨幅榜两者互为交叉验证。
   在 `push2.eastmoney.com` 与 `push2delay.eastmoney.com` 两 host 间兜底重试。
**涨跌家数**：用"全A逐页精确计数（pz=100）"，**不要用行业板块成分股求和**（跨层级重复计数不可靠）。
**分时**：`minute/query` 返回的是**当日**数据（与传入 date 无关），故采集脚本必须在**交易日当天 12:00 前后**运行，不能回补历史。

### Windows / 沙箱环境硬约束（v3 新增，踩过就别再踩）
1. **Python 直连 DNS 会失败**（`socket.gaierror: [Errno 11001] getaddrinfo failed`），而 `curl` 子进程度假正常解析。
   → **所有 HTTP 一律走 `subprocess.run(["curl","-s","-m","30",url,"-o",path])`**，不要 `urllib`/`requests`。
   → 建议封装带指数退避的重试（实测 5 次重试可救回大部分东财限流）：
   ```python
   def curl(url, name, tries=5):
       p = os.path.join(TMP, name)
       for i in range(tries):
           subprocess.run(["curl","-s","-m","30",url,"-o",p], check=False)
           try:
               with open(p, "r", encoding="utf-8") as f:
                   j = json.load(f)
               if j: return j
           except Exception: pass
           time.sleep(1.2 * (i + 1))
       print(f"[FAIL] {name}"); return None
   ```
2. **`/tmp` 不可写**（`No such file or directory`）→ 临时数据落工作区 `.tmpdata/`，用完清理。
3. **代理 127.0.0.1:7897 常被拒**（`WinError 10061`）且无 `HTTP_PROXY` 环境变量 → 不要依赖代理，直接 curl 直连反而通。
4. **长采集脚本会撞前台超时** → 直接 `run_in_background=true` 起，用 TaskOutput 取回，别反复 sleep 轮询。

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
  - 代码前缀：`sh`/`sz` + 6 位。**可转债同样支持**（如 `sh113050`、`sz128101`），走腾讯 `qt.gtimg.cn` 取价；沪市可转债代码段为 `113xxx`/`110xxx`/`111xxx`，深市为 `128xxx`/`127xxx`/`123xxx`。
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

## v4 输出缺陷清单（2026-09-02 实测，全部已修进脚本，勿重犯）

生成后**必须扫一遍这几处**，它们都是模板里"换了行情就露馅"的硬编码：

| # | 症状 | 根因 | 修正 |
|---|---|---|---|
| 1 | 证券名称全乱码（`\ufffd\ufffd\u05a4…`） | `get()` 默认 `decode="utf-8"` 解腾讯 GBK，因 `errors="replace"` 不抛异常而静默损坏 | `collect_snapshot()` 显式 `decode="gbk"`；`get()` 增加编码自适应（严格 UTF-8 失败或出现 U+FFFD → 退回 GBK） |
| 2 | 走势叙事把「低开→探底→回升」写成「**冲高**后回落」 | 只判断 `hi_first`（高点在低点前），没看收盘相对低点的回升 | 新增 `bounce = am_pct - lo_pct`，形成三分支：`bounce>0.3` → 低开探底回升；否则才叫冲高回落 |
| 3 | 领涨 TOP5 被「地面兵装Ⅲ / 地面兵装Ⅱ」两条重复占位 | 东财行业板块含 Ⅰ/Ⅱ/Ⅲ 多级同名录入 | `_dedup_sectors()`：去罗马数字后缀后重名 + 涨跌幅/净额/涨跌家数/龙头全一致 → 只留一个 |
| 4 | 持仓「主力净流入 +720 万」却给「主力净流出背景下不宜加仓」 | 操作建议的 `else` 分支写死"净流出"文案 | 拆成净流入 / 净流出 / 数据缺失三支 |
| 5 | 「创业板指**涨 -2.18%**」语病；量价结论写死「指数涨、个股跌」 | 文案硬编码 | `build_divergence_note()` 按创业板指实际方向三分支，跌时用 `abs()` |
| 6 | 「核心事件：上午无重大宏观催化」但表里 14 条资讯 | `CORE_KEY` 只认恒大/临港/以旧换新等某日特定事件 | 泛化为央行/财政/统计/监管/关税等通用词；无命中时改述"未出现央行/财政/统计口径的重大事件" |
| 7 | 「指数红、账户绿」「指数与个股**严重背离**」出现在指数同跌日 | 未判断指数方向 | 仅在 `上证 am_pct >= 0` 时才用"指数红"表述，否则说"同步走弱" |
| 8 | 全 7 指数收跌却写「上证50 **领涨**」 | 同上 | `strong_word = "相对抗跌" if strong.am_pct < 0 else "领涨"` |
| 9 | 科创50 明明 V 型修复却说"日内回吐" | 只看 `retrace` | 增加 `hi_t > lo_t 且 bounce>0.8` → 描述为"探底回升、日内动能最强" |
| 10 | 资讯只筛出 3 条 | `KEY` / `IMPACT` 只覆盖某一天的事件词 | 重写为七类通用词表（货币/财政产业/宏观数据/监管市场/行业主题/公司业绩/外部），`IMPACT` 具体词排在泛化词之前 |

**同一类坑的第二批（2026-08-25 回归测试发现，均已修）：**

| # | 症状（出现在"指数跌但个股普涨"日） | 修正 |
|---|---|---|
| 11 | 7 指数全绿却输出"指数与多数个股**同向走强**，行情具备一定广度支撑" | 宽度联动改为**「指数方向 × 上涨占比」二维判定**：`上证<-0.1 且 占比过半` → "个股普涨而指数承压，权重股拖累、中小盘活跃" |
| 12 | "量价同步走弱……**仅 66%** 个股上涨，指数与个股同向走弱" | ①"仅/有"由占比阈值切换（<45% 才用"仅"）；②新增第四分支：`指数跌 且 占比>=55%` → **权重拖累型分化**，明确"不应按普跌市处理" |
| 13 | 风险点"市场宽度偏弱，指数与个股**同步下行**。仅 66.4% 个股上涨" | 二维判定：占比>=55% → 降级为"中"风险，改写为"指数承压而个股普涨，存在权重股补跌拖累指数的风险" |
| 14 | 操作总纲"其余在 **66%** 上涨占比环境下追高胜率低" | 占比>=45% → 切换为"轻指数、重个股"表述 |
| 15 | "盘中**冲高 -0.10%**"（高点本身仍是负的） | `hi_pct < 0` 时改述"盘中最高仅 -0.10%" |

### 回归测试（改完脚本必做）
改动 `generate_midday_review.py` 后，用**不同行情形态**渲染确认分支都触发正确、无未替换占位。
**首选仓库自带的四态夹具回归**（不依赖本机历史数据，任何机器可跑）：
```bash
cd <skill>/tests && python run_tests.py    # 采集器落盘契约 + 四态渲染，全绿退出 0
```
四份夹具覆盖 SKILL 规定的四种形态：普涨（82%）/ 分化（46%）/ 普跌（18%）/
**权重拖累**（`weight_drag_merged.json`：7 大指数翻跌 -0.6% + 66% 个股上涨，
专测「指数方向 × 上涨占比」二维判定，断言"个股普涨而指数承压 / 权重拖累型分化"必现）。

有本机历史 merged 数据时，可额外加跑真实日期回归：
```bash
for d in 20260821 20260824 20260825; do
  python generate_midday_review.py --date ${d:0:4}-${d:4:2}-${d:6:2} \
    --merged midday_merged_$d.json --breadth breadth_$d.json --out .tmpdata/regress_$d.html
  grep -o "{[A-Z_][A-Za-z_0-9]*}" .tmpdata/regress_$d.html | sort -u   # 应为空
done
```
**务必覆盖至少四种形态：普涨、普跌、指数跌+广度强（权重拖累）、分化震荡。**
只测当天数据会漏掉一多半分支（2026-09-02：当天修复 10 处后，仅测当天全绿，
一跑 8/25 历史数据立刻又暴露 5 处"指数跌但个股普涨"分支的矛盾）。

## 变更记录
- **v4（2026-09-02）**：修复上表 10 项输出缺陷（编码乱码、走势叙事三分支、板块去重、持仓操作建议与资金事实一致、涨跌幅语病条件化、核心事件与资讯关键词泛化、背离/领涨表述按指数方向条件化）；新增 `--mcp-note` 参数，用于把本期 MCP 实际使用情况（含 hithink 交叉校验与精度说明）写进文末「数据来源」；tests 升级**四态**回归（新增 `weight_drag` 权重拖累夹具，专测「指数方向 × 上涨占比」二维判定），`deploy_to_github.py` 排除 `*_review.html` 测试产物。
- **v3.1（2026-09-01）**：新增「⚠️ 先重试，再降级」节 —— MCP 掉线是间歇性的（12:05 断、13:01 自愈），必须先探测+重试 3 次再降级，并给出降级后回补 + 交叉复测的 patch 脚本范式；westock 补充板块主力资金流用法与「单位万元」。
- **v3（2026-09-01）**：新增「数据源四级降级链」+ hithink-finance MCP 实测要点（`.SH` 走 A股版 / `.TI` 走 index 版 / 不含可转债）；修正东财 `pz` 上限 `100→90` 且仅第 1 页可用，并给出「领跌板块改从 hithink 行业指数本地排序」的解法；新增 Windows/沙箱硬约束（Python DNS 失败走 curl、`/tmp` 不可写用 `.tmpdata/`、代理常被拒、长脚本后台跑）；新增降级披露红线；`holdings.json` 补充可转债代码段支持。
- **v2**：时间一致性三模式（`strict-midday`/`late-snapshot`/`render-archive`）、数据质量闸门、事实层与条件叙事红线、持仓外置到 `holdings.json`。
