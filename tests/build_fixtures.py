# -*- coding: utf-8 -*-
"""四态夹具构造器（普涨 / 分化 / 普跌 / 权重拖累）。

以 workspace 中真实的 midday_merged_20260821.json 为「结构基底」，
但做一次 v2 schema 校正后，再按四种市场状态注入受控的关键字段：
  - breadth（v2：listed_total/valid_total/missing/up/down/flat）→ 决定 breadth_regime 三档
  - zt_pool（涨停家数 / 最高连板）→ 决定 zt_regime 三档
  - sector 涨跌方向（普涨/普跌翻转符号）→ 决定 persist/资金主线分支
  - snapshot+minutes 指数价格（weight_drag 翻转 7 大指数收跌）→
    决定「指数方向 × 上涨占比」二维判定（2026-08-25 / 2026-09-02 两次实测修复的核心分支）

四种形态覆盖矩阵（对应 SKILL.md「务必覆盖至少四种形态」）：
  broad_rise       指数涨 + 个股普涨(82%)   → breadth strong / 同向走强
  differentiation  分化震荡(46%)            → breadth neutral
  broad_fall       指数跌 + 个股普跌(18%)   → breadth weak / 同步下行
  weight_drag      指数跌 + 个股普涨(66%)   → breadth strong + 上证收跌 → 权重拖累型分化

校正项（旧采集器 → 当前 generate 期望）：
  ① 类型纠正：sector.netflow/pct、zt.pct/streak 等由字符串转为 float/int
  ② 剔除隐私：移除真实持仓（snapshot / minutes / kline / fundflow **以及 sector 行的 lead / lead_code、
     zt_pool / dt_pool 里的个股**）。脱敏名单**不在本文件硬编码**，按序取
     `MIDDAY_PRIVACY` 环境变量 → skill 目录或工作目录下的 `holdings.json`；
     两者皆空时 `--real` 模式直接拒绝执行（退出码 4），防止静默泄露。
     2026-09-15 修复：旧版只清前四处，**漏了 sector 的 lead 字段**——一份真实夹具里
     "钨"板块的龙头值正是持仓个股，已随本次修复清为中性值。
  ③ 丢弃过期键：breadth_exact / updown / breadth_from_industry / market_total_stocks /
     top_gainers / top_losers / market_amount（与当前生成脚本无关，避免误用）
  ④ 注入 meta.mode = strict-midday

用法：
  python build_fixtures.py
产物：tests/fixtures/{broad_rise,differentiation,broad_fall,weight_drag}_merged.json
"""
import json, copy, os, re, sys

