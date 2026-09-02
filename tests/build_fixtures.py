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
  ② 剔除隐私：移除真实持仓代码 sz002378 / sz002185 / sz159622（snapshot/minutes/kline/fundflow）
  ③ 丢弃过期键：breadth_exact / updown / breadth_from_industry / market_total_stocks /
     top_gainers / top_losers / market_amount（与当前生成脚本无关，避免误用）
  ④ 注入 meta.mode = strict-midday

用法：
  python build_fixtures.py
产物：tests/fixtures/{broad_rise,differentiation,broad_fall,weight_drag}_merged.json
"""
import json, copy, os, sys

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

PRIVACY = ("sz002378", "sz002185", "sz159622")
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


def coerce_base(d):
    """把旧采集器的类型/结构校正为当前 generate 期望的 v2 形态。"""
    # —— snapshot ——
    for c, s in list(d.get("snapshot", {}).items()):
        if c in PRIVACY:
            d["snapshot"].pop(c, None); continue
        for k in ("prev_close", "price", "pct", "open", "high", "low",
                 "amount_wan", "volume_hand", "chg", "amplitude", "vol_ratio", "pb", "pe"):
            if k in s: s[k] = to_f(s[k])
    # —— minutes ——
    for c, ms in list(d.get("minutes", {}).items()):
        if c in PRIVACY:
            d["minutes"].pop(c, None); continue
        for m in ms:
            for k in ("p", "v", "amt"):
                if k in m: m[k] = to_f(m[k])
    # —— kline ——
    for c, ks in list(d.get("kline", {}).items()):
        if c in PRIVACY:
            d["kline"].pop(c, None); continue
        for k in ks:
            for fld in ("open", "close", "high", "low", "vol"):
                if fld in k: k[fld] = to_f(k[fld])
    # —— fundflow：全量剔除（含隐私持仓资金流）——
    d.pop("fundflow", None)
    # —— sector：类型纠正 ——
    for sec in ("sector_industry", "sector_concept"):
        for r in d.get(sec, []):
            r["pct"] = to_f(r.get("pct"))
            r["netflow"] = to_f(r.get("netflow"))
            r["up"] = to_i(r.get("up"))
            r["down"] = to_i(r.get("down"))
            r["lead_pct"] = to_f(r.get("lead_pct"))
    # —— zt_pool / dt_pool：类型纠正 ——
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


def flip_index_down(d, target=-0.006):
    """把 7 大指数整体翻为收跌（权重拖累形态用）。

    generate 的指数涨跌（IM[c]['am_pct']）由 minutes 末点价 ÷ snapshot.prev_close 计算，
    故需同时缩放 minutes 价格与 snapshot 显示字段（price/open/high/low/pct），
    保持日内相对形态不变、仅整体平移至目标跌幅。prev_close 不动（作分母基准）。
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
        scale = (pc * (1 + target)) / last_p
        for m in ms:
            if "p" in m:
                m["p"] = round(m["p"] * scale, 3)
        for k in ("price", "open", "high", "low"):
            v = s.get(k)
            if v:
                s[k] = round(v * scale, 3)
        if s.get("pct") is not None:
            s["pct"] = round(target * 100, 2)
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
    print("\n夹具已生成（已剔除隐私持仓代码、纠正为 v2 schema）。")


if __name__ == "__main__":
    main()
