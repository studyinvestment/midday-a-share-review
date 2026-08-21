# 三态回归测试

本目录用于守护 `generate_midday_review.py` 的**叙事逻辑在三类市场行情下都不崩、不空、不错档**。

## 为什么需要它

P2 把叙事从「写死结论」改成「按 `compute_facts()` 事实桶条件触发」。
这一改动最容易在**没测过的行情分支**上出问题（例如普跌日冒出"需进一步验证的温和信号"之类怪话，或整段空白）。
真实采集只覆盖过「分化」一种行情，因此用三份受控夹具补齐普涨 / 普跌的回归覆盖。

## 夹具

`fixtures/*.json`（由 `build_fixtures.py` 生成，已随 Skill 提交，自包含可复现）：

| 夹具 | 上涨占比 | 涨停 | 板块方向 | 验证的档位 |
|---|---|---|---|---|
| `broad_rise_merged.json` | 82% | 60 只 / 最高 6 板 | 全翻红 | breadth_regime=**strong**、zt_regime=**active** |
| `differentiation_merged.json` | 46% | 44 只（真实） | 真实混合 | breadth_regime=**neutral**（保留真实板块与涨停池） |
| `broad_fall_merged.json` | 18% | 8 只 / 最高 2 板 | 全翻绿 | breadth_regime=**weak**、zt_regime=**weak** |

夹具构造时已做合规处理：
- **剔除隐私**：移除真实持仓代码 `sz002378 / sz002185 / sz159622`（snapshot / minutes / kline / fundflow）；
- **类型纠正**：旧采集器的 sector 数值、zt `pct/streak` 等字符串 → float / int，匹配当前生成脚本；
- **丢弃过期键**：`breadth_exact / updown / breadth_from_industry / market_total_stocks / top_gainers / top_losers / market_amount`，避免误用；
- **注入 v2 breadth**：当前生成脚本优先读 `D["breadth"]`（含 `listed_total/valid_total/missing/up/down/flat`），identity 等式成立。

## 运行

```bash
cd tests
python build_fixtures.py        # 如需重新生成夹具（依赖 workspace 真实 merged 作基底）
python run_three_state.py       # 运行回归；全绿退出 0，任一失败退出 1
```

`run_three_state.py` 对每份夹具调用 `../scripts/generate_midday_review.py` 渲染，并断言：
1. 进程退出码 = 0，HTML 正常生成；
2. 各行情下 `breadth_regime` / `zt_regime` 对应的叙事文案分支**正确出现**；
3. HTML 中「上涨占比 X%」与夹具注入宽度一致（容差 1%）；
4. 额外用非周五日期（`2026-08-19`）跑一次分化夹具，验证 weekday 分支（隔夜外盘）。

## 维护

- 若 `compute_facts()` 或 `build_*` 叙事函数调整了档位阈值 / 文案措辞，**同步更新本目录的断言字符串**；
- 新增叙事分支时，优先补一份能触发该分支的夹具或日期，而非只靠手工肉眼检查。