# 结构基底来源：优先用环境变量 MIDDAY_BASE，其次回退到本仓库上层目录（skill 与采集数据
# 平级放置时的布局）。刻意【不写死个人机器路径】（公开模板防泄漏）；本地重建夹具时用：
#   MIDDAY_BASE="<某次真实采集的 midday_merged_*.json 路径>" python build_fixtures.py
def _resolve_src():
    env = os.environ.get("MIDDAY_BASE")
    if env and os.path.exists(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    # 相对回退仅覆盖"采集数据放在 skill 仓库上一级"这一种布局，其余一律走 MIDDAY_BASE
    candidates = [
        os.path.join(here, "..", "..", "midday_merged_20260821.json"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
    return None

SRC = _resolve_src()
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "fixtures")
os.makedirs(OUTDIR, exist_ok=True)

# ============ 隐私代码来源（2026-09-15 改为「外部注入」，不再把真实持仓写进仓库）============
# 历史做法：在源码里硬编码 PRIVACY 常量。问题有二——
#   ① 公开仓库会因此暴露真实持仓代码（该常量本身就把代码写进了公开文件）；
#   ② 持仓会变动，硬编码必然滞后，实际已失效（旧列表不含当前持仓，等于没有保护）。
# 现行做法：按序解析候选来源，**仓库内不保留任何真实持仓代码**：
#   ① 环境变量 MIDDAY_PRIVACY（逗号分隔，支持 sz123456 或 123456 两种写法）
#   ② skill 目录或其上一级的 holdings.json（与渲染器共用的同一份外部配置）
# 两处都取不到时 PRIVACY 为空 —— 此时 `--real` 模式会**拒绝执行**（除非显式 --allow-no-privacy），
# 避免"以为在脱敏、其实一条都没剔"的静默失败。
def _norm_code(c):
    c = (c or "").strip().lower()
    return c[-6:] if len(c) >= 6 and c[-6:].isdigit() else c


def _privacy_codes():
    codes = [c for c in (os.environ.get("MIDDAY_PRIVACY") or "").split(",") if c.strip()]
    if not codes:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "..", "holdings.json"),
                     os.path.join(here, "holdings.json"),
                     os.path.join(os.getcwd(), "holdings.json")):
            if os.path.exists(cand):
                try:
                    hj = json.load(open(cand, encoding="utf-8"))
                    out = []
                    for row in hj.get("holdings", []):
                        if not row:
                            continue
                        out += [str(x) for x in row[:2] if x]   # 代码 + 名称，两者都参与剔除
                    codes = out
                except Exception:
                    codes = []
                if codes:
                    break
    return tuple(dict.fromkeys(_norm_code(c) for c in codes if _norm_code(c)))

PRIVACY = _privacy_codes()
_PRIV6 = {p[-6:] for p in PRIVACY}
# 与 generate_midday_review.py 的 IDX 常量保持一致（7 大指数）
IDX_CODES = ("sh000001", "sz399001", "sz399006", "sh000688", "sh000016",
             "sh000905", "sh000852")
STALE_KEYS = ("breadth_exact", "updown", "breadth_from_industry",
              "market_total_stocks", "top_gainers", "top_losers", "market_amount")

def to_f(x):
    try:
        return float(x)
    except Exception:
        return 0.0

def to_i(x):
    try:
        return int(float(x))
    except Exception:
        return 0


def _is_priv(code):
    """隐私命中判定：按后 6 位比对，兼容 sz123456 / 123456.SZ / 123456 三种写法。"""
    return bool(code) and _norm_code(str(code))[-6:] in _PRIV6


def leak_scan(blob, tokens=None):
    """残留自检：在文本中查找隐私 token。

    **6 位纯数字必须按「数字边界」匹配**——早期实现用朴素子串 `token in blob`，
    会把 JSON 里任意长数字的尾段当成代码命中。实测案例：某持仓代码与板块资金流
    `netflow: -1234568816.0` 的尾段完全相同，导致脱敏**明明成功**却报
    `✗ 脱敏失败`（退出码 3）。安全闸门的误报会训练使用者忽略告警，属实质缺陷。

    规则：6 位纯数字代码前后不得紧邻数字或小数点（`(?<![\\d.])123456(?!\\d)`），
    即 `"lead_code": "123456"`、`sz123456` 仍会命中，而 `-1234568816.0` 不会。
    非数字 token（中文名、带市场前缀的写法）沿用子串匹配。
    """
    tokens = PRIVACY if tokens is None else tokens
    hits = []
    for t in tokens:
        t = str(t)
        if re.fullmatch(r"\d{6}", t):
            if re.search(r"(?<![\d.])" + t + r"(?!\d)", blob):
                hits.append(t)
        elif t and t in blob:
            hits.append(t)
    return hits


