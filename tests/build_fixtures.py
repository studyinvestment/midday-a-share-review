# -*- coding: utf-8 -*-
"""三态夹具构造器（普涨 / 分化 / 普跌）。

以 workspace 中真实的 midday_merged_20260821.json 为「结构基底」，
但做一次 v2 schema 校正后，再按三种市场状态注入受控的关键字段：
  - breadth（v2：listed_total/valid_total/missing/up/down/flat）→ 决定 breadth_regime 三档
  - zt_pool（涨停家数 / 最高连板）→ 决定 zt_regime 三档
  - sector 涨跌方向（仅普涨/普跌翻转符号）→ 决定 persist/资金主线分支

校正项（旧采集器 → 当前 generate 期望）：
  ① 类型纠正：sector.netflow/pct、zt.pct/streak 等由字符串转为 float/int
  ② 剔除隐私：移除真实持仓代码 sz002378 / sz002185 / sz159622（snapshot/minutes/kline/fundflow）
  ③ 丢弃过期键：breadth_exact / updown / breadth_from_industry / market_total_stocks /
     top_gainers / top_losers / market_amount（与当前生成脚本无关，避免误用）
  ④ 注入 meta.mode = strict-midday

用法：
  python build_fixtures.py
产物：tests/fixtures/{broad_rise,differentiation,broad_fall}_merged.json
"""
import json, copy, os

SRC = r"C:\Users\Administrator\WorkBuddy\automation-2026-07-29-17-17-17\midday_merged_20260821.json"
HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "fixtures")
os.makedirs(OUTDIR, exist_ok=True)

PRIVACY = ("sz002378", "sz002185", "sz159622")
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
    else:  # differentiation：保留真实混合板块与真实涨停池，仅注入受控中性宽度
        up, down, flat = 2550, 2900, 93          # 46.0% → neutral
        d["breadth"] = v2_breadth(up, down, flat, valid, listed, missing)
        # zt_pool / sectors 保持真实（混合）
    d["meta"]["fixture_regime"] = regime
    return d


def main():
    for reg in ("broad_rise", "differentiation", "broad_fall"):
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
