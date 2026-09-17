# 十一态回归测试

本目录用于守护 `generate_midday_review.py` 的**叙事逻辑在各类市场行情下都不崩、不空、不错档**。

## 为什么需要它

P2 把叙事从「写死结论」改成「按 `compute_facts()` 事实桶条件触发」。
这一改动最容易在**没测过的行情分支**上出问题（例如普涨日冒出"个股层偏普跌"之类怪话，或整段空白）。

真实采集每天只产生**一种**形态，靠肉眼检查最多覆盖当天那一种。
因此用一批受控/真实脱敏夹具把**已知形态矩阵**补满——每发现一个新的错误形态，
就补一份能复现它的夹具 + 负断言（见 SKILL.md 各批次缺陷表）。

> **为什么必须做成矩阵（2026-09-02 / 09-15 两次教训）**：
> 叙事判定是「指数方向 × 上涨占比」**二维**的。
> ① 只测普涨/普跌/分化会漏掉"指数跌 + 个股普涨"的**权重拖累日**（2026-08-25 实测：
> 7 大指数全绿但 66% 个股上涨，旧模板输出"指数与多数个股同向走强"自相矛盾）；
> ② 2026-09-15 用**全部夹具横向比对**才发现模块 06 的方向句在 11 种形态里有 **6 种**落同一句
> 兜底文案"指数与个股方向大体一致"，其中 2 种明确错误——**只跑当天数据永远看不到这个影响面**。
> **只测当天数据会漏掉一多半分支。**

## 夹具（11 份）

`fixtures/*.json`（自包含可复现，已随 Skill 提交）：

| # | 夹具 | 形态 | 上涨占比 / 指数 | 量能 | 主要验证点 |
|---|---|---|---|---|---|
| 1 | `broad_rise_merged.json` | 指数内部分化 × 个股普涨 | 82% / 4 涨 2 跌 1 平 | flat | breadth=**strong**、zt=**active**、开盘"多数高开" |
| 2 | `differentiation_merged.json` | 分化震荡（真实混合） | 46% / 涨（真实） | 真实 | breadth=**neutral**；保留真实板块与涨停池 |
| 3 | `broad_fall_merged.json` | 指数跌 × 个股普跌 | 18% / 跌 | flat | breadth=**weak**、zt=**weak**、同步承压 |
| 4 | `weight_drag_merged.json` | **指数跌 × 个股普涨**（权重拖累） | 66% / 7 指数全跌 -0.60% | flat | 二维判定：strong + 指数跌 → "方向相反、权重股拖累特征明显" |
| 5 | `bull_resonance_merged.json` | 指数红 × 个股普涨共振 | 66% / 全涨 +0.40% | flat | 指数与个股**同向走强**；负断言"全线高开"（缺陷 #37） |
| 6 | `index_flat_bull_merged.json` | 指数平盘 × 个股普涨 | 66% / 平（创业板 -0.15%） | flat | 平收分支"指数大体走平、个股普涨"；负断言"个股弱、量能平" |
| 7 | `expanded_bear_merged.json` | **放量普跌** | 33% / 跌 | **expanded 117%** | 量能档须为 expanded；负断言"缩量 / 量能亦未放大 / 量能仍不能放出" |
| 8 | `low_open_recover_merged.json` | 低开→探底→回升但**全程未翻红** | 20.1% / 7 指数 0 涨 7 跌 | flat 111% | 负断言"跌幅收敛并翻红"；`weak` 分支写"指数与个股同步承压"（缺陷 #36） |
| 9 | `open_high_selloff_merged.json` | **开盘即高点**、单边下行 | 6.3% / 跌 | expanded 136% | 负断言"带动指数快速拉升 / 冲高后震荡回落"（缺陷 #26） |
| 10 | `index_split_hold_merged.json` | 指数内部方向分化 × 个股普涨 | 64.4% / 3 涨 4 跌 | flat 107% | 分化×普涨二维象限（缺陷 #28）；retrace 仅 0.01pct |
| 11 | `index_split_bear_merged.json` | 指数涨跌互现 × 个股普跌 | **27.9%** / 3 涨 2 跌 1 平 1 未知 | flat | 分化×普跌二维象限；负断言"指数与个股方向大体一致"（缺陷 #34/#36） |

> **D 级形态说明（第 11 态）**：2026-09-15 真实采集件只有 6 只指数落到 `minutes`（1 只缺分时），
> 渲染器按「缺失不计入方向统计」处理，故该夹具刻意为**指数方向不完整**的形态，
> 用来锁住 `idx_dir()` 在 flat/缺失混合下的行为。

## 运行

```bash
cd tests
python build_fixtures.py        # 如需从历史基底重建 1–6 号夹具（依赖 MIDDAY_BASE）
python run_tests.py             # 一键跑全部回归（采集器落盘契约 + 十一态渲染）；全绿退出 0
# 或分开跑：
python test_collector_contract.py   # 采集器落盘契约（成功/失败均必写文件 + v2 关键字段）
python run_three_state.py           # 渲染器十一态回归（文件名沿袭三态时期，未改名）
```