def coerce_base(d):
    """把旧采集器的类型/结构校正为当前 generate 期望的 v2 形态。"""
    # —— snapshot ——
    for c, s in list(d.get("snapshot", {}).items()):
        if _is_priv(c):
            d["snapshot"].pop(c, None); continue
        for k in ("prev_close", "price", "pct", "open", "high", "low",
                 "amount_wan", "volume_hand", "chg", "amplitude", "vol_ratio", "pb", "pe"):
            if k in s: s[k] = to_f(s[k])
    # —— minutes ——
    for c, ms in list(d.get("minutes", {}).items()):
        if _is_priv(c):
            d["minutes"].pop(c, None); continue
        for m in ms:
            for k in ("p", "v", "amt"):
                if k in m: m[k] = to_f(m[k])
    # —— kline ——
    for c, ks in list(d.get("kline", {}).items()):
        if _is_priv(c):
            d["kline"].pop(c, None); continue
        for k in ks:
            for fld in ("open", "close", "high", "low", "vol"):
                if fld in k: k[fld] = to_f(k[fld])
    # —— fundflow：全量剔除（含隐私持仓资金流）——
    d.pop("fundflow", None)
    # —— sector：类型纠正 + 龙头字段脱敏 ——
    # 2026-09-15 修复一处真实泄露：板块行的 lead / lead_code 也可能指向真实持仓
    # （实例：某真实采集件里"钨"板块的龙头字段正是持仓个股），而旧脱敏只清了
    # snapshot / minutes / kline / fundflow 四处，**漏掉 sector 这条路径**。
    for sec in ("sector_industry", "sector_concept"):
        for r in d.get(sec, []):
            r["pct"] = to_f(r.get("pct"))
            r["netflow"] = to_f(r.get("netflow"))
            r["up"] = to_i(r.get("up"))
            r["down"] = to_i(r.get("down"))
            r["lead_pct"] = to_f(r.get("lead_pct"))
            if _is_priv(r.get("lead_code")) or _is_priv(r.get("lead")):
                r["lead"], r["lead_code"] = "示例个股", "000000"
    # —— zt_pool / dt_pool：类型纠正 + 隐私剔除（涨停/跌停股也可能是持仓）——
    for key in ("zt_pool", "dt_pool"):
        rows = d.get(key, [])
        kept = []
        for r in rows:
            if _is_priv(r.get("code")) or _is_priv(r.get("name")):
                continue
            kept.append(r)
        d[key] = kept
    for r in d.get("zt_pool", []):
        r["pct"] = to_f(r.get("pct"))
        r["streak"] = to_i(r.get("streak"))
        r["open_times"] = to_i(r.get("open_times"))
        r["first_time"] = str(r.get("first_time") or "").zfill(6)
    for r in d.get("dt_pool", []):
        r["pct"] = to_f(r.get("pct"))
    # —— 丢弃过期键 ——
    for k in STALE_KEYS:
        d.pop(k, None)
    # —— meta ——
    d["meta"] = {"schema_version": 2, "report_date": "2026-08-21",
                 "collected_at": "2026-08-21 11:30:00", "mode": "strict-midday"}
    return d


def v2_breadth(up, down, flat, valid, listed, missing):
    return {"listed_total": listed, "valid_total": valid, "missing": missing,
            "up": up, "down": down, "flat": flat,
            "up_ratio_valid": (up / valid) if valid else 0.0,
            "method": "三态夹具合成 v2（受控宽度，用于回归测试）"}


def flip_sectors(rows, regime):
    out = []
    for r in rows:
        r = dict(r)
        pct = r.get("pct") or 0.0
        nf = r.get("netflow") or 0.0
        u = int(r.get("up") or 0); d_ = int(r.get("down") or 0)
        tot = max(u + d_, 1)
        if regime == "broad_rise":
            r["pct"] = round(abs(pct) + 1.5, 2)
            r["netflow"] = round(abs(nf) + 8e8, 1)
            r["up"], r["down"] = tot, 0
            r["lead_pct"] = round(abs(r.get("lead_pct") or 0) + 1.0, 2)
        elif regime == "broad_fall":
            r["pct"] = round(-(abs(pct) + 1.5), 2)
            r["netflow"] = round(-(abs(nf) + 8e8), 1)
            r["up"], r["down"] = 0, tot
            r["lead_pct"] = round(-(abs(r.get("lead_pct") or 0) + 1.0), 2)
        out.append(r)
    return out


