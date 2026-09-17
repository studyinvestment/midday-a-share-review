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

## 在 WorkBuddy / Codex / 其他 Agent 中复用

本仓库遵循 **Agent Skills 开放标准**（目录内 `SKILL.md` + `name`/`description` frontmatter），
**同一份文件无需改动即可被多个 Agent 加载**。安装 = 把整个目录放到对应 skills 路径下：

| 环境 | 作用域 | 安装路径 |
| --- | --- | --- |
| WorkBuddy | 用户级 | `~/.workbuddy/skills/midday-a-share-review/` |
| Codex CLI | 用户级（所有项目） | `~/.agents/skills/midday-a-share-review/` |
| Codex CLI | 项目级（仅本仓库） | `<repo>/.agents/skills/midday-a-share-review/` |
| Cursor / Gemini CLI / GitHub Copilot / OpenCode / Cline 等 | 项目级 | `.agents/skills/midday-a-share-review/` |

```bash
# Codex 项目级（在仓库根执行）
mkdir -p .agents/skills && cp -r /path/to/midday-a-share-review .agents/skills/

# Codex 用户级
mkdir -p ~/.agents/skills && cp -r /path/to/midday-a-share-review ~/.agents/skills/
```

安装后用 `$midday-a-share-review` 显式调用，或直接说"午间复盘"由 description 隐式匹配（`agents/openai.yaml` 提供 Codex 侧展示元数据）。

> **路径说明**：项目级已收敛到中立目录 `.agents/skills/`（Codex 自当前目录向上搜索至仓库根）。
> Codex 用户级官方为 `~/.agents/skills/`，同时兼容 `$CODEX_HOME/skills/`（默认 `~/.codex/skills/`）——
> **两处都存在会被各加载一次，建议只保留一处**，避免同一 skill 出现两个版本。
> **Claude Code 是例外**：只读 `.claude/skills/` 与 `~/.claude/skills/`，不读 `.agents/skills/`。

### ⚠️ 跨环境的 MCP 差异（影响数据精度，务必知悉）

`SKILL.md` 的「四级降级链」第 1–3 级是 **WorkBuddy 连接器专属**（`westock-mcp` / `tdx-connector` / `hithink-finance`）。
**在 Codex 或任何没有这些连接器的环境里，实际只有第 4 级可用** —— 即两个脚本内置的
**腾讯财经 + 东方财富公开 API**，开箱即用、不需要任何 MCP 或 API key。

代价是板块资金流 5/20 日维度、部分指数快照等字段拿不到（报告文末 `sources` 会如实记录降级）。
**但脚本层不依赖任何 MCP**，因此两种环境下都能跑通、产出结构一致的报告。

## 运行模式与数据质量（v2）
- 采集器 `--mode`：`strict-midday`（默认，11:30–13:00 才采）/ `late-snapshot`（13:00 后，板块等标"当前快照"）/ `render-archive`（用已存 JSON 重渲，唯一可信历史补跑）。
- 采集器产出 `quality(level/errors/warnings)` + `sources(status/as_of)`；核心数据缺失→非零退出、不生成报告。
- 涨跌家数分母用 `valid_total`（剔除无报价/停牌），非 `listed_total`。

## 测试与质量门禁

改动采集器或渲染器后，务必先跑回归再合并：

```bash
cd tests && python run_tests.py
```

- `test_collector_contract.py`：**采集器落盘契约**（针对 2026-08-24 P0 bug 的回归锁）。
  通过离线 `--selftest` / `--selftest-fail` 走真实 `save_outputs` 落盘，断言
  成功/失败两种结局下 `midday_merged_{DC}.json` 必然写出且含 v2 关键字段
  （`quality` / `sources` / `meta.as_of` / `breadth`）。**这是自动化的假成功防线**。
- `run_three_state.py`：渲染器**十二态**回归（普涨 / 分化 / 普跌 / 权重拖累 / 普涨共振 / 指数平×普涨 /
  放量普跌 / 低开探底回升未翻红 / 开盘即高点单边下行 / 指数内部分化×普涨 / 指数涨跌互现×个股普跌 /
  **科技成长普涨共振** + 非周五分支）。
  每态除正向断言外还带**负断言**（禁止出现的文案），共 36 条。
  各夹具专测「指数方向 × 上涨占比」二维判定，防止"指数跌但个股普涨"日输出"同向走强/普跌"等自相矛盾文案
  （2026-08-25 / 09-02 / 09-14 / 09-15 / 09-17 五次实测修复）。
  > 夹具的**方向口径以 `am_pct`（分时末点 ÷ 前收）为准**，与夹具 `snapshot[].pct` 可能长期不一致 —— 见 harness 顶部「夹具口径提示」。

> **Windows UTF-8 兼容**：默认控制台为 GBK，中文输出易报 `UnicodeEncodeError`。
> 各脚本已内置 `sys.stdout.reconfigure(encoding="utf-8")`，`run_tests.py` 也会注入
> 环境变量 `PYTHONUTF8=1`。若单独运行仍报错：`set PYTHONUTF8=1 && python xxx.py`。

## 上传到 GitHub（仓库维护，非运行时依赖）

`tools/deploy_to_github.py` 走 REST API（`api.github.com`）建仓并上传，**不用 `git push`**——
规避 `github.com:443` 上行 TLS 不稳。属维护工具，不随运行时 skill 加载：

```bash
# ① 先只评审：跑脱敏闸门 + 列出待上传清单，零外部动作
python tools/deploy_to_github.py --owner <user> --repo midday-a-share-review --public --audit-only

# ② 通过后再发布（token 省略即自动从 git credential 取，不进命令行历史）
python tools/deploy_to_github.py --owner <user> --repo midday-a-share-review --public --template
```

**发布前脱敏闸门（在 `create_repo` 之前执行）**：扫描全部待上传文本文件，命中真实持仓代码/名称即
**中止发布**（退出码 5）；未取得脱敏名单时同样中止（退出码 6）。名单来源：`MIDDAY_PRIVACY`
环境变量 → `holdings.json`，**源码内不保留任何真实持仓**。

## 设为 GitHub Template
仓库已设计为可复用模板：fork/clone 后填入自己的 `holdings.json` 即可。