> Windows 注意：默认控制台编码为 GBK，中文输出易报 `UnicodeEncodeError`。
> 各脚本已内置 `sys.stdout.reconfigure(encoding="utf-8")`，`run_tests.py` 也会注入
> 环境变量 `PYTHONUTF8=1`。若单独运行仍报错，请显式加：
> `set PYTHONUTF8=1 && python xxx.py`

`run_three_state.py` 对每份夹具调用 `../scripts/generate_midday_review.py` 渲染，并断言：
1. 进程退出码 = 0，HTML 正常生成；
2. 各行情下 `breadth_regime` / `zt_regime` / 二维判定对应的叙事文案分支**正确出现**；
3. **负断言**：该形态下**明确不该出现**的文案一句都不出现（CASES 元组第 5 项，当前共 31 条）；
4. HTML 中「上涨占比 X%」与夹具注入宽度一致（容差 1%）；
5. 额外用非周五日期（`2026-08-19`）跑一次分化夹具，验证 weekday 分支（隔夜外盘）。

### ⚠️ 夹具口径提示（2026-09-15 立）

**断言与形态描述一律以 `am_pct` 为准**，不要读夹具 JSON 里的 `snapshot[code]["pct"]`——
两者长期不一致且无人察觉。渲染器真正使用的分时口径是：

```python
am_pct = (minutes[code][-1]["p"] / snapshot[code]["prev_close"] - 1) * 100
```

`broad_rise` 夹具按 `snapshot.pct` 会被读成"4 跌 3 涨"，按 `am_pct` 复算实为 **4 涨 2 跌 1 平**。
诊断形态时请务必用上式复算，否则会得出错误的形态描述、进而写出错误的断言。

## 制作新夹具（推荐用真实脱敏模式）

发现新形态时，**优先**用当天的真实采集件固化，保真度高于从历史基底派生：

```bash
# 1) 让脱敏名单可被解析（二选一）：
set MIDDAY_PRIVACY=sz123456,示例名称,sh111111,某主题ETF
#    或把 holdings.json 放到 skill 目录 / 当前工作目录（与渲染器共用同一份外部配置）

# 2) 从真实采集件生成（自动脱敏 + schema 校正 + 残留自检）
python build_fixtures.py --real <midday_merged_YYYYMMDD.json> \
       --regime <形态名> --out <形态名>_merged.json --note "<形态一句话描述>"

# 3) 在 run_three_state.py 的 CASES 里补一条：正断言 + 负断言，然后跑 run_tests.py
```

**脱敏覆盖范围**（`coerce_base()`，缺一即可能泄露）：
`snapshot` / `minutes` / `kline` / `fundflow` / **`sector_industry`+`sector_concept` 的 `lead` 与 `lead_code`** / **`zt_pool`+`dt_pool` 的个股行**。

> **2026-09-15 真实事故**：旧版只清前四处，**漏了 sector 的龙头字段** ——
> 一份真实夹具里"钨"板块的 `lead` / `lead_code` 正是持仓个股（该字段出现在 JSON **第 1 行**，
> 肉眼 review 完全不可见）。现已补上并加自检。
> **隐私名单不在源码里硬编码**（`PRIVACY = _privacy_codes()`，改由环境变量或 `holdings.json` 外部注入）：
> 硬编码既会把真实持仓写进公开文件，又必然滞后于持仓变动等于没保护。
> 名单为空时 `--real` 模式**拒绝出件**（退出码 4），除非显式 `--allow-no-privacy`——
> 宁可挡住一次合法运行，也不要"以为在脱敏、其实一条都没剔"的静默泄露。

## 采集器落盘契约测试（test_collector_contract.py）

针对 **2026-08-24 发现的 P0 bug**（采集成功路径只打印"完成"却不写文件，
导致下游误以为采集成功、实际拿到旧文件/空文件）的回归锁。

通过采集器的离线自检开关 `--selftest` / `--selftest-fail` 走**真实** `save_outputs`
落盘代码（不联网、秒级），断言：
- **成功路径**：退出码 0，且 `midday_merged_{DC}.json` 必然写出，并含 v2 关键字段
  `quality`（含 `level`）/ `sources` / `meta`（含 `as_of`）/ `breadth`；
- **失败路径**：退出码非 0，但文件**仍写出**（"仍落盘供排查"契约，不得静默丢失）。

任何改采集器落盘逻辑的人，都必须先让本测试通过再合并。

## 维护

- 若 `compute_facts()` 或 `build_*` 叙事函数调整了档位阈值 / 文案措辞，**同步更新本目录的断言字符串**；
- 新增叙事分支时，优先补一份能触发该分支的夹具或日期，而非只靠手工肉眼检查；
- **夹具不仅要覆盖形态，还要对每个模块的关键结论句都设断言**（2026-09-15 教训）——
  `index_split_bear` 首轮只写正断言，漏了模块 07 的板块句，全绿却仍带着缺陷；
- 改动夹具构造逻辑后，**重新跑 `build_fixtures.py` + `run_tests.py`**，并把新的 `*_merged.json` 一并提交
  （`.gitignore` 只忽略 `*_review.html` 测试产物）。