def flip_index_down(d, target=-0.006, per_code=None):
    """把 7 大指数整体平移至目标涨跌幅（weight_drag 用 -0.6%，bull_resonance 用 +0.4%）。

    generate 的指数涨跌（IM[c]['am_pct']）由 minutes 末点价 ÷ snapshot.prev_close 计算，
    故需同时缩放 minutes 价格与 snapshot 显示字段（price/open/high/low/pct），
    保持日内相对形态不变、仅整体平移至目标涨跌幅。prev_close 不动（作分母基准）。
    per_code：可选 dict，逐指数覆盖 target（index_flat_bull 用——把创业板指单独放到 -0.15%
    以触发 divergence 平收分支，其余指数放 ±0.2% 内平盘）。
    """
    for c in IDX_CODES:
        s = d.get("snapshot", {}).get(c)
        ms = d.get("minutes", {}).get(c)
        if not s or not ms:
            continue
        pc = s.get("prev_close") or 0
        last_p = ms[-1].get("p") if ms else 0
        if not pc or not last_p:
            continue
        tgt = (per_code or {}).get(c, target)
        scale = (pc * (1 + tgt)) / last_p
        for m in ms:
            if "p" in m:
                m["p"] = round(m["p"] * scale, 3)
        for k in ("price", "open", "high", "low"):
            v = s.get(k)
            if v:
                s[k] = round(v * scale, 3)
        if s.get("pct") is not None:
            s["pct"] = round(tgt * 100, 2)
    return d


def make_zt(n, max_streak):
    pool = []
    for i in range(n):
        streak = max_streak if i < 3 else max(2, max_streak - 1)
        pool.append({
            "code": "mock%06d" % i, "name": "测试涨停股%d" % i,
            "pct": 10.0, "price": 0, "first_time": "093500",
            "open_times": 0, "streak": streak, "industry": "测试行业",
        })
    return pool


def real_fixture(src_path, regime, note=""):
    """把真实采集产物脱敏 + schema 校正后固化为夹具（真实形态首选制作法）。

    保真度高于从历史基底派生：保留当日真实的指数/板块/广度/涨跌停/资讯结构，
    仅做三类处理——① 剔除隐私持仓代码；② 类型与 v2 schema 校正；③ 写 meta 注明来源。

    ⚠️ 2026-09-17 修复（缺陷 #40，aliasing 陷阱）：`coerce_base()` 会**原地**把
    `d["meta"]` 重置为硬编码的基底日期（`2026-08-21 11:30:00`），而它收到的 `d`
    就是这里的 `base` 本身（同一对象）。旧代码把 `src_meta = base.get("meta")`
    写在 `coerce_base(base)` **之后**，等于读自己刚被覆盖掉的值 —— 结果所有 `--real`
    夹具的 `meta.report_date` / `collected_at` 长期被写成 2026-08-21，
    与真实来源日期不符（例：`index_split_bear` 源为 2026-09-15、`low_open_recover`
    源为 2026-09-10，meta 却都写 08-21），直接违背本 skill 的 trust 标签
    「时间一致性 / 数据可审计」。**修法：先取 meta，再 coerce。**
    """
    base = json.load(open(src_path, encoding="utf-8"))
    # 必须在 coerce_base() 之前取——它会原地重置 meta（见上方说明）
    src_meta = dict(base.get("meta") or {})
    d = coerce_base(base)
    d["meta"] = {
        "schema_version": 2,
        "report_date": src_meta.get("report_date", ""),
        "collected_at": src_meta.get("collected_at", ""),
        "mode": src_meta.get("mode", "strict-midday"),
        "fixture_regime": regime,
        "fixture": note or f"{regime}（源={os.path.basename(src_path)} 真实采集脱敏）",
    }
    return d


