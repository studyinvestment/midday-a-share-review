# midday-a-share-review

A股午间复盘「结构固化」模板：一套 **date-agnostic** 的采集 + 渲染脚本，产出结构一致、渲染一致、逻辑一致的自包含 HTML 报告（内联 SVG，无外部依赖）。

## 两脚本
- `scripts/collect_midday_data.py` — 交易日 12:00 前后采集行情（腾讯财经 + 东方财富公开 API 降级，无需 MCP）。
- `scripts/generate_midday_review.py` — 渲染 8 大模块 HTML 报告，叙事全部数据驱动自动生成。

## 8 大模块
指数行情 / 走势叙事 / 板块TOP5 / 量能 / 资讯 / 涨停梯队 / 持仓诊断 / 下午推演。
详见 `SKILL.md`。

## 快速开始
```bash
cd scripts
python collect_midday_data.py --date 2026-08-21          # 产出 midday_merged_*.json + breadth_*.json
python generate_midday_review.py --date 2026-08-21 \
    --merged midday_merged_20260821.json --breadth breadth_20260821.json
```

## ⚠️ 持仓是「个人私有配置」，不随本模板分发
本仓库**不含任何真实持仓**。持仓通过外部 `holdings.json` 提供：
- 在运行目录或 `scripts/` 同级放 `holdings.json`（自动加载），或运行脚本时加 `--holdings 路径.json`；
- 格式见 `scripts/holdings.example.json`：
  ```json
  {
    "holdings": [["sh600519", "贵州茅台", 1500.00], ["sz159915", "创业板ETF", null]],
    "hold_ctx": {"sh600519": {"sector": "白酒", "note": "白酒龙头"},
                 "sz159915": {"sector": null, "note": "宽基ETF"}}
  }
  ```
- 未提供时，报告「持仓诊断」模块显示占位提示，其余模块照常生成。
- `.gitignore` 已忽略 `holdings.json`，请勿误提交真实持仓。

## 在 WorkBuddy / Codex 中复用
将本仓库内容整体放入：
- WorkBuddy：`~/.workbuddy/skills/midday-a-share-review/`
- Codex：`<项目>/.codex/skills/midday-a-share-review/`（结构一致，SKILL.md + scripts/ 直接可用）

## 设为 GitHub Template
仓库已设计为可复用模板：fork/clone 后填入自己的 `holdings.json` 即可。