def build(regime):
    base = json.load(open(SRC, encoding="utf-8"))
    d = coerce_base(base)
    # 三种行情的受控宽度档位（valid_total 统一定为 5543，listed 5901，missing 358）
    listed, valid, missing = 5901, 5543, 358
    if regime == "broad_rise":
        up, down, flat = 4544, 550, 449          # 82.0% → strong
        d["breadth"] = v2_breadth(up, down, flat, valid, listed, missing)
        d["sector_industry"] = flip_sectors(d["sector_industry"], regime)
        d["sector_concept"] = flip_sectors(d["sector_concept"], regime)
        d["zt_pool"] = make_zt(60, 6)            # active
    elif regime == "broad_fall":
        up, down, flat = 998, 4322, 223          # 18.0% → weak
        d["breadth"] = v2_breadth(up, down, flat, valid, listed, missing)
        d["sector_industry"] = flip_sectors(d["sector_industry"], regime)
        d["sector_concept"] = flip_sectors(d["sector_concept"], regime)
        d["zt_pool"] = make_zt(8, 2)             # weak
    elif regime == "weight_drag":
        # 权重拖累型：指数跌 + 个股普涨（2026-08-25 实测形态，二维判定修复的分支）
        up, down, flat = 3660, 1700, 183         # 66.0% → breadth strong
        d["breadth"] = v2_breadth(up, down, flat, valid, listed, missing)
        d = flip_index_down(d)                   # 7 大指数翻为收跌
        d["zt_pool"] = make_zt(40, 4)            # 中上活跃（非普涨日的涨停高峰）
        # sector 保持真实混合：权重拖累日板块本就涨跌互现（由指数/宽度刻画结构）
    else:  # differentiation：保留真实混合板块与真实涨停池，仅注入受控中性宽度
        up, down, flat = 2550, 2900, 93          # 46.0% → neutral
        d["breadth"] = v2_breadth(up, down, flat, valid, listed, missing)
        # zt_pool / sectors 保持真实（混合）
    d["meta"]["fixture_regime"] = regime
    return d


def main():
    args = sys.argv[1:]
    if args and args[0] == "--real":
        # 直接用法：把某次真实采集的 midday_merged_*.json 脱敏后固化为夹具。
        # 这是"真实形态"夹具的首选制作法（比从历史基底派生保真）：
        #   python build_fixtures.py --real <midday_merged_YYYYMMDD.json> \
        #          --regime index_split_bear --out index_split_bear_merged.json \
        #          --note "指数涨跌互现×个股普跌(27.9%)，源=2026-09-15真实采集脱敏"
        def _opt(flag, default=None):
            return args[args.index(flag) + 1] if flag in args else default
        src = _opt("--real")
        regime = _opt("--regime", "real")
        out = _opt("--out", f"{regime}_merged.json")
        note = _opt("--note", "")
        if not src or not os.path.exists(src):
            sys.stderr.write(f"✗ 找不到真实采集文件：{src}\n"); sys.exit(2)
        # 失败保护：脱敏名单为空时拒绝出件，避免"以为在脱敏、其实一条都没剔"的静默泄露
        if not PRIVACY and "--allow-no-privacy" not in args:
            sys.stderr.write(
                "✗ 未获取到任何脱敏用持仓代码/名称，已拒绝生成真实夹具。\n"
                "  请设置环境变量 MIDDAY_PRIVACY=\"sz123456,示例名称\"，\n"
                "  或把 holdings.json 放到 skill 目录（当前工作目录亦可）。\n"
                "  确属无持仓可用时，显式追加 --allow-no-privacy。\n")
            sys.exit(4)
        d = real_fixture(src, regime, note)
        path = os.path.join(OUTDIR, out)
        json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        # 脱敏自检：确认隐私代码未残留
        blob = json.dumps(d, ensure_ascii=False)
        leaked = leak_scan(blob)
        print(f"[OK] {regime} -> {out}  指数={len(d.get('snapshot', {}))}  "
              f"板块={len(d.get('sector_industry', []))}  广度={d.get('breadth', {}).get('up_ratio_valid')}  "
              f"隐私残留={leaked or '无'}")
        if leaked:
            sys.stderr.write(f"✗ 脱敏失败，以下代码仍存在：{leaked}\n"); sys.exit(3)
        sys.exit(0)

    if not SRC or not os.path.exists(SRC):
        sys.stderr.write(
            "✗ 找不到结构基底 midday_merged_20260821.json。\n"
            "  请先在某次真实采集后把它放到本目录上级，或设置环境变量 "
            "MIDDAY_BASE 指向该文件，再运行 build_fixtures.py。\n")
        sys.exit(2)
    for reg in ("broad_rise", "differentiation", "broad_fall", "weight_drag"):
        out = build(reg)
        path = os.path.join(OUTDIR, f"{reg}_merged.json")
        json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        br = out["breadth"]
        ratio = br["up"] / br["valid_total"] * 100
        print(f"[OK] {reg:14s} -> {os.path.basename(path)}  "
              f"up_ratio={ratio:.1f}%  zt={len(out['zt_pool'])}  "
              f"ind={len(out['sector_industry'])}  con={len(out['sector_concept'])}  "
              f"news={len(out['news'])}")
    # 第 5 形态：bull_resonance（指数红 × 个股普涨共振，2026-09-04 实测）。
    # 直接以 weight_drag 产物派生，唯一变量 = 指数方向（-0.6% → +0.4%）：
    # 与 weight_drag 成对隔离出「宽度风险条在指数红×普涨日必须消失」这一分支
    # （generate 模块 08 曾因只看指数方向，在该形态误报"宽度偏弱/指数红账户绿"）。
    wd_path = os.path.join(OUTDIR, "weight_drag_merged.json")
    d = json.load(open(wd_path, encoding="utf-8"))
    d = flip_index_down(d, target=0.004)
    d["meta"]["fixture_regime"] = "bull_resonance"
    path = os.path.join(OUTDIR, "bull_resonance_merged.json")
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    br = d["breadth"]
    ratio = br["up"] / br["valid_total"] * 100
    print(f"[OK] {'bull_resonance':14s} -> {os.path.basename(path)}  "
          f"up_ratio={ratio:.1f}%  zt={len(d['zt_pool'])}  (由 weight_drag 派生，指数翻红)")
    # 第 6 形态：index_flat_bull（指数平盘 × 个股普涨，2026-09-08 实测）。
    # 由 weight_drag 派生，唯一变量 = 指数方向（-0.6% → 平盘；创业板指单独放 -0.15%，
    # 触发 build_divergence_note 平收分支——旧模板在此象限无脑写"指数平、个股弱"，
    # 与 66% 上涨自相矛盾，缺陷 #22 残留；bull_resonance 的 +0.4% 走 cyb>=0.3 分支测不到）。
    d = json.load(open(wd_path, encoding="utf-8"))
    d = flip_index_down(d, per_code={
        "sh000001": 0.0015, "sz399001": 0.0005, "sz399006": -0.0015,
        "sh000688": -0.0010, "sh000016": 0.0010, "sh000905": 0.0005,
        "sh000852": 0.0005})
    d["meta"]["fixture_regime"] = "index_flat_bull"
    path = os.path.join(OUTDIR, "index_flat_bull_merged.json")
    json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    br = d["breadth"]
    ratio = br["up"] / br["valid_total"] * 100
    print(f"[OK] {'index_flat_bull':14s} -> {os.path.basename(path)}  "
          f"up_ratio={ratio:.1f}%  zt={len(d['zt_pool'])}  (由 weight_drag 派生，指数平盘)")
    print("\n夹具已生成（已剔除隐私持仓代码、纠正为 v2 schema）。")


if __name__ == "__main__":
    main()
