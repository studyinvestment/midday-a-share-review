# -*- coding: utf-8 -*-
"""生成 A股午间复盘 HTML 报告（自包含，内联 SVG 图表，无外部依赖）。

固化版本（date-agnostic）：
- 8 大模块结构、暗色渲染模板、SVG 分时/条形图、中国配色全部保留；
- 叙事/点评全部由数据驱动自动生成（无需每期手写）；
- 支持 --narrative 传入 JSON 覆盖任意模块的文案（用于人工精修）；
- 输入：合并后的行情 JSON + 涨跌家数 JSON（由 collect_midday_data.py 产出）。

用法：
  python generate_midday_review.py --date 2026-08-21 \
      --merged midday_merged_20260821.json --breadth breadth_20260821.json
"""
import json, html, os, re, argparse, datetime as _dt

# ---------------- 配置（按需修改） ----------------
IDX = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
       ("sh000688", "科创50"), ("sh000016", "上证50"), ("sh000905", "中证500"),
       ("sh000852", "中证1000")]
# 持仓为【个人私有配置】，不随 Skill 分发（避免把真实持仓固化进可分享模板）。
# 通过 --holdings 指向一个 holdings.json（见同目录 holdings.example.json 模板），
# 或在运行目录 / 脚本目录下放置 holdings.json 自动加载。
# 格式：{"holdings": [[代码, 名称, 成本], ...], "hold_ctx": {代码: {"sector": 板块名或null, "note": "..."}}}
HOLD = []        # 由 load_holdings() 填充：(代码, 名称, 成本; None=未记录)
HOLD_CTX = {}    # 由 load_holdings() 填充：{代码: {"sector":..., "note":...}}

UP, DN, FLAT = "#ef4444", "#22c55e", "#94a3b8"      # 涨红 跌绿

def cls(v): return "up" if v > 0 else ("down" if v < 0 else "flat")
def sgn(v, d=2): return f"{v:+.{d}f}"
def esc(s): return html.escape(str(s if s is not None else ""))

# ================= 加载数据 =================
ap = argparse.ArgumentParser()
ap.add_argument("--date", default=_dt.datetime.now().strftime("%Y-%m-%d"))
ap.add_argument("--merged", default=None)
ap.add_argument("--breadth", default=None)
ap.add_argument("--out", default=None)
ap.add_argument("--narrative", default=None, help="可选：覆盖各模块文案的 JSON 路径")
ap.add_argument("--mcp-note", default=None,
                help="可选：MCP 数据源实际使用情况说明（写入文末「数据来源」）。"
                     "未提供时用默认降级表述。")
ap.add_argument("--holdings", default=None,
                help="持仓配置文件 JSON 路径；持仓为个人私有配置不随 Skill 分发，见 holdings.example.json")
args = ap.parse_args()
DATE = args.date
DC = DATE.replace("-", "")
MERGED = args.merged or f"midday_merged_{DC}.json"
BREADTH = args.breadth or f"breadth_{DC}.json"
OUT = args.out or f"daily-review/午间复盘_{DC}.html"

# ---- 加载持仓（个人私有，不随 Skill 分发）----
def _load_holdings(path):
    if not path:
        return [], {}
    if not os.path.exists(path):
        print(f"[WARN] 持仓配置文件不存在：{path}")
        return [], {}
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 持仓配置读取失败：{e}")
        return [], {}
    return [tuple(x) for x in cfg.get("holdings", [])], cfg.get("hold_ctx", {})

def _default_holdings():
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(os.getcwd(), "holdings.json"),
              os.path.join(here, "holdings.json")):
        if os.path.exists(c):
            return c
    return None

HOLD, HOLD_CTX = _load_holdings(args.holdings or _default_holdings())

D = json.load(open(MERGED, encoding="utf-8"))
# 涨跌家数：优先单独文件，其次合并体内的 breadth / breadth_exact / updown
BR = None
if args.breadth and os.path.exists(BREADTH):
    BR = json.load(open(BREADTH, encoding="utf-8"))
elif "breadth" in D: BR = D["breadth"]
elif "breadth_exact" in D: BR = D["breadth_exact"]
elif "updown" in D: BR = D["updown"]

snap, mins, kl = D["snapshot"], D["minutes"], D["kline"]
def _dedup_sectors(rows):
    """东财行业板块含 Ⅰ/Ⅱ/Ⅲ 多级同名录入（如「地面兵装Ⅲ」与「地面兵装Ⅱ」），
    会同时占据 TOP5 名额、把真实第二强板块挤出去（2026-09-02 实测）。
    判定：去掉罗马数字后缀后重名，且 涨跌幅/主力净额/涨跌家数/龙头 全部一致 → 保留名称更短更常见的一个。
    """
    out, seen = [], {}
    for r in rows:
        base = re.sub(r"[ⅠⅡⅢⅣ]+$", "", (r.get("name") or "")).strip()
        sig = (round(r.get("pct") or 0, 4), round((r.get("netflow") or 0) / 1e4, 2),
               r.get("up"), r.get("down"), r.get("lead"))
        key = (base, sig)
        if key in seen:
            prev = seen[key]
            if len(r.get("name") or "") < len(prev.get("name") or ""):
                out[out.index(prev)] = r
                seen[key] = r
            continue
        seen[key] = r
        out.append(r)
    return out


ind = _dedup_sectors(D["sector_industry"])
con = _dedup_sectors(D["sector_concept"])
zt, dt = D.get("zt_pool", []), D.get("dt_pool", [])
news = D.get("news", [])
ff = D.get("fundflow", {})
meta = D.get("meta", {})

# ================= 指数 / 持仓计算 =================
def ma(code, n, field="close"):
    k = kl.get(code, [])
    if len(k) < n: return None
    return sum(x[field] for x in k[-n:]) / n

def metrics(code):
    s = snap.get(code, {}); m = mins.get(code, [])
    pc = s.get("prev_close") or 0
    if not m or not pc: return None
    op, cl_ = m[0]["p"], m[-1]["p"]
    hi = max(x["p"] for x in m); lo = min(x["p"] for x in m)
    hi_t = max(m, key=lambda x: x["p"])["t"]; lo_t = min(m, key=lambda x: x["p"])["t"]
    return {"prev": pc, "open": op, "am_close": cl_, "high": hi, "low": lo,
            "hi_t": hi_t, "lo_t": lo_t,
            "open_pct": (op / pc - 1) * 100, "am_pct": (cl_ / pc - 1) * 100,
            "hi_pct": (hi / pc - 1) * 100, "lo_pct": (lo / pc - 1) * 100,
            "amp": (hi - lo) / pc * 100,
            "now": s.get("price", 0), "now_pct": s.get("pct", 0),
            "retrace": (hi - cl_) / pc * 100,
            "am_amt": m[-1].get("amt", 0), "am_vol": m[-1].get("v", 0),
            "ma5": ma(code, 5), "ma10": ma(code, 10), "ma20": ma(code, 20), "ma60": ma(code, 60)}

IM = {c: metrics(c) for c, _ in IDX}
HM = {c: metrics(c) for c, _, _ in HOLD}

# ================= 量能 =================
def vol_stat(code):
    k = kl.get(code, [])
    if len(k) < 6: return None
    prev5 = [x["vol"] for x in k[-6:-1]]
    avg5 = sum(prev5) / 5
    am_v = (mins.get(code, [{}])[-1] or {}).get("v", 0)
    return {"avg5_full": avg5, "am_vol": am_v, "ratio": am_v / avg5 * 100 if avg5 else 0,
            "proj_full": am_v / 0.565, "proj_vs_avg5": (am_v / 0.565) / avg5 * 100 if avg5 else 0,
            "prev5": prev5, "dates": [x["date"] for x in k[-6:-1]]}

VS = vol_stat("sh000001"); VZ = vol_stat("sz399001")


def build_divergence_note():
    """量价关系提示。按「创业板指方向 × 上涨占比」二维判定 + 量能档(volume_regime)三态措辞。

    历史缺陷批次：
    - 2026-09-02：#5 原模板硬编码「指数涨、个股跌」，指数下跌日出现「涨 -2.18%」语病 → 按 cyb 方向措辞。
    - 2026-08-25（#11-15 批次）：66% 上涨日"仅 66%"自相矛盾 → up_word 按占比阈值切换。
    - 2026-09-08（#22）：平收分支无脑"个股弱"与普涨矛盾 → 平收×普涨象限二维化。
    - 2026-09-09（#23，本次）：所有分支量能措辞仍写死（"未放大/普跌缩量/量能平"），
      在 volume_regime=expanded（推算全日 117%）放量日仍输出"普跌缩量形态"自相矛盾；
      且 cyb>=0.3 涨分支在 up_ratio>=55 普涨日仍写"个股跌"。
      → 量能措辞全部接 _VREG 三态化（缩量/持平/温和放大），涨分支补 up_ratio 二维判定。
    注意：本函数在 FACTS 与 _VOL_* 定义之后才被调用（DIVERG_NOTE 赋值点已后移），可安全引用。
    """
    cyb = (IM.get("sz399006") or {}).get("am_pct")
    if cyb is None:
        return ""
    # 「仅 X% 个股上涨」中的"仅"字只在低占比时成立；66% 说"仅"自相矛盾（2026-08-25 实测）。
    up_word = f"仅 {up_ratio:.0f}%" if up_ratio < 45 else f"有 {up_ratio:.0f}%"
    # 量能档标签（与模块 04/08 的 _VOL_* 同源，按 volume_regime 三态；2026-09-09 #23）
    _vw = {"shrunk": "缩量", "flat": "量能平", "expanded": "温和放量"}.get(_VREG, "量能平")
    _vw_long = {"shrunk": "量能偏弱（缩量）", "flat": "量能未明显放大",
                "expanded": "量能温和放大"}.get(_VREG, "量能未明显放大")
    # 尾句按「指数方向 × 量能档」二维收敛（2026-09-17 修复缺陷 #38）
    # 缺陷：原 else 分支（非放量档）无条件写死"<b>指数缺乏向上突破的动能</b>，下午若量能仍不能放出，
    # 高位品种回落风险大于上行空间"，而该 tail 被全部 5 个方向分支共用（含 cyb>=0.3 的上涨分支）。
    # 2026-09-16 实测（第 12 形态「科技成长主导的普涨共振」）：创业板指 +2.35%、科创50 +4.50%、
    # 上证自日内高点仅回落 0.01 个百分点、收在日内高位——同函数 head/body 已写"量价齐升、
    # 有 76% 个股上涨、赚钱效应较广"，尾句却仍断言"指数缺乏向上突破的动能"，同一段落内自相矛盾。
    # 修法：按指数方向三态（涨 / 跌 / 平）分别给措辞；上涨档一律不得再出现方向性否定判断。
    _cyb_dir = "up" if cyb >= 0.3 else ("down" if cyb <= -0.3 else "flat")
    _TAIL_UP = ("不过量能未同步放大，下午需以补量确认上行——若午后成交无法跟进，则上行空间或受限。"
                if _VREG != "expanded" else
                "量能已温和放大，需关注午后能否延续——若午后量能无法跟进，谨防高位品种放量滞涨。")
    _TAIL_DN = ("但指数仍缺乏向上突破的动能，下午若量能仍不能放出，反弹空间受限、需防再度走弱。"
                if _VREG != "expanded" else
                "量能已温和放大，需关注午后能否延续——放量下跌需防资金分歧进一步加大。")
    _TAIL_FL = ("但量能未明显放大，方向选择仍需量能配合，下午以观察量能变化为主。"
                if _VREG != "expanded" else
                "量能已温和放大，需关注午后能否延续——指数方向未明，量能能否维持是关键。")
    tail = ("该形态下，资金净流入居前的板块相对更具韧性；"
            + {"up": _TAIL_UP, "down": _TAIL_DN, "flat": _TAIL_FL}[_cyb_dir] + "<br>")
    if cyb >= 0.3:
        if up_ratio >= 55:
            # 指数涨×个股普涨：量价齐升的共振健康形态（2026-09-09 #23 补：旧模板此象限写"个股跌"矛盾）
            # 2026-09-14 修复缺陷 #28：原写死"指数与多数个股同向走强"，但本分支只锚定创业板指，
            # 7 大指数可能多数收跌（broad_rise 夹具：创业板 +1.21% 而 5/7 指数下跌，仍写"指数同向走强"）。
            # 2026-09-15 起改由 idx_dir() 单一事实源提供（缺陷 #34/#36 同批）。
            _iu2, _id2, _if2, _sp2, _t2 = idx_dir()
            _rel2 = ("但 7 大指数内部方向分化（%d 涨 %d 跌），指数整体并未同步走强" % (_iu2, _id2)
                     if _sp2 else "指数与多数个股同向走强")
            head = (f"<b>量价齐升：</b>创业板指涨 {abs(cyb):.2f}%、{_vw_long}，同时<b>有 {up_ratio:.0f}% 个股上涨</b>，"
                    f"{_rel2}。")
            body = f'属于"<b>指数涨、个股涨、{_vw}</b>"的普涨形态，赚钱效应较广。'
        elif up_ratio >= 45:
            head = (f"<b>量价背离提示：</b>创业板指涨 {abs(cyb):.2f}%、{_vw_long}，但<b>有 {up_ratio:.0f}% 个股上涨</b>，"
                    f"指数与个股方向存在分歧。")
            body = f'属"<b>指数涨、个股分化、{_vw}</b>"的结构性行情——指数走强主要由权重/主线贡献。'
        else:
            head = (f"<b>量价背离提示：</b>创业板指涨 {abs(cyb):.2f}% 而{_vw_long}，同时<b>{up_word} 个股上涨</b>，"
                    f"指数与多数个股方向相反。")
            body = f'构成"<b>指数涨、个股跌、{_vw}</b>"的结构性行情，赚钱效应与指数表现背离。'
    elif cyb <= -0.3:
        if up_ratio >= 55:
            # 指数跌但个股普涨 —— 权重拖累，不是普跌
            head = (f"<b>权重拖累型分化：</b>创业板指跌 {abs(cyb):.2f}%，但<b>有 {up_ratio:.0f}% 个股上涨</b>，"
                    f"指数与个股方向相反。")
            body = (f'属于"<b>指数跌、个股涨、{_vw}</b>"的权重拖累形态——多数个股实为上涨，'
                    '指数回落主要由少数权重股贡献，不应按普跌市处理。')
        else:
            head = (f"<b>量价同步走弱：</b>创业板指跌 {abs(cyb):.2f}%、{_vw_long}，同时<b>{up_word} 个股上涨</b>，"
                    f"指数与个股同向走弱。")
            body = (f'属于"<b>指数跌、个股跌、{_vw}</b>"的普跌'
                    + ("缩量形态" if _VREG == "shrunk" else
                       ("整理形态——量能未明显放大，反弹需先看量能配合" if _VREG == "flat"
                        else "形态——指数与个股同步走弱但量能仍处高位，放量下跌需防资金分歧加大")) + '。')
    else:
        if up_ratio >= 55:
            # 平收×普涨：个股活跃度不弱，不能再按"个股弱"描述（2026-09-08 修复，#22 残留：
            # 62% 上涨日仍输出"指数平、个股弱"自相矛盾；二维判定补平收×普涨象限）
            head = (f"<b>量价关系：</b>创业板指基本平收（{cyb:+.2f}%）、{_vw_long}，"
                    f"但<b>有 {up_ratio:.0f}% 个股上涨</b>，")
            body = (f'指数与多数个股方向并不一致，个股活跃度不弱，属"<b>指数平、个股涨、{_vw}</b>"的整理形态'
                    '——指数横盘主要由权重股贡献，不宜按弱势市处理。')
        else:
            head = f"<b>量价关系：</b>创业板指基本平收（{cyb:+.2f}%）、{_vw_long}，<b>{up_word} 个股上涨</b>，"
            body = f'属于"<b>指数平、个股弱、{_vw}</b>"的观望形态。'
    return (head + body + tail)
am_amt_sh = (IM["sh000001"]["am_amt"] / 1e8) if IM.get("sh000001") else 0
am_amt_sz = (IM["sz399001"]["am_amt"] / 1e8) if IM.get("sz399001") else 0
am_amt_tot = am_amt_sh + am_amt_sz

# ================= 板块 =================
ind_v = [r for r in ind if isinstance(r.get("pct"), (int, float))]
ind_v.sort(key=lambda x: -x["pct"])
TOP5, BOT5 = ind_v[:5], ind_v[-5:][::-1]
con_v = [r for r in con if isinstance(r.get("pct"), (int, float))]
con_v.sort(key=lambda x: -x["pct"])

# ============ 指数内部方向：单一事实源（2026-09-15 修复缺陷 #34/#36）============
# 问题：M01 / M04 / M06 原先各自内联重复计算"7 大指数里几只涨几只跌"，且用途与死区不一致，
# 导致同一天报告并存三套互斥口径（"指数分化" / "指数与个股方向大体一致" / "方向相反、权重拖累"）。
# 例：2026-09-15 为「指数涨跌互现 × 个股普跌」第 11 形态（am_pct -0.10/+0.04/-0.18/+1.79/
# -0.03/+0.22/+0.11，广度 27.9%），M01 输出"指数分化 + 多数个股下跌"，M06 却落兜底句
# "指数与个股方向大体一致"——7 指数均值 +0.26% 对 27.9% 上涨占比本身就是背离。
# 更早的 weight_drag 夹具（7 指数全跌 -0.60% × 66% 个股普涨）同样落兜底句"大体一致"，
# 而 M01 同页写的是"个股普涨而指数承压、权重股拖累"——方向相反却被称作一致。
# 多形态实测（11 种形态渲染后提取 M01/M06 原句）：6/11 形态落兜底句，其中 2 种明确错误。
# → 统一为单一 helper，M01/M04/M06 共用同一份计数与判定。
_IDX_EPS = 0.05          # 死区：|涨跌幅| <= 0.05% 视为基本持平，不计入涨/跌

def idx_dir():
    """返回 (n_up, n_down, n_flat, is_split, trend)。
    is_split：同时存在 >=2 只涨、>=2 只跌 → 指数内部方向分化。
    trend：'up' 普涨 / 'down' 普跌 / 'split' 分化（其余按多数方向归并）。"""
    ups = sum(1 for c, _n in IDX if (IM.get(c) or {}).get("am_pct", 0) > _IDX_EPS)
    dns = sum(1 for c, _n in IDX if (IM.get(c) or {}).get("am_pct", 0) < -_IDX_EPS)
    flt = len(IDX) - ups - dns
    split = (ups >= 2 and dns >= 2)
    if split:
        trend = "split"
    elif ups > dns:
        trend = "up"
    elif dns > ups:
        trend = "down"
    else:
        trend = "up" if ups > 0 else ("down" if dns > 0 else "flat")
    return ups, dns, flt, split, trend

def idx_cnt_txt(ups, dns, flt):
    return (f"{len(IDX)} 大指数 {ups} 涨 {dns} 跌" + (f"、{flt} 只基本持平" if flt else ""))


def find_ind(name):
    for r in ind_v:
        if r["name"] == name: return r
    return None

def persist(r):
    """基于 主力净流入 / 板块内涨跌比 / 龙头强度 计算【盘中强度/共振评分】。
    注意：这是日内截面强度，不承诺对下午或次日的预测性。"""
    sc, notes = 0, []
    nf = (r.get("netflow") or 0) / 1e8
    u, d_ = int(r.get("up") or 0), int(r.get("down") or 0)
    lp = r.get("lead_pct") or 0
    if nf > 10: sc += 2; notes.append(f"主力大幅净流入{nf:.1f}亿")
    elif nf > 2: sc += 1; notes.append(f"主力净流入{nf:.1f}亿")
    elif nf < -10: sc -= 2; notes.append(f"主力大幅净流出{nf:.1f}亿")
    elif nf < -2: sc -= 1; notes.append(f"主力净流出{nf:.1f}亿")
    else: notes.append(f"主力净额{nf:+.1f}亿")
    tot = u + d_
    if tot:
        ratio = u / tot
        if ratio >= 0.9: sc += 2; notes.append(f"板块内{u}涨{d_}跌(全线普涨)")
        elif ratio >= 0.6: sc += 1; notes.append(f"板块内{u}涨{d_}跌")
        elif ratio <= 0.1: sc -= 2; notes.append(f"板块内{u}涨{d_}跌(全线普跌)")
        else: notes.append(f"板块内{u}涨{d_}跌")
    if isinstance(lp, (int, float)):
        if lp >= 9.8: sc += 1; notes.append(f"龙头{r.get('lead')}涨停")
        elif lp >= 5: sc += 1; notes.append(f"龙头{r.get('lead')}+{lp:.1f}%")
    if sc >= 4: lv, lc = "强度高", "s-high"
    elif sc >= 2: lv, lc = "强度中等", "s-mid"
    elif sc >= 0: lv, lc = "需观察", "s-obs"
    else: lv, lc = "强度弱", "s-low"
    return sc, lv, lc, "；".join(notes)

# ================= 涨停 =================
streaks = {}
for r in zt:
    s = r.get("streak") or 1
    streaks[s] = streaks.get(s, 0) + 1
multi = sorted([r for r in zt if (r.get("streak") or 1) >= 2], key=lambda x: -(x.get("streak") or 0))
ind_cnt = {}
for r in zt:
    k_ = r.get("industry") or "其他"
    ind_cnt[k_] = ind_cnt.get(k_, 0) + 1
zt_ind = sorted(ind_cnt.items(), key=lambda x: -x[1])[:8]

# ================= 资讯筛选 =================
# 通用财经关键词（2026-09-02 重写）：原列表只覆盖某一天的特定事件（恒大/临港/以旧换新…），
# 换一天几乎筛不出内容。改为按「货币 / 财政产业 / 宏观数据 / 监管市场 / 行业主题 / 公司业绩 / 外部」七类泛化。
KEY = [
    # 货币与流动性
    "央行", "人民银行", "逆回购", "MLF", "LPR", "降准", "降息", "净投放", "净回笼",
    "流动性", "资金面", "存款准备金", "社融", "信贷",
    # 财政与产业政策
    "财政部", "国债", "专项债", "税收", "减税", "发改委", "工信部", "商务部", "国资委",
    "能源局", "政策", "规划", "部署", "方案", "试点", "两部门", "三部门",
    # 宏观数据
    "统计局", "PMI", "GDP", "CPI", "PPI", "进出口", "外贸", "用电量", "经济数据",
    # 监管与市场
    "证监会", "交易所", "IPO", "注册制", "退市", "回购", "增持", "减持",
    "北交所", "科创板", "两融", "投资者",
    # 行业主题
    "人工智能", "算力", "芯片", "半导体", "集成电路", "新能源", "光伏", "储能", "电网",
    "军工", "机器人", "医药", "医保", "集采", "汽车", "房地产", "楼市", "消费", "内需",
    "稀土", "煤炭", "钢铁", "有色", "黄金", "原油", "农业", "种业",
    # 公司与业绩
    "业绩", "盈利", "净利", "中报", "年报", "预增", "预亏", "并购", "重组", "订单",
    # 外部环境
    "美联储", "美股", "纳斯达克", "人民币", "汇率", "关税", "贸易摩擦",
]


def pick(n):
    t = n.get("title") or ""
    return any(k in t for k in KEY)


sel = [n for n in news if pick(n)][:14]

# ================= SVG 分时图 =================
def spark(code, w=300, h=86, label=""):
    m = mins.get(code, [])
    mm = IM.get(code) or HM.get(code)
    if not m or not mm: return ""
    pc = mm["prev"]
    ps = [x["p"] for x in m]
    lo, hi = min(ps), max(ps)
    rng = max(hi - pc, pc - lo, pc * 0.0015) * 1.12
    top, bot = pc + rng, pc - rng
    def X(i): return 34 + i / max(len(ps) - 1, 1) * (w - 42)
    def Y(p): return 6 + (top - p) / (top - bot) * (h - 24)
    pts = " ".join(f"{X(i):.1f},{Y(p):.1f}" for i, p in enumerate(ps))
    col = UP if ps[-1] >= pc else DN
    ybase = Y(pc)
    area = f"{X(0):.1f},{ybase:.1f} " + pts + f" {X(len(ps)-1):.1f},{ybase:.1f}"
    gid = "g_" + code
    ticks = ""
    for tl, lab in [("0930", "9:30"), ("1000", "10:00"), ("1030", "10:30"), ("1100", "11:00"), ("1130", "11:30")]:
        idx = next((i for i, x in enumerate(m) if x["t"] == tl), None)
        if idx is None: continue
        ticks += (f'<line x1="{X(idx):.1f}" y1="6" x2="{X(idx):.1f}" y2="{h-18}" stroke="#334155" '
                  f'stroke-width="0.5" stroke-dasharray="2,3"/>'
                  f'<text x="{X(idx):.1f}" y="{h-6}" fill="#64748b" font-size="8" text-anchor="middle">{lab}</text>')
    return f'''<svg viewBox="0 0 {w} {h}" class="spark" role="img" aria-label="{esc(label)}分时走势">
<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">
<stop offset="0%" stop-color="{col}" stop-opacity="0.30"/><stop offset="100%" stop-color="{col}" stop-opacity="0.02"/>
</linearGradient></defs>
{ticks}
<line x1="34" y1="{ybase:.1f}" x2="{w-8}" y2="{ybase:.1f}" stroke="#64748b" stroke-width="0.7" stroke-dasharray="4,3"/>
<text x="30" y="{ybase+3:.1f}" fill="#94a3b8" font-size="8" text-anchor="end">{pc:.0f}</text>
<text x="30" y="9" fill="#94a3b8" font-size="8" text-anchor="end">{top:.0f}</text>
<text x="30" y="{h-20:.1f}" fill="#94a3b8" font-size="8" text-anchor="end">{bot:.0f}</text>
<polygon points="{area}" fill="url(#{gid})"/>
<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.5" stroke-linejoin="round"/>
</svg>'''

# 横向条形图（板块）
def barchart(rows, w=660, rowh=26, maxn=10):
    rows = rows[:maxn]
    if not rows: return ""
    mx = max(abs(r["pct"]) for r in rows) or 1
    h = rowh * len(rows) + 8
    mid = 190
    out = [f'<svg viewBox="0 0 {w} {h}" class="barsvg">']
    for i, r in enumerate(rows):
        y = i * rowh + 4
        p = r["pct"]; col = UP if p > 0 else DN
        bw = abs(p) / mx * (w - mid - 96)
        x0 = mid
        out.append(f'<text x="{mid-8}" y="{y+13}" fill="#cbd5e1" font-size="11" text-anchor="end">{esc(r["name"])}</text>')
        out.append(f'<rect x="{x0}" y="{y+3}" width="{bw:.1f}" height="{rowh-11}" fill="{col}" rx="2" opacity="0.85"/>')
        out.append(f'<text x="{x0+bw+6:.1f}" y="{y+13}" fill="{col}" font-size="11" font-weight="600">{p:+.2f}%</text>')
        nf = (r.get("netflow") or 0) / 1e8
        ncol = UP if nf > 0 else DN
        out.append(f'<text x="{w-4}" y="{y+13}" fill="{ncol}" font-size="10" text-anchor="end">{nf:+.1f}亿</text>')
    out.append("</svg>")
    return "".join(out)

# ================= 自动叙事（数据驱动） =================
def fmt_drv(lst, n=2):
    return "、".join(f"{esc(r['name'])} {r['pct']:+.2f}%（主力{(r.get('netflow') or 0)/1e8:+.1f}亿）"
                     for r in lst[:n])

# ================= 事实层（数据驱动、可追溯） =================
# 每条事实：value / metric(用了什么指标) / threshold(阈值) / missing(数据缺失时停判) / kind(fact事实|correlation相关性|speculation推测)
def compute_facts():
    F = {}
    # 1) 市场宽度
    if BR and BR_valid:
        reg = "weak" if up_ratio < 40 else ("neutral" if up_ratio < 55 else "strong")
        F["breadth_regime"] = {"value": reg, "metric": f"上涨占比{up_ratio:.1f}%",
                               "threshold": "<40弱 / 40-55中性 / >55强", "missing": False, "kind": "fact"}
    else:
        F["breadth_regime"] = {"missing": True, "kind": "fact"}
    # 2) 量能
    if VS:
        p = VS.get("proj_vs_avg5", 0)
        reg = "shrunk" if p < 100 else ("flat" if p < 115 else "expanded")
        F["volume_regime"] = {"value": reg, "metric": f"推算全日量能约前5日均量{p:.0f}%",
                              "threshold": "<100缩量 / 100-115持平 / >115放量", "missing": False, "kind": "fact"}
    else:
        F["volume_regime"] = {"missing": True, "kind": "fact"}
    # 3) 指数日内形态
    m = IM.get("sh000001")
    if m:
        hit = m.get("hi_t"); lot = m.get("lo_t")
        hi_first = bool(hit and lot and hit <= lot)
        # 补 bounce：hi_first 只能说明"高点在低点之前"，无法区分「冲高回落」与「低开探底回升」
        bounce_ = (m.get("am_pct", 0) or 0) - (m.get("lo_pct", 0) or 0)
        # 2026-09-11 缺陷 #26 同族：还需判断高点是否高于开盘价（是否真的上行过）。
        _lift_ = (m.get("hi_pct", 0) or 0) - (m.get("open_pct", 0) or 0)
        if hi_first and bounce_ > 0.3: pat = "dip_then_rebound"
        elif hi_first and m.get("retrace", 0) > 0.8:
            pat = "rush_then_fall" if _lift_ >= 0.15 else "open_high_fall"
        elif (not hi_first) and m.get("retrace", 0) > 0.8: pat = "dip_then_rebound"
        elif m.get("am_pct", 0) > 0.3: pat = "strong"
        else: pat = "weak"
        _pat_cn = {"dip_then_rebound": "低开探底后回升", "rush_then_fall": "冲高回落",
                   "open_high_fall": "开盘即高点后单边下行",
                   "strong": "单边走强", "weak": "弱势整理"}[pat]
        F["index_pattern"] = {"value": pat,
                              "metric": f"上证{_pat_cn}：自高点{m.get('retrace',0):.2f}pct、自低点{bounce_:+.2f}pct",
                              "threshold": "冲高回落/探低回升/单边", "missing": False, "kind": "fact"}
    else:
        F["index_pattern"] = {"missing": True, "kind": "fact"}
    # 4) 龙头资金集中度（相关性）
    pos = [r for r in ind_v if (r.get("netflow") or 0) > 0]
    tot_pos = sum((r.get("netflow") or 0) for r in pos)
    if pos and tot_pos:
        top1 = max(pos, key=lambda r: (r.get("netflow") or 0))
        share = (top1.get("netflow") or 0) / tot_pos
        F["leader_concentration"] = {"value": "high" if share >= 0.4 else "low",
                                     "metric": f"最大净流入板块占全部净流入{share*100:.0f}%",
                                     "threshold": ">=40%为高集中", "missing": False, "kind": "correlation"}
    else:
        F["leader_concentration"] = {"missing": True, "kind": "correlation"}
    # 5) 资讯置信度
    n_sel = len(sel)
    n_core = len([n for n in sel if any(k in (n.get("title") or "")
                 for k in ["财政部", "贴息", "专项债", "特别国债", "破产", "恒大", "以旧换新"])])
    conf = "high" if (n_sel >= 6 and n_core) else ("medium" if n_sel >= 3 else "low")
    F["news_confidence"] = {"value": conf, "metric": f"筛选{n_sel}条(核心{n_core}条)",
                            "threshold": ">=6且含核心=高", "missing": False, "kind": "fact"}
    # 6) 涨停情绪
    top_streak = max(streaks) if streaks else 0
    n_zt = len(zt)
    if n_zt >= 40 and top_streak >= 5: reg = "active"
    elif n_zt < 20 or top_streak <= 3: reg = "weak"
    else: reg = "neutral"
    F["zt_regime"] = {"value": reg, "metric": f"涨停{n_zt}只/最高{top_streak}板",
                      "threshold": ">=40只且>=5板=活跃", "missing": False, "kind": "fact"}
    # 7) 星期（仅真实周五才提"周末"，不硬编码效应）
    try:
        wd = _dt.datetime.strptime(DATE, "%Y-%m-%d").weekday()
    except Exception:
        wd = -1
    F["weekday"] = {"value": wd, "missing": False, "kind": "fact"}
    # 8) 资金流相对排名（避免绝对阈值对大小板块不公平）
    F["ff_rel_rank"] = {r["name"]: i + 1 for i, r in
                        enumerate(sorted(ind_v, key=lambda r: -((r.get("netflow") or 0))))}
    return F

def build_s01_note():
    opens = [(c, n, IM[c]) for c, n in IDX if IM.get(c)]
    if not opens: return "指数数据缺失。"
    n_down = sum(1 for _, _, m in opens if m['open_pct'] < 0)
    n_up = sum(1 for _, _, m in opens if m['open_pct'] > 0)
    srt = sorted(opens, key=lambda x: x[2]['am_pct'])
    weak = srt[0]; strong = srt[-1]
    kc = IM.get('sh000688'); mc = IM.get('sh000905'); m1 = IM.get('sh000852')
    kc_name = next((n for c, n in IDX if c == 'sh000688'), '科创50')
    # 高低开档位对称判断（2026-09-04 修复：0低开7高开却落"开盘分化"）
    # 2026-09-15 修复缺陷 #37：「全线」需对侧计数为 0，否则与紧随其后的统计句自相矛盾。
    # 实测 bull_resonance（2 只低开、5 只高开）输出"全线高开。7 大指数中 2 只低开、5 只高开"。
    if n_up >= 5 and n_down == 0:
        _oreg = "全线高开"
    elif n_up >= 4:
        _oreg = "多数高开"
    elif n_down >= 5 and n_up == 0:
        _oreg = "全线低开"
    elif n_down >= 4:
        _oreg = "多数低开"
    else:
        _oreg = "开盘分化"
    s = f"<b>结构特征：{_oreg}。</b>"
    s += f"{len(opens)} 大指数中 {n_down} 只低开、{n_up} 只高开；"
    # 全部收跌时说"领涨"会误导，改"相对抗跌"（2026-09-02 修正）
    strong_word = "相对抗跌" if strong[2]['am_pct'] < 0 else "领涨"
    weak_word = "领跌" if weak[2]['am_pct'] < 0 else "最弱"
    s += f"风格上<b>{esc(strong[1])} {strong[2]['am_pct']:+.2f}%</b>{strong_word}，<b>{esc(weak[1])} {weak[2]['am_pct']:+.2f}%</b>{weak_word}。"
    # 2026-09-14 修复缺陷 #29：「全场最强/最弱」原为硬编码最高级、未做跨指数排名。
    # 当日科创50 自低点修复 1.38pct 被称"全场日内动能最强"，实际中证1000 修复 1.83pct 更强
    # （科创50 仅第 2）。凡最高级表述必须由跨指数排序得出，不能按分支语义想当然。
    _b_r = sorted(opens, key=lambda x: -(x[2]['am_pct'] - x[2]['lo_pct']))
    _t_r = sorted(opens, key=lambda x: -x[2]['retrace'])
    _b_rank = next((i + 1 for i, (c, _1, _2) in enumerate(_b_r) if c == 'sh000688'), 0)
    _t_rank = next((i + 1 for i, (c, _1, _2) in enumerate(_t_r) if c == 'sh000688'), 0)
    _b_max = _b_r[0] if _b_r else None
    _t_max = _t_r[0] if _t_r else None
    # 只有「先冲高、后回落」才叫回吐；若高点在低点之后（V 型回升）则应描述为修复
    if kc and kc['retrace'] > 0.8 and kc['hi_t'] < kc['lo_t']:
        # 「冲高 -0.10%」是语病：高点仍为负时不能叫冲高（2026-08-24 实测）
        _hiw = (f"盘中最高仅 {kc['hi_pct']:+.2f}%" if kc['hi_pct'] < 0
                else f"盘中冲高 {kc['hi_pct']:+.2f}%")
        _tw = ("，为全场回落幅度最大的指数。" if _t_rank == 1 else
               (f"，回落幅度居全场第 {_t_rank}（最大为{esc(_t_max[1])}的 {_t_max[2]['retrace']:.2f} 个百分点）。"
                if _t_max else "。"))
        s += (f"<b>需留意 {esc(kc_name)} 的日内回吐</b>：{_hiw}后回落至 "
              f"{kc['am_pct']:+.2f}%，回落 {kc['retrace']:.2f} 个百分点" + _tw)
    elif kc and kc['hi_t'] > kc['lo_t'] and (kc['am_pct'] - kc['lo_pct']) > 0.8:
        _bn = kc['am_pct'] - kc['lo_pct']
        _bw = ("，为全场自低点修复幅度最大的指数。" if _b_rank == 1 else
               (f"，修复幅度居全场第 {_b_rank}（最大为{esc(_b_max[1])}的 "
                f"{_b_max[2]['am_pct'] - _b_max[2]['lo_pct']:.2f} 个百分点）。" if _b_max else "。"))
        s += (f"<b>{esc(kc_name)} 呈探底回升</b>：{kc['lo_t'][:2]}:{kc['lo_t'][2:]} 下探 {kc['lo_pct']:+.2f}% 后"
              f"一路上行至 {kc['hi_t'][:2]}:{kc['hi_t'][2:]} 的 {kc['hi_pct']:+.2f}%，"
              f"自低点修复 {_bn:.2f} 个百分点" + _bw)
    if mc and m1 and mc['am_pct'] < 0 and m1['am_pct'] < 0:
        s += f"<b>中小盘走弱</b>——中证500 {mc['am_pct']:+.2f}%、中证1000 {m1['am_pct']:+.2f}% 双双收跌。"
    # 宽度联动：必须「指数方向 × 广度」二维判定。
    # 只按 breadth_regime 一维判断会漏掉「指数跌、个股普涨」的权重拖累日，
    # 2026-08-25 就因此输出了"指数与多数个股同向走强"（当天 7 大指数全绿）。
    br = FACTS.get("breadth_regime", {})
    _sh = (IM.get("sh000001") or {}).get("am_pct")
    # 2026-09-14 修复缺陷 #28：原判定只看上证一个指数（_sh >= -0.1 即"同向走强"），
    # 当日 7 大指数 3 涨 4 跌（3 涨 +0.16/+0.02/+0.68、4 跌 -0.25/-0.47/-0.94/-0.05），
    # 仍输出"同向走强"，与模块 04 用创业板口径得出的"方向相反、权重拖累"自相矛盾。
    # 2026-09-15 修复缺陷 #36：改由 idx_dir() 单一事实源提供计数与方向，并让「指数分化」
    # 这类方向断言真正受数据约束——原 weak / neutral 分支无条件写"指数分化"，
    # 多形态实测下 low_open_recover / open_high_selloff（7 大指数全线收跌）与 expanded_bear
    # （1 涨 6 跌）仍输出"指数分化 + 多数个股下跌"，属方向断言未接数据。
    _iu, _id, _if, _split, _trend = idx_dir()
    if br.get("missing"):
        s += "（涨跌家数暂缺，指数与个股宽度的关系待补全数据后判断。）"
    elif br["value"] == "weak":
        if _split:
            s += (f"<b>指数内部分化 × 个股普跌</b>——{idx_cnt_txt(_iu, _id, _if)}，"
                  "多数个股下跌，<b>上午上涨主要由少数权重与主线拉动，而非全面性行情</b>（详见第 4 节涨跌家数）。")
        else:
            s += (f"<b>指数与个股同步承压</b>——{idx_cnt_txt(_iu, _id, _if)}，"
                  "多数个股下跌，<b>上午上涨主要由少数权重与主线拉动，而非全面性行情</b>（详见第 4 节涨跌家数）。")
    elif br["value"] == "neutral":
        _pre = "指数分化与个股涨跌大致相当" if _split else "指数与个股方向一致、涨跌大致相当"
        s += f"{_pre}，行情结构偏均衡，主线与补涨并存。"
    elif _split:
        s += (f"<b>指数内部方向分化</b>——{len(opens)} 大指数 {_iu} 涨 {_id} 跌"
              f"{('、%d 只基本持平' % _if) if _if else ''}，"
              f"小盘与大盘成长方向相反；个股层面有 {BR_up} 只上涨（{up_ratio:.1f}%），"
              "呈<b>「指数分化、个股普涨」</b>格局，指数回落主要由权重与成长龙头贡献，"
              "不应按普跌市处理（详见第 4 节涨跌家数）。")
    elif _sh is not None and _sh < -0.1:
        # 广度强但指数跌 —— 典型权重拖累 / 高低切换
        s += (f"<b>个股普涨而指数承压</b>——上涨占比达 {up_ratio:.1f}%，但上证 {_sh:+.2f}% 收跌，"
              "呈现典型的<b>权重股拖累、中小盘活跃</b>的高低切换特征（详见第 4 节涨跌家数）。")
    else:
        s += "指数与多数个股同向走强，行情具备一定广度支撑。"
    return s

def build_s02():
    m = IM.get("sh000001"); mm = mins.get("sh000001", [])
    if not m or not mm:
        return "<div class='nr'><div class='nr-b'>分时数据缺失，无法还原走势叙事。</div></div>"
    op, cp = m["open_pct"], m["am_pct"]
    hi, lo = m["hi_pct"], m["lo_pct"]; hit, lot = m["hi_t"], m["lo_t"]
    up_drv = sorted([r for r in ind_v if (r.get("netflow") or 0) > 0], key=lambda r: -(r.get("netflow") or 0))
    dn_drv = sorted([r for r in ind_v if (r.get("netflow") or 0) < 0], key=lambda r: (r.get("netflow") or 0))
    hi_first = hit <= lot
    # 形态三分支（2026-09-02 修正）：仅用 hi_first 会把「低开→探底→回升」误判为「冲高回落」。
    # 关键补一个 bounce（收盘相对日内低点的回升幅度）。
    bounce = cp - lo
    blk = []
    blk.append(("09:30–09:40<br><span class='chip'>开盘</span>",
        f"<b>上证{op:+.2f}%开盘（{m['open']:.2f}），沪深两市{'低开' if op<0 else '高开'}。</b>"
        + (f"开盘即为上午最高点 {m['high']:.2f}（{hi:+.2f}%），其后单边下行。" if hi == op
           else (f"开盘后短暂下探至上午最低 {m['low']:.2f}（{lo:+.2f}%）。" if op < 0
                 else f"开盘后短暂冲高至 {m['high']:.2f}（{hi:+.2f}%）。"))
        + "情绪面承接前收盘态势，早盘方向由主线板块决定。"))
    if hi_first and bounce > 0.3:
        # 低开 → 探底 → 回升（V 型）
        blk.append((f"09:30–{lot[:2]}:{lot[2:]}<br><span class='chip'>下探</span>",
            f"<b>低开后延续弱势单边下探。</b>上证 {lot[:2]}:{lot[2:]} 触及上午低点 {m['low']:.2f}（{lo:+.2f}%），"
            f"自开盘价回落 {op - lo:.2f} 个百分点，为上午跌幅最深时段；"
            f"净流出居前的板块（{('、'.join(esc(r['name']) for r in dn_drv[:3]) or '无明显集中流出')}）与指数下行同时出现。"))
        _rec = ("但仍未收复昨收（距平盘 %.2f 个百分点处震荡）" % abs(cp) if cp < 0
                else "并已收复昨收、由跌转涨")
        blk.append((f"{lot[:2]}:{lot[2:]}–11:30<br><span class='chip'>回升</span>",
            f"<b>探底后跌幅持续收敛。</b>上证自低点回升 {bounce:.2f} 个百分点至 {m['am_close']:.2f}（{cp:+.2f}%），"
            f"{_rec}；"
            f"资金净流入居前的板块（{('、'.join(esc(r['name']) for r in up_drv[:3]) or '无明显主线')}）与指数回升同时出现，"
            "回升属相关性观察，是否形成日内反转需以下午量能验证。"))
    elif hi_first:
        # 2026-09-11 修复缺陷 #26：hi_first 只说明"高点在低点之前"，不等于"指数盘中曾经上行"。
        # 最高价即为开盘价（全天未上行）时，写"带动指数快速拉升/冲高"与事实矛盾（当日上证
        # 开盘 -0.60% 即为全天最高，全天未上行）。按「自开盘价的上行幅度」_lift 分档。
        _lift = hi - op
        if _lift < 0.15:
            blk.append(("盘中<br><span class='chip'>单边下行</span>",
                f"<b>开盘后单边走弱、全天未见上行。</b>上证 {lot[:2]}:{lot[2:]} 回踩至 {m['low']:.2f}（{lo:+.2f}%），"
                f"净流出居前的板块（{('、'.join(esc(r['name']) for r in dn_drv[:3]) or '无明显集中流出')}）"
                "与指数下行同时出现；成交未能持续跟进是同步观察到的现象，并非唯一因果。"))
        else:
            blk.append(("早盘<br><span class='chip'>冲高</span>",
                f"<b>资金净流入居前的板块（{('、'.join(esc(r['name']) for r in up_drv[:3]) or '无明显主线')}）带动指数快速拉升。</b>"
                f"上证 {hit[:2]}:{hit[2:]} 触及上午高点 {m['high']:.2f}（{hi:+.2f}%）"
                "，其后成交未进一步放大，指数自高点回落。"))
            blk.append(("盘中<br><span class='chip'>回落</span>",
                f"<b>冲高后震荡回落。</b>上证 {lot[:2]}:{lot[2:]} 回踩至 {m['low']:.2f}（{lo:+.2f}%），"
                "成交未能持续跟进是同步观察到的现象，并非唯一因果。"))
    else:
        # 2026-09-10 修复缺陷 #24：原写死"跌幅收敛并翻红"，但当日上证盘中最高仅 -0.06%、
        # 全程未转正（低开→探底→回升→冲高→回落形态），与事实矛盾。按日内最高点分三档。
        _rec2 = ("跌幅收敛并翻红" if hi >= 0
                 else ("跌幅收敛、盘中一度逼近平盘" if hi > -0.2
                       else "跌幅有所收敛，但盘中始终未能翻红"))
        # 2026-09-14 修复缺陷 #30：原写死"部分板块发力，指数反弹冲高""但量能未能跟进，尾盘再度回落"。
        # 实测 6 个交易日中 4 天走本分支，却输出逐字相同的句子（仅时间/点位在变）：
        # 09-08(retrace 0.10) / 09-09(0.17) / 09-10(0.29) / 09-14(0.01) 全部写"尾盘再度回落"，
        # 其中 09-14 自高点仅回落 0.01 个百分点（实为持稳），09-10 高点 -0.06% 仍为负。
        # 改为引用实际 hi（是否翻正）与 retrace（回落幅度）条件化。
        _upsw = "指数自低位反弹" if hi < 0 else "指数反弹冲高"
        if m['retrace'] <= 0.15:
            _rtw = f"，此后至收盘基本持稳、自高点仅回落 {m['retrace']:.2f} 个百分点，未出现明显回落"
        elif m['retrace'] >= 0.6:
            _rtw = f"，此后自高点回落 {m['retrace']:.2f} 个百分点"
        else:
            _rtw = f"，此后自高点小幅回落 {m['retrace']:.2f} 个百分点"
        blk.append(("早盘<br><span class='chip'>下探</span>",
            f"<b>开盘后延续弱势下探。</b>上证 {lot[:2]}:{lot[2:]} 触及上午低点 {m['low']:.2f}（{lo:+.2f}%），"
            f"随后部分板块转强，{_rec2}。"))
        blk.append(("盘中<br><span class='chip'>冲高</span>",
            f"<b>部分板块发力，{_upsw}。</b>上证 {hit[:2]}:{hit[2:]} 摸高 {m['high']:.2f}（{hi:+.2f}%）"
            + _rtw + "。"))
    # 2026-09-14 修复缺陷 #30（续）：尾盘句原为常量「窄幅整理收官」+「多空分歧显著，为下午留出双向空间」，
    # 且末句以「。」开头拼接导致输出「。。」双句号；6 个交易日实测全中（含 09-11 极端普跌 -1.82%）。
    if m['retrace'] <= 0.05:
        # 2026-09-17 修复缺陷 #39：原尾句写死"全天未再跌破开盘低点"，但"上午低点高于开盘价"
        # 并非必然成立 —— 2026-09-16 上午低点 3843.28 低于开盘 3861.75（10:01 触及），
        # 该句与事实直接矛盾。按「最低价 vs 开盘价」两态 + 日内低点出现时间条件化。
        if m['low'] >= m['open']:
            _hold = "全天未跌破开盘价，下午关注量能能否跟进。"
        elif lot <= "0935":
            _hold = (f"开盘后短暂下探至上午低点 {m['low']:.2f}（{lo:+.2f}%）后回升，"
                     "此后未再跌回该位置，下午关注量能能否跟进。")
        else:
            _hold = (f"开盘后于 {lot[:2]}:{lot[2:]} 下探 {m['low']:.2f} 形成上午低点，"
                     "此后未再回落至该点位附近，下午关注量能能否跟进。")
        _tail, _tail2 = "高位持稳收官", _hold
    elif hi_first and bounce > 0.3:
        _tail, _tail2 = "低位回升后收官", "多空分歧显著，为下午留出双向空间。"
    elif m['retrace'] >= 0.6:
        _tail, _tail2 = "自高位回落收官", "多空分歧显著，为下午留出双向空间。"
    else:
        _tail, _tail2 = "窄幅整理收官", "多空分歧显著，为下午留出双向空间。"
    _tail_detail = (f"，自高点回落 {m['retrace']:.2f} 个百分点" if m['retrace'] > 0.05 else "")
    if bounce > 0.05 and m['retrace'] > 0.05:
        _tail_detail += f"、自低点回升 {bounce:.2f} 个百分点"
    blk.append(("11:15–11:30<br><span class='chip'>尾盘</span>",
        f"<b>{_tail}。</b>上证最终收 {m['am_close']:.2f}（{cp:+.2f}%）" + _tail_detail + "。" + _tail2))
    # 资金主线与个股广度联动（2026-09-04 修复：74.5% 普涨日仍硬编码"少数主线拉动"）
    if up_ratio < 45:
        _mkt = "上涨由少数主线拉动而非全面行情（详见第4节涨跌家数）。"
    elif up_ratio < 55:
        _mkt = "主线与个股表现分化并存，普涨面一般（详见第4节涨跌家数）。"
    else:
        _mkt = "个股普涨、赚钱效应较广，资金主线为其中强度居前的方向（详见第4节涨跌家数）。"
    blk.append(("主线<br><span class='chip'>资金</span>",
        f"<b>资金主线：</b>净流入居前——{fmt_drv(up_drv)}；净流出居前——{fmt_drv(dn_drv)}。"
        + _mkt))
    return "".join(f"<div class='nr'><div class='nr-t'>{t}</div><div class='nr-b'>{b}</div></div>" for t, b in blk)

def build_s03_note():
    strong = sorted([r for r in ind_v if persist(r)[0] >= 2 and (r.get("netflow") or 0) > 0], key=lambda r: -persist(r)[0])
    weak = sorted([r for r in ind_v if persist(r)[0] <= 0 and (r.get("netflow") or 0) < 0], key=lambda r: persist(r)[0])
    def desc(r):
        ld = (f"，龙头{esc(r.get('lead'))} {r.get('lead_pct') or 0:+.2f}%" if (r.get('lead_pct') or 0) >= 5 else "")
        return f"{esc(r['name'])} {r['pct']:+.2f}%（主力{(r.get('netflow') or 0)/1e8:+.1f}亿，{r.get('up') or 0}涨{r.get('down') or 0}跌{ld}）"
    s = "<b>盘中强度研判 —— 上涨主线（强）：</b>"
    s += ("；".join(desc(r) for r in strong[:3]) + "，资金、宽度、龙头三项指标偏正面，<b>盘中强度居前</b>；"
          "能否延续需观察下午量能与资金面变化，不预先承诺延续性。<br>"
          if strong else "未见资金与宽度双验证的强主线。<br>")
    s += "<b>盘中强度研判 —— 下跌主线（弱势）：</b>"
    s += ("；".join(desc(r) for r in weak[:3]) + "，伴随主力净流出，<b>盘中强度居后</b>；"
          "是否构成系统性调整需进一步验证，不宜简单抄底。<br>"
          if weak else "暂无显著主力出逃板块。<br>")
    cpo = find_ind("CPO") or find_ind("光模块") or next((r for r in con_v if "CPO" in r['name'] or "光模块" in r['name']), None)
    if cpo and (cpo.get("netflow") or 0) > 0 and cpo["pct"] < 1.5:
        s += (f"<b>需警惕的分歧信号：</b>{esc(cpo['name'])} 主力净流入 {(cpo.get('netflow') or 0)/1e8:+.1f}亿 "
              f"但指数仅 {cpo['pct']:+.2f}%，高位承接不足，下午重点观察能否重新走强。")
    return s

# 影响映射（2026-09-02 重写为通用版）：匹配按顺序取首个命中，
# 因此「具体词条」必须排在「泛化词」之前（如"降准"先于"央行"，"净回笼"先于"流动性"）。
IMPACT = {
    # —— 货币流动性：方向由操作方向决定 ——
    "降准": ("银行/地产/券商", "偏多", "imp-pos"), "降息": ("银行/地产/成长", "偏多", "imp-pos"),
    "净投放": ("宏观流动性", "偏多", "imp-pos"), "净回笼": ("宏观流动性", "偏空", "imp-neg"),
    "MLF": ("宏观流动性", "中性", "imp-neu"), "LPR": ("银行/地产", "中性", "imp-neu"),
    "逆回购": ("宏观流动性", "中性", "imp-neu"), "央行": ("宏观流动性", "中性", "imp-neu"),
    "人民银行": ("宏观流动性", "中性", "imp-neu"),
    "流动性": ("宏观流动性", "中性", "imp-neu"), "资金面": ("宏观流动性", "中性", "imp-neu"),
    # —— 财政与产业政策 ——
    "专项债": ("基建/建材", "偏多", "imp-pos"), "国债": ("基建/银行", "偏多", "imp-pos"),
    "减税": ("全市场", "偏多", "imp-pos"), "税收": ("全市场", "中性", "imp-neu"),
    "财政部": ("宏观/顺周期", "偏多", "imp-pos"),
    "能源局": ("电力设备/电网", "偏多", "imp-pos"), "电网": ("电力设备/电网", "偏多", "imp-pos"),
    "工信部": ("制造/科技", "偏多", "imp-pos"), "发改委": ("基建/产业", "偏多", "imp-pos"),
    "商务部": ("消费/外贸", "偏多", "imp-pos"), "国资委": ("国企改革", "偏多", "imp-pos"),
    "人工智能": ("科技成长/算力", "偏多", "imp-pos"), "算力": ("科技成长/算力", "偏多", "imp-pos"),
    "政策": ("产业政策", "中性", "imp-neu"), "规划": ("产业政策", "中性", "imp-neu"),
    "部署": ("产业政策", "中性", "imp-neu"), "方案": ("产业政策", "中性", "imp-neu"),
    # —— 宏观数据 ——
    "统计局": ("宏观/顺周期", "中性", "imp-neu"), "PMI": ("宏观/顺周期", "中性", "imp-neu"),
    "GDP": ("宏观/顺周期", "中性", "imp-neu"), "CPI": ("消费/通胀", "中性", "imp-neu"),
    "PPI": ("周期/资源", "中性", "imp-neu"), "社融": ("银行/宏观", "中性", "imp-neu"),
    "进出口": ("出口链", "中性", "imp-neu"), "外贸": ("出口链", "中性", "imp-neu"),
    # —— 监管与市场 ——
    "IPO": ("券商/次新", "中性", "imp-neu"), "注册制": ("券商/成长", "中性", "imp-neu"),
    "退市": ("绩差/ST", "偏空", "imp-neg"), "证监会": ("券商/市场", "中性", "imp-neu"),
    "北交所": ("券商/中小盘", "中性", "imp-neu"), "两融": ("券商/市场", "中性", "imp-neu"),
    "回购": ("相关个股", "偏多", "imp-pos"), "增持": ("相关个股", "偏多", "imp-pos"),
    "减持": ("相关个股", "偏空", "imp-neg"),
    # —— 行业主题 ——
    "芯片": ("半导体", "偏多", "imp-pos"), "半导体": ("半导体", "偏多", "imp-pos"),
    "集成电路": ("半导体", "偏多", "imp-pos"), "机器人": ("机器人/自动化", "偏多", "imp-pos"),
    "光伏": ("新能源", "偏多", "imp-pos"), "储能": ("新能源", "偏多", "imp-pos"),
    "新能源": ("新能源", "偏多", "imp-pos"), "军工": ("军工装备", "偏多", "imp-pos"),
    "医保": ("医药", "中性", "imp-neu"), "集采": ("医药", "偏空", "imp-neg"),
    "医药": ("医药", "中性", "imp-neu"), "汽车": ("汽车链", "中性", "imp-neu"),
    "房地产": ("地产链", "中性", "imp-neu"), "楼市": ("地产链", "中性", "imp-neu"),
    "消费": ("消费", "偏多", "imp-pos"), "内需": ("消费", "偏多", "imp-pos"),
    "稀土": ("小金属/稀土", "中性", "imp-neu"), "煤炭": ("煤炭", "中性", "imp-neu"),
    "钢铁": ("钢铁", "中性", "imp-neu"), "有色": ("有色金属", "中性", "imp-neu"),
    "黄金": ("贵金属", "中性", "imp-neu"), "原油": ("石化", "中性", "imp-neu"),
    "种业": ("农业", "中性", "imp-neu"), "农业": ("农业", "中性", "imp-neu"),
    # —— 公司业绩 ——
    "预增": ("相关个股", "偏多", "imp-pos"), "预亏": ("相关个股", "偏空", "imp-neg"),
    "并购": ("相关个股", "偏多", "imp-pos"), "重组": ("相关个股", "偏多", "imp-pos"),
    "业绩": ("相关个股", "中性", "imp-neu"), "盈利": ("全市场", "中性", "imp-neu"),
    # —— 外部环境 ——
    "美联储": ("外部风险", "中性", "imp-neu"), "美股": ("外部风险", "中性", "imp-neu"),
    "纳斯达克": ("外部风险", "中性", "imp-neu"), "人民币": ("汇率/出口", "中性", "imp-neu"),
    "汇率": ("汇率/出口", "中性", "imp-neu"), "关税": ("出口链", "偏空", "imp-neg"),
    "贸易摩擦": ("出口链", "偏空", "imp-neg"),
}

def build_s05_note():
    groups = {}
    for n in sel:
        t = n.get("title") or ""
        sect, view, klass = "大盘", "中性", "imp-neu"
        for k_, v in IMPACT.items():
            if k_ in t:
                sect, view, klass = v; break
        groups.setdefault(sect, []).append(t)
    # 「重大宏观催化」判定泛化（2026-09-02 修正）：原列表只认某一天的特定事件（恒大/临港/以旧换新…），
    # 换一天恒定输出「上午无重大宏观催化」，与表格里 14 条资讯自相矛盾。
    CORE_KEY = ["财政部", "专项债", "特别国债", "贴息", "以旧换新", "降准", "降息", "LPR",
                "净投放", "净回笼", "央行", "人民银行", "发改委", "国务院", "统计局", "证监会",
                "关税", "贸易摩擦", "美联储", "PMI", "GDP", "社融", "两会", "中央经济工作会议"]
    core = [n.get("title") for n in sel if any(k in (n.get("title") or "") for k in CORE_KEY)]
    s = "<b>核心事件：</b>" + ("；".join(esc(c) for c in core[:4]) if core
                              else "筛选出的资讯中未出现央行/财政/统计口径的重大宏观事件，上午以行业与监管类信息为主。")
    s += "<br><b>影响分布：</b>" + "、".join(f"{esc(k)}（{len(v)}条）" for k, v in list(groups.items())[:6])
    topc = sorted(con_v, key=lambda r: -(r.get("netflow") or 0))[:1]
    rank_map = {r["name"]: i + 1 for i, r in
                enumerate(sorted(con_v, key=lambda r: -(r.get("netflow") or 0)))}
    if topc and (topc[0].get("netflow") or 0) > 0:
        rk = rank_map.get(topc[0]['name'], 1)
        s += (f"<br><b>资金焦点：</b>{esc(topc[0]['name'])} 概念主力净流入 "
              f"{(topc[0].get('netflow') or 0)/1e8:+.1f}亿（概念板块净流入排名第{rk}），"
              f"为相对集中的资金方向，按排名而非绝对额判定。")
    return s

def build_s06_note():
    top_streak = max(streaks) if streaks else 0
    top_name = next((r['name'] for r in multi if r.get('streak') == top_streak), "—")
    s = (f"<b>情绪判读：赚钱效应{'显著恶化' if up_ratio < 40 else ('一般' if up_ratio < 55 else '尚可')}，"
         f"涨停结构{'脆弱' if top_streak <= 3 else '尚可'}。</b>")
    # 「背离」只在指数与个股方向相反时才成立；同跌应叫「同步走弱」（2026-09-02 修正）
    # 2026-09-15 修复缺陷 #34：原 else 分支只覆盖「指数内部分化 × 个股普涨」（且带
    # up_ratio >= 55 条件），其余形态一律落兜底句"指数与个股方向大体一致"。
    # 多形态实测 11 种形态：6 种落兜底句，其中 2 种明确错误——
    #   ① weight_drag（7 指数全跌 -0.60% × 66% 个股普涨）：方向实际相反，M01 同页写
    #      "个股普涨而指数承压、权重股拖累"，M06 却说"大体一致"；
    #   ② 2026-09-15（涨跌互现 × 27.9% 普跌）：M01 已写"指数内部分化 × 个股普跌"。
    # → 改为「指数方向（普涨/普跌/分化）× 广度档（强≥55/中45–55/弱<45）」3×3 二维判定，
    #   与 M01/M04 共用 idx_dir() 同一事实源。
    _iu6, _id6, _if6, _split6, _t6 = idx_dir()
    if _split6:
        _bidir = "指数内部方向分化（%d 涨 %d 跌）、" % (_iu6, _id6)
        _rel = (_bidir + ("个股层面偏普涨" if up_ratio >= 55 else
                          ("个股层面偏普跌" if up_ratio < 45 else "个股涨跌大致相当")))
    elif _t6 == "up":
        _rel = ("指数与个股同向走强" if up_ratio >= 55 else
                ("指数走强而个股涨跌相当" if up_ratio >= 45 else
                 "指数区间偏强而个股普跌，普通持仓体验明显差于指数"))
    elif _t6 == "flat":
        # 7 大指数全部落在 ±0.05% 死区（罕见）：不给方向断言，按广度出中性表述
        _rel = ("指数大体走平、个股普涨" if up_ratio >= 55 else
                ("指数与个股涨跌大致相当" if up_ratio >= 45 else "指数大体走平、个股层面偏普跌"))
    else:
        _rel = ("指数收跌而个股普涨，方向相反、权重股拖累特征明显" if up_ratio >= 55 else
                ("指数与个股同向走弱，个股涨跌大致相当" if up_ratio >= 45 else
                 "指数与个股同步走弱，缺少逆势赚钱效应"))
    # "仅 X 只上涨"在 X 占比过半时不成立（2026-08-25：3684 只上涨 / 66.4% 却写"仅"）
    _upw = "仅" if up_ratio < 45 else "有"
    s += (f"① <b>市场宽度{'极差' if up_ratio < 40 else ('一般' if up_ratio < 55 else '良好')}</b>"
          f"——全市场 {BR_tot} 只标的中{_upw} <b>{BR_up} 只上涨（{up_ratio:.1f}%）</b>，"
          f"{BR_dn} 只下跌，{_rel}；")
    s += (f"② <b>连板高度{'极低' if top_streak <= 3 else '中等'}</b>——最高仅 <b>{top_streak} 板</b>（{esc(top_name)}），"
          f"2 板 {streaks.get(2,0)} 只，首板 {streaks.get(1,0)} 只，缺乏高标引领，题材空间被压缩；")
    if zt_ind:
        ind_str = "、".join(f"{k}({v})" for k, v in zt_ind[:5])
        s += f"③ <b>涨停行业分散</b>——{ind_str}，未形成单一强势主线合力。"
    s += "<br><b>结构性提示：</b>涨停池中可能包含与整体板块背离的个别事件驱动股，不能因个别涨停误判所在板块已止跌。"
    return s

def build_s07_note():
    if not HOLD:
        return "未配置持仓，跳过持仓组合小结（持仓为个人私有配置，不随 Skill 分发；配置见 holdings.example.json）。"
    best = worst = None
    for code, name, cost in HOLD:
        m = HM.get(code)
        if not m: continue
        if best is None or m["am_pct"] > HM[best]["am_pct"]: best = code
        if worst is None or m["am_pct"] < HM[worst]["am_pct"]: worst = code
    s = "<b>持仓组合小结：</b>"
    for code, name, cost in HOLD:
        m = HM.get(code)
        if not m: continue
        tag = "最强" if code == best else ("最弱" if code == worst else "")
        s += f"<b>{name}</b>{('（'+tag+'）') if tag else ''}上午 {m['am_pct']:+.2f}%；"
    s += "组合内部表现分化，强弱与当日市场结构相关。<br>"
    if any(cost is None for _, _, cost in HOLD):
        s += ("<br><b>⚠ 数据缺口：</b>部分持仓<b>成本价未在记录中留存</b>，无法计算浮亏率，"
              "仅能给出当日涨跌与技术面判断。建议补录成本数据以便完整跟踪盈亏。")
    return s

def build_s08():
    risks = []; opps = []
    # 宽度风险（数据驱动标签，去"极差"主观化 → "偏弱"）
    # 宽度风险需「指数方向 × 上涨占比」二维判定（2026-09-02 / 2026-08-25 两次实测修正）：
    # 指数跌+个股普涨 既不是"指数红账户绿"，也不是"同步下行"，而是权重拖累。
    _sh = (IM.get("sh000001") or {}).get("am_pct")
    _upw = "仅" if up_ratio < 45 else "有"
    _w_risk = None  # None = 该形态无宽度风险条（见下方四象限注释）
    # 宽度风险条必须「指数方向 × 上涨占比」二维四象限判定（2026-09-04 修复）：
    #   指数红×普涨(≥55) = 共振健康形态 → 无宽度风险，跳过（旧分支只看指数方向，
    #     在 9/4 上证+0.35%/74.5%普涨日误报"宽度偏弱/指数红账户绿"）
    #   指数平/跌×普涨(≥55) = 权重拖累 → 用权重补跌表述（缺陷13，2026-08-25 实测）
    #   指数红×宽度不足(<55) = 背离 → "指数红、账户绿"仅在此象限成立
    #   指数平/跌×宽度不足(<55) = 同步承压
    if up_ratio >= 55:
        if _sh is not None and _sh <= 0.05:
            _w_risk = ("中", "l-m",
                       f"<b>指数承压而个股普涨，存在权重股补跌拖累指数的风险。</b>"
                       f"上证 {_sh:+.2f}% 收跌但多数个股上涨，说明回落集中在权重股；"
                       f"若下午权重股跌势扩散，前期抗跌的中小盘存在补跌可能。")
    elif _sh is not None and _sh > 0.05:
        _lv = "高" if up_ratio < 40 else "中"
        _w_risk = (_lv, "l-h" if up_ratio < 40 else "l-m",
                   f"<b>指数偏强而宽度不足，\"指数红、账户绿\"风险。</b>"
                   f"{_upw} {up_ratio:.1f}%（{BR_up}/{BR_tot}）个股上涨，{BR_dn} 只下跌，"
                   f"赚钱效应集中在少数主线，追高非主线品种胜率低。")
    else:
        _lv = "高" if up_ratio < 40 else "中"
        _w_risk = (_lv, "l-h" if up_ratio < 40 else "l-m",
                   f"<b>市场宽度偏弱，指数（上证 {_sh:+.2f}%）与个股同步下行。</b>"
                   f"{_upw} {up_ratio:.1f}%（{BR_up}/{BR_tot}）个股上涨，{BR_dn} 只下跌，"
                   f"赚钱效应集中在少数主线，追高非主线品种胜率低。")
    if _w_risk:
        risks.append(_w_risk)
    # 高位回落板块补跌压力（仅陈述事实 + 条件）
    for c, nm in [("sh000688", "科创50"), ("sz399006", "创业板指")]:
        mm = IM.get(c)
        if mm and mm["retrace"] > 1.0:
            # 2026-09-11 缺陷 #26 同族：开盘即高点（自开盘无上行）时不能叫"冲高"。
            _shp = "冲高回落" if (mm["hi_pct"] - mm["open_pct"]) >= 0.15 else "开盘后单边下行"
            risks.append(("高", "l-h", f"<b>{nm} {_shp}，高位品种存补跌压力。</b>{nm} 自高点回落 {mm['retrace']:.2f} 个百分点，"
                          f"下午若失守昨收（{mm['prev']:.2f}）可能触发获利盘与融资盘止损，属需观察的尾部风险。"))
    # 主力净流出板块（相关性，非因果"直接压制"）
    # 2026-09-11 修复缺陷 #27：原取 ind_v 中前两个满足「强度<=0 且净流出」的板块，但 ind_v 按涨幅
    # 降序排列，命中的往往是几乎持平、仅微幅净流出的冷门板块（当日为"橡胶助剂 -0.0亿"），
    # 与标题「对相关持仓形成压制」不符，也不构成有效风险提示。
    # 改为按主力净流出额从大到小取前 2 个（净流出最显著方向），并显式标注是否与持仓所属板块重叠。
    # 2026-09-14 修复缺陷 #33：重叠判定原只按 hold_ctx.sector 精确匹配，而某持仓 ETF（sh11xxxx
    # 「某通信主题ETF」）的 sector 为 null → 「通信 -52.0亿」「通信设备 -54.5亿」未判为重叠，报告写
    # "与持仓所属方向无直接重叠"，但该 ETF 恰为当日组合内最弱（-2.07%），相关性明显。
    # 补：从持仓名称剥离 ETF / 基金公司 / 转债 等后缀取关键词（如"某通信主题ETF"→"通信"）后双向包含匹配。
    _SUF = ("ETF", "LOF", "转债", "国泰", "华夏", "易方达", "广发", "南方", "嘉实", "天弘",
            "华宝", "银华", "工银", "汇添富", "富国", "招商", "博时", "鹏华", "建信",
            "中银", "平安", "兴业", "景顺", "万家", "海富通", "国联安")
    _kw = set()
    for _c, _n, _cc in HOLD:
        _sec = (HOLD_CTX.get(_c) or {}).get("sector")
        if _sec:
            _kw.add(_sec)
        _nm = (_n or "")
        for _s in _SUF:
            _nm = _nm.replace(_s, "")
        if len(_nm) >= 2:
            _kw.add(_nm[:2])
    _kw = {k for k in _kw if k}

    def _ovl(secname):
        return any(k and (k in secname or secname in k) for k in _kw)

    weak = sorted([r for r in ind_v if (r.get("netflow") or 0) < 0],
                  key=lambda r: (r.get("netflow") or 0))[:2]
    if weak:
        _rel = [r["name"] for r in weak if _ovl(r.get("name") or "")]
        _rel_txt = (f"，其中 {'、'.join(_rel)} 为持仓所属方向" if _rel
                    else "，与持仓所属方向无直接重叠（仅作板块资金面参考）")
        risks.append(("高", "l-h", "<b>主力净流出居前的板块对相关方向形成压制（相关性）。</b>"
                      + "；".join(f"{esc(r['name'])} {(r.get('netflow') or 0)/1e8:+.1f}亿（{r.get('up') or 0}涨{r.get('down') or 0}跌）" for r in weak)
                      + _rel_txt + "，资金面是否转向需进一步验证，不宜据此直接抄底。"))
    # 量能（去"缺乏向上突破基础"因果，改为事实+条件；2026-09-08 按 regime 条件化开头措辞）
    risks.append(("中", "l-m", f"<b>{_VOL_RISK}</b>沪市上午成交量约为前5日全日均量的 {VS['ratio']:.1f}%，"
                  f"推算全日约 {VS['proj_vs_avg5']:.0f}%；上攻需放量配合，否则更可能以震荡收敛为主。"))
    top_streak = max(streaks) if streaks else 0
    # 连板高度（去"炸板率偏高/多数连板股盘中开板"硬编码，只陈述高度）
    risks.append(("中", "l-m", f"<b>连板高度仅 {top_streak} 板，题材接力意愿偏弱。</b>最高板 {top_streak} 板，"
                  "低位首板可参与、高位追板风险大，需结合个股分时与板块联动判断。"))
    neg = [n for n in sel if any(k in (n.get('title') or '') for k in ["破产", "恒大", "违约"])]
    if neg:
        risks.append(("中", "l-m", f"<b>偏空事件：</b>{esc(neg[0]['title'])}，对相关产业链情绪形成压制，下午留意反应。"))
    # 周末窗口（仅当真实周五才提示，不硬编码"周五效应"）
    wd = FACTS.get("weekday", {}).get("value", -1)
    if wd == 4:
        risks.append(("低", "l-l", "<b>周末窗口：</b>今日为周五，部分资金可能降低周末敞口，尾盘（14:30后）或现获利了结，高位品种尤甚；"
                      "此为历史统计上的倾向，并非必然，需结合盘面确认。"))
    else:
        risks.append(("低", "l-l", "<b>隔夜外盘不确定性。</b>若晚间海外风险资产波动放大，可能影响次日开盘情绪，属需关注的外部变量。"))
    # 机会：双验证主线（去"确定性最高"）
    strong = [r for r in ind_v if persist(r)[0] >= 2 and (r.get("netflow") or 0) > 0][:3]
    if strong:
        opps.append(("高", "l-h", "<b>资金与宽度双验证的板块盘中强度居前，可重点跟踪。</b>"
                     + "；".join(f"{esc(r['name'])} {r['pct']:+.2f}%（主力{(r.get('netflow') or 0)/1e8:+.1f}亿，{r.get('up') or 0}涨{r.get('down') or 0}跌）" for r in strong)
                     + "，三项指标全部为正；能否延续以下午量能与资金面为准。"))
    topc = sorted(con_v, key=lambda r: -(r.get("netflow") or 0))[:1]
    if topc and (topc[0].get("netflow") or 0) > 0:
        # 「高位分歧 / 未收复昨收 / 收复转涨」三态，按上证方向与日内形态条件化
        # （2026-09-04 修复：指数红+普涨日旧 else 写死"尚未收复昨收"，与上证收红矛盾）
        _shm = IM.get("sh000001") or {}
        _sh_pct = _shm.get("am_pct")
        # 2026-09-11 缺陷 #26 同族：开盘即高点时不存在"高位分歧"（全天未上行）。
        _sh_lift = (_shm.get("hi_pct", 0) or 0) - (_shm.get("open_pct", 0) or 0)
        if _shm.get("hi_t") and _shm.get("lo_t") and _shm["hi_t"] <= _shm["lo_t"] and _shm.get("retrace", 0) > 0.8:
            _div = ("但指数自高点回落，显示高位分歧" if _sh_lift >= 0.15
                    else "但指数自开盘起单边回落、尚未出现企稳信号")
        elif _sh_pct is not None and _sh_pct < 0:
            _div = f"但上证仍收跌 {_sh_pct:+.2f}%，主线能否带动指数修复仍待验证"
        else:
            _div = "指数已收复昨收，该方向资金靠前；需防资金一致性过高后的短线分歧"
        opps.append(("中", "l-m", f"<b>{esc(topc[0]['name'])} 资金相对集中，需等分歧修复。</b>主力净流入 {(topc[0].get('netflow') or 0)/1e8:+.1f}亿，"
                      f"{_div}；若下午重新放量走强则主线确认。"))
    # 支撑位（去"下方空间有限"因果，改为参考位）
    opps.append(("中", "l-m", f"<b>上证 {IM['sh000001']['low']:.2f} 为日内明确低点参考。</b>上午最低获支撑后收回，"
                 "该位可视为震荡格局下的支撑参考，跌破则打开下方空间，需密切观察。"))
    # 财政主题（去"常在下午或次日二次发酵"确定性，改为条件机会）
    if any("财政部" in (n.get('title') or '') or "贴息" in (n.get('title') or '') or "专项债" in (n.get('title') or '')
           for n in sel):
        opps.append(("中", "l-m", "<b>财政政策链的主题机会。</b>贴息上限上调、专项债与特别国债待发、以旧换新等主题若存在进一步催化，"
                     "可留意低位补涨；需事件确认，不预设必然发酵。"))
    for code, name, cost in HOLD:
        ctx = HOLD_CTX.get(code)
        if ctx and ctx.get("sector"):
            sr = find_ind(ctx["sector"])
            if sr and sr["pct"] > 1.5 and (sr.get("netflow") or 0) > 0:
                opps.append(("高", "l-h", f"<b>{name} 处于风口。</b>所属{esc(sr['name'])}板块 {sr['pct']:+.2f}%（主力"
                             f"{(sr.get('netflow') or 0)/1e8:+.1f}亿，{sr.get('up') or 0}涨{sr.get('down') or 0}跌），为持仓中相对强势的标的。"))
    if len(opps) < 3:
        opps.append(("低", "l-l", "<b>错杀反弹的博弈机会。</b>若下午出现放量止跌的超跌板块，可做短线超跌反弹，但需严格止损、不宜重仓。"))
    def li(items):
        return "".join(f"<li><span class='lvl {lv}'>{lvl}</span>{b}</li>" for lvl, lv, b in items)
    return li(risks), li(opps)

# ================= 持仓点评（数据驱动） =================
def tech_signal(code, m):
    sig = []
    if not m: return sig
    p = m["am_close"]
    for lbl, key in [("MA5", "ma5"), ("MA10", "ma10"), ("MA20", "ma20"), ("MA60", "ma60")]:
        v = m.get(key)
        if v:
            d = (p / v - 1) * 100
            sig.append((lbl, v, d))
    return sig

def build_hold_note(code, name, cost, m, fl, secr, label=None):
    parts = []
    # 2026-09-11 修复缺陷 #25：原按固定阈值 am_pct<-1.5 就地打「上午最弱持仓」，
    # 普跌日会出现 4 只同时被标"最弱"（互斥最高级却重复出现）→ 标签改由调用方按
    # 组合内相对排名统一分配（label 传入），未传入时退回单只绝对阈值表述。
    if label:
        pass
    elif m["am_pct"] > 1.5: label = "上午走强持仓"
    elif m["am_pct"] < -1.5: label = "上午走弱持仓"
    else: label = "上午震荡持仓"
    # 形态需区分「冲高回落」与「下探后回升（V型）」：
    # 仅看 retrace 会把「低开→探底→回升」误判成「冲高回落」（2026-09-02 实测踩坑）。
    bounce = m["am_pct"] - m["lo_pct"]          # 自日内低点回升幅度（百分点）
    lift = m["hi_pct"] - m["open_pct"]          # 自开盘价上行幅度（百分点）
    if m["am_pct"] > 0.5 and m["retrace"] < 0.5:
        shape = "单边走高"
    elif bounce > 0.5 and m["retrace"] > 0.8:
        shape = "先抑后扬（V 型）"
    elif m["retrace"] > 0.8:
        # 2026-09-11 缺陷 #26 同族：最高价≈开盘价时全天未上行，不能叫"冲高回落"。
        shape = "冲高回落" if lift >= 0.15 else "单边回落"
    else:
        shape = "窄幅整理"
    # 开盘方式条件化（去固定"平开后"）
    op = m["open_pct"]
    open_word = "平开后" if abs(op) < 0.15 else ("高开后" if op > 0 else "低开后")
    parts.append(f"<b>{label}。</b>{open_word}{shape}，{m['hi_t'][:2]}:{m['hi_t'][2:]} 触及日内高点 {m['high']:.2f}（{m['hi_pct']:+.2f}%），"
                 f"{m['lo_t'][:2]}:{m['lo_t'][2:]} 下探日内低点 {m['low']:.2f}（{m['lo_pct']:+.2f}%），"
                 f"11:30 收 {m['am_close']:.2f}（{m['am_pct']:+.2f}%），自高点回落 {m['retrace']:.2f}%、自低点回升 {bounce:.2f}%。")
    if secr:
        ld = (f"，龙头{esc(secr['lead'])} {secr['lead_pct']:+.2f}%" if (secr.get('lead_pct') or 0) >= 5 else "")
        # 2026-09-15 修复缺陷 #35：原句尾为固定串"与个股同向/背离需结合比较"——不引用任何数据、
        # 同一日多只持仓逐字重复（当日 示例个股A +4.66%/钨 +4.75%、示例个股B +1.69%/半导体 +1.56%
        # 两条完全相同），读起来是半句未完成的话。改为按「板块涨幅 - 个股涨幅」差值给出实际比较。
        _gap = (secr.get("pct") or 0) - m["am_pct"]
        _cmp = ("板块与个股涨幅基本同步" if abs(_gap) < 1.0 else
                (f"个股涨幅领先板块 {abs(_gap):.2f} 个百分点" if _gap < 0 else
                 f"个股涨幅落后板块 {_gap:.2f} 个百分点"))
        parts.append(f"所属<b>{esc(secr['name'])}板块 {secr['pct']:+.2f}%（主力{(secr.get('netflow') or 0)/1e8:+.2f}亿，"
                     f"{secr.get('up') or 0}涨{secr.get('down') or 0}跌{ld}），{_cmp}。")
    if fl:
        main, huge, big, small = fl['main'], fl.get('huge', 0), fl.get('big', 0), fl.get('small', 0)
        if main > 0:
            parts.append(f"个股主力净流入 {main/1e4:+.0f} 万（超大单 {huge/1e4:+.0f} 万），资金结构偏强。")
        else:
            # 去"主力派发、散户接盘"因果判定，改为事实陈述 + 需综合判断
            parts.append(f"个股主力净流出 {abs(main)/1e4:.0f} 万（大单 {abs(big)/1e4:.0f} 万），小单净流入 {small/1e4:+.0f} 万，"
                         "资金结构偏弱，需结合板块与承接情况综合判断。")
    sigs = tech_signal(code, m)
    if sigs:
        above = [l for l, v, d in sigs if d > 0]
        tech_txt = "技术面" + ('已站上 ' + "、".join(above) if above else '仍处均线下方')
        # 成本价距离条件化（去固定"仍有较大距离/深套格局未改"）
        if cost:
            d_pct = (m["am_close"] - cost) / cost * 100
            if d_pct < -10: dist = f"距成本 {cost:.2f} 仍有较大距离（暂处深套约 {d_pct:+.1f}%）"
            elif d_pct < -2: dist = f"小幅低于成本 {cost:.2f}（约 {d_pct:+.1f}%）"
            elif d_pct <= 2: dist = f"接近成本 {cost:.2f}（约 {d_pct:+.1f}%）"
            else: dist = f"已高于成本 {cost:.2f}（扭亏约 {d_pct:+.1f}%）"
            parts.append(tech_txt + f"，{dist}。")
        else:
            parts.append(tech_txt + "，成本未记录，浮亏率无法给出。")
    # 操作建议条件化（去"板块主线明确且资金持续流入""下跌中继"硬编码）
    if m["am_pct"] > 1.5 and (not fl or fl['main'] > 0):
        parts.append("<b>操作</b>：板块强度居前且主力净流入，可持有；若冲高未能有效放量突破，可考虑减仓做T降低成本。")
    elif fl and fl['main'] < 0:
        parts.append("<b>操作</b>：反弹至日内高点区建议减仓；若跌破日内低点则短线止损，不宜在主力流出背景下补仓摊薄。")
    elif fl and fl['main'] > 0:
        # 主力净流入：原模板此处误写「主力净流出背景下不宜加仓」，与资金事实自相矛盾（2026-09-02 修正）
        parts.append("<b>操作</b>：主力资金净流入，可继续持有观察；以日内低点为止损参考，"
                     "跌破则减仓。仓位是否增加需结合板块资金面与量能，不宜仅凭个股资金单因子加码。")
    else:
        parts.append("<b>操作</b>：资金流数据缺失，暂以日内低点为止损参考，等待板块资金面与量能给出更明确信号。")
    return "".join(parts)

# ================= 组装 HTML =================
def idx_row(c, n):
    m = IM[c]
    if not m: return ""
    return f"""<tr>
<td class="nm">{n}</td>
<td class="num">{m['am_close']:.2f}</td>
<td class="num {cls(m['am_pct'])}"><b>{sgn(m['am_pct'])}%</b></td>
<td class="num {cls(m['open_pct'])}">{sgn(m['open_pct'])}%</td>
<td class="num">{m['high']:.2f}<span class="sub {cls(m['hi_pct'])}">{sgn(m['hi_pct'])}%</span></td>
<td class="num">{m['low']:.2f}<span class="sub {cls(m['lo_pct'])}">{sgn(m['lo_pct'])}%</span></td>
<td class="num">{m['amp']:.2f}%</td>
<td class="num {cls(m['now_pct'])}">{m['now']:.2f} <span class="sub {cls(m['now_pct'])}">{sgn(m['now_pct'])}%</span></td>
</tr>"""

idx_rows = "".join(idx_row(c, n) for c, n in IDX)

idx_cards = ""
for c, n in IDX:
    m = IM[c]
    if not m: continue
    idx_cards += f"""<div class="mini">
<div class="mini-h"><span class="mini-n">{n}</span><span class="mini-p {cls(m['am_pct'])}">{sgn(m['am_pct'])}%</span></div>
{spark(c, label=n)}
<div class="mini-f">高 {m['hi_t'][:2]}:{m['hi_t'][2:]} {sgn(m['hi_pct'])}% · 低 {m['lo_t'][:2]}:{m['lo_t'][2:]} {sgn(m['lo_pct'])}% · 自高点回落 {m['retrace']:.2f}%</div>
</div>"""

def sec_card(r, rank, kind):
    sc, lv, lc, note = persist(r)
    nf = (r.get("netflow") or 0) / 1e8
    return f"""<div class="sec {'sec-up' if kind=='up' else 'sec-dn'}">
<div class="sec-h"><span class="rk">{rank}</span><span class="sec-n">{esc(r['name'])}</span>
<span class="sec-p {cls(r['pct'])}">{r['pct']:+.2f}%</span></div>
<div class="sec-b">
<div class="kv"><span>主力净额</span><b class="{cls(nf)}">{nf:+.2f}亿</b></div>
<div class="kv"><span>板块内涨跌</span><b>{r.get('up') or 0}涨 / {r.get('down') or 0}跌</b></div>
<div class="kv"><span>龙头</span><b>{esc(r.get('lead'))} <span class="{cls(r.get('lead_pct') or 0)}">{(r.get('lead_pct') or 0):+.2f}%</span></b></div>
</div>
<div class="tag {lc}">{lv}</div>
<div class="sec-note">{esc(note)}</div>
</div>"""

top_cards = "".join(sec_card(r, i + 1, "up") for i, r in enumerate(TOP5))
bot_cards = "".join(sec_card(r, i + 1, "dn") for i, r in enumerate(BOT5))

def con_rows(lst, n=8):
    o = ""
    for r in lst[:n]:
        nf = (r.get("netflow") or 0) / 1e8
        o += (f"<tr><td class='nm'>{esc(r['name'])}</td><td class='num {cls(r['pct'])}'>{r['pct']:+.2f}%</td>"
              f"<td class='num {cls(nf)}'>{nf:+.2f}亿</td><td>{esc(r.get('lead'))}</td></tr>")
    return o

zt_rows = ""
for r in multi[:12]:
    ft = str(r.get("first_time") or "").zfill(6)[:4]
    zt_rows += (f"<tr><td class='nm'>{esc(r['name'])}<span class='code'>{esc(r['code'])}</span></td>"
                f"<td class='num'><b class='up'>{r.get('streak')}板</b></td>"
                f"<td class='num'>{ft[:2]}:{ft[2:]}</td>"
                f"<td class='num'>{r.get('open_times')}次</td>"
                f"<td>{esc(r.get('industry'))}</td></tr>")

news_rows = ""
for n in sel:
    t = n.get("title") or ""
    sect, view, klass = "大盘", "中性", "imp-neu"
    for k_, v in IMPACT.items():
        if k_ in t:
            sect, view, klass = v; break
    tm = (n.get("time") or "")[-8:-3]
    news_rows += (f"<tr><td class='num tm'>{esc(tm)}</td><td class='nt'>{esc(t)}</td>"
                  f"<td class='num'><span class='sm'>{esc(sect)}</span></td>"
                  f"<td class='num'><span class='imp {klass}'>{view}</span></td></tr>")

# 持仓卡（单循环，数据驱动点评）
# 2026-09-11 修复缺陷 #25：标签按组合内相对排名统一分配，"最弱/最强"各只出现一次。
_hl_pts = [(c, (HM.get(c) or {}).get("am_pct")) for c, _n, _c in HOLD]
_hl_pts = [(c, v) for c, v in _hl_pts if isinstance(v, (int, float))]
_HLAB = {}
if _hl_pts:
    _hl_pts.sort(key=lambda x: x[1])
    if _hl_pts[0][1] < 0: _HLAB[_hl_pts[0][0]] = "上午组合内最弱"
    if len(_hl_pts) > 1 and _hl_pts[-1][1] > 0: _HLAB[_hl_pts[-1][0]] = "上午组合内最强"
    for _c, _v in _hl_pts:
        if _c in _HLAB: continue
        _HLAB[_c] = "上午走强持仓" if _v > 1.5 else ("上午走弱持仓" if _v < -1.5 else "上午震荡持仓")
hold_cards = ""
for code, name, cost in HOLD:
    m = HM.get(code)
    if not m: continue
    fl = ff.get(code, {})
    sec = (HOLD_CTX.get(code) or {}).get("sector")
    secr = find_ind(sec) if sec else None
    now, nowp = m["now"], m["now_pct"]
    if cost:
        pl = (now - cost) / cost * 100
        pl_html = (f"<div class='kv'><span>持仓成本</span><b>{cost:.2f}</b></div>"
                   f"<div class='kv'><span>浮动盈亏</span><b class='{cls(pl)}'>{sgn(pl)}%</b></div>")
        pl_badge = f"<span class='pl {cls(pl)}'>{'浮盈' if pl > 0 else ('浮亏' if pl < 0 else '持平')} {sgn(pl)}%</span>"
    else:
        pl_html = ("<div class='kv'><span>持仓成本</span><b class='na'>未记录</b></div>"
                   "<div class='kv'><span>浮动盈亏</span><b class='na'>无法计算</b></div>")
        pl_badge = "<span class='pl na'>成本未记录</span>"
    sigs = tech_signal(code, m)
    sig_html = "".join(f"<div class='ma'><span>{l}</span><b>{v:.3f}</b><i class='{cls(d)}'>{sgn(d)}%</i></div>" for l, v, d in sigs)
    fl_html = ""
    if fl:
        fl_html = (f"<div class='kv'><span>主力净额</span><b class='{cls(fl['main'])}'>{fl['main']/1e4:+.0f}万</b></div>"
                   f"<div class='kv'><span>超大单</span><b class='{cls(fl['huge'])}'>{fl['huge']/1e4:+.0f}万</b></div>")
    sec_html = ""
    if secr:
        sec_html = (f"<div class='kv'><span>所属板块</span><b>{esc(secr['name'])} "
                    f"<span class='{cls(secr['pct'])}'>{secr['pct']:+.2f}%</span></b></div>")
    hold_cards += f"""<div class="hold">
<div class="hold-h">
  <div><span class="hold-n">{name}</span><span class="code">{code[-6:]}</span></div>
  <div class="hold-r">{pl_badge}</div>
</div>
<div class="hold-price">
  <div class="hp-now"><span>现价 <i class="tny">11:30+</i></span><b class="{cls(nowp)}">{now:.3f}</b><i class="{cls(nowp)}">{sgn(nowp)}%</i></div>
  <div class="hp-am"><span>上午收 <i class="tny">11:30</i></span><b class="{cls(m['am_pct'])}">{m['am_close']:.3f}</b><i class="{cls(m['am_pct'])}">{sgn(m['am_pct'])}%</i></div>
</div>
{spark(code, w=300, h=80, label=name)}
<div class="hold-grid">
{pl_html}
<div class="kv"><span>今日开盘</span><b class="{cls(m['open_pct'])}">{m['open']:.3f} ({sgn(m['open_pct'])}%)</b></div>
<div class="kv"><span>上午高/低</span><b>{m['high']:.3f} / {m['low']:.3f}</b></div>
<div class="kv"><span>振幅</span><b>{m['amp']:.2f}%</b></div>
<div class="kv"><span>自高点回落</span><b class="{'down' if m['retrace']>0 else 'flat'}">{m['retrace']:.2f}%</b></div>
{sec_html}{fl_html}
</div>
<div class="ma-box"><div class="ma-t">均线位置（以上午收盘价对比）</div><div class="ma-row">{sig_html}</div></div>
<div class="hold-note">{build_hold_note(code, name, cost, m, fl, secr, _HLAB.get(code))}</div>
</div>"""

if not HOLD:
    hold_cards = ("<div class='hold-empty' style='padding:18px 20px;color:#94a3b8;"
                  "background:#1e293b;border:1px dashed #334155;border-radius:10px;line-height:1.7'>"
                  "未配置持仓（持仓为个人私有配置，不随 Skill 分发）。"
                  "如需持仓诊断，请在运行目录放置 <code>holdings.json</code>（参考 <code>holdings.example.json</code>），"
                  "或运行脚本时加 <code>--holdings 路径</code>。</div>")
# 涨跌家数（叙事/点评函数依赖，提前计算）
# 广度分母修复：上市总数 listed_total 可能含停牌/无报价项，上涨占比按有效样本 valid_total 计。
BR_up = BR.get("up", 0) if BR else 0
BR_dn = BR.get("down", 0) if BR else 0
BR_fl = BR.get("flat", 0) if BR else 0
BR_listed = (BR.get("listed_total") or BR.get("total", 0)) if BR else 0
BR_valid = (BR.get("valid_total") or (BR_up + BR_dn + BR_fl)) if BR else 0
BR_missing = (BR.get("missing", 0)) if BR else 0
BR_tot = BR_valid  # 占比分母统一用有效样本
up_ratio = BR_up / BR_valid * 100 if BR_valid else 0

# 量价关系提示（依赖 up_ratio、FACTS、_VREG——FACTS 在下方 1017 行才定义，
# 故赋值点后移至量能档措辞之后；2026-09-09 #23：旧位置(1010)先于 FACTS 导致无法引用量能档）

MCP_NOTE = (args.mcp_note.strip() if args.mcp_note else
            "本报告数据<b>全部来自上述公开行情接口</b>（腾讯财经 / 东方财富），"
            "未使用 westock-mcp / tdx-connector（当前环境未连接或未注册，脚本亦未调用任何 MCP）。")

# 事实层：在叙事函数调用前推导（供各 build_* 引用）
FACTS = compute_facts()

# 上证日内形态中文描述（供量能结论等处复用，避免硬编码「冲高回落」）
_PAT_CN = {"dip_then_rebound": "低开探底后回升", "rush_then_fall": "冲高回落",
           "open_high_fall": "开盘即高点后单边下行",
           "strong": "单边走强", "weak": "弱势震荡"}
PAT_CY = _PAT_CN.get((FACTS.get("index_pattern") or {}).get("value"), "震荡")

# 量能档措辞（模块 04 / 08 共用，按 volume_regime 条件化；2026-09-08 修复：
# 旧模板无条件写"上午量能偏弱""基本持平至小幅缩量"，在 flat 档(100-115%)上沿 115% 处措辞过保守）
_VREG = (FACTS.get("volume_regime") or {}).get("value", "flat")
_VOL_LAB = {"shrunk": "上午量能偏弱（缩量）", "flat": "上午量能基本持平",
            "expanded": "上午量能温和放大"}.get(_VREG, "上午量能基本持平")
_VOL_TAIL = {"shrunk": "即较前 5 日全日均量缩量，主线行情缺乏量能配合",
             "flat": "即与前期基本持平、接近前 5 日均量，尚未出现主线行情所需的明显放量",
             "expanded": "即高于前 5 日全日均量，主线行情具备一定量能基础"}.get(_VREG, "")
_VOL_RISK = {"shrunk": "量能偏弱（缩量）。", "flat": "量能未明显放大。",
             "expanded": "量能温和放大。"}.get(_VREG, "量能未明显放大。")

# 量价关系提示：FACTS 与量能档措辞均已就绪后再计算（2026-09-09 #23 从 1010 行后移至此）
DIVERG_NOTE = build_divergence_note()

# 叙事 / 点评（可被 --narrative 覆盖）
OV = {}
if args.narrative and os.path.exists(args.narrative):
    try:
        OV = json.load(open(args.narrative, encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 叙事覆盖文件读取失败：{e}")
narr_s01 = OV.get("s01") or build_s01_note()
narr_s02 = OV.get("s02") or build_s02()
narr_s03 = OV.get("s03") or build_s03_note()
narr_s05 = OV.get("s05") or build_s05_note()
narr_s06 = OV.get("s06") or build_s06_note()
narr_s07 = OV.get("s07") or build_s07_note()
risk_html, opp_html = build_s08()
risk_html = OV.get("s08_risk") or risk_html
opp_html = OV.get("s08_opp") or opp_html

# 涨跌家数已在前文计算（供叙事/点评函数使用）
gen_time = (meta.get("gen_time") or meta.get("collected_at")
            or _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
# 运行模式 → 时段标签（时间一致性核心）
MODE = meta.get("mode") or "strict-midday"
_PERIOD = {"strict-midday": "上午", "late-snapshot": "当前快照", "render-archive": "归档(11:30)"}
PERIOD = _PERIOD.get(MODE, "上午")
try:
    dobj = _dt.datetime.strptime(DATE, "%Y-%m-%d")
    _wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    date_cn = f"{dobj.year}年{dobj.month}月{dobj.day}日（{_wd[dobj.weekday()]}）"
except Exception:
    date_cn = DATE
reopened = IM.get("sh000001") and abs(IM["sh000001"]["now"] - IM["sh000001"]["am_close"]) > 0.02

if not HOLD:
    cost_str = "（未配置持仓，跳过；持仓为个人私有配置，不随 Skill 分发）"
else:
    cost_str = " / ".join(f"{n}:{c}" for _, n, c in HOLD if c)
    if any(c is None for _, _, c in HOLD):
        cost_str += "；" + " / ".join(f"{n}:未记录" for _, n, c in HOLD if not c)

# 2026-09-14 修复缺陷 #32：操作总纲②的关键位原写死「科创50 {昨收}（破位则成长股离场）」，
# 但当日科创50 上午收 1538.84 已低于昨收 1553.39 达 0.94%——"破位"条件其实早已触发，
# 仍按"待破位"表述会误导（读者以为还有空间）。关键位必须校验当前是否已在位下。
_kc0 = IM.get("sh000688") or {}
if _kc0 and _kc0.get("am_close", 0) >= _kc0.get("prev", 0):
    KC_KEY = f"科创50 <b>{_kc0['prev']:.2f}</b>（昨收，破位则成长股离场）"
elif _kc0:
    KC_KEY = (f"科创50 <b>{_kc0['low']:.2f}</b>（上午低点；现价 {_kc0['am_close']:.2f} 已低于昨收 "
              f"{_kc0['prev']:.2f}，再破上午低点则成长股走弱信号强化）")
else:
    KC_KEY = "科创50（数据缺失）"

OUT_HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A股午间复盘 {DATE}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.6;padding:14px;font-size:14px}}
.container{{max-width:1180px;margin:0 auto}}
.up{{color:{UP}}} .down{{color:{DN}}} .flat{{color:{FLAT}}} .na{{color:#64748b;font-weight:400}}
header{{background:linear-gradient(135deg,#1e293b,#263348);border-radius:12px;padding:20px 22px;margin-bottom:16px;
border:1px solid #334155}}
h1{{font-size:22px;font-weight:700;letter-spacing:.5px}}
.hsub{{color:#94a3b8;font-size:12px;margin-top:6px}}
.hi-badges{{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}}
.hb{{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:7px 11px;font-size:12px}}
.hb b{{font-size:14px;margin-left:4px}}
.section{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px;margin-bottom:16px}}
.section-title{{font-size:16px;font-weight:700;margin-bottom:14px;padding-left:11px;border-left:4px solid #3b82f6;
display:flex;align-items:center;gap:8px}}
.st-n{{background:#3b82f6;color:#fff;font-size:11px;padding:1px 7px;border-radius:20px;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#263348;color:#94a3b8;font-weight:600;padding:8px 6px;text-align:right;font-size:12px;white-space:nowrap}}
th:first-child,td.nm,td.nt{{text-align:left}}
td{{padding:8px 6px;border-bottom:1px solid #2b3a52;text-align:right;white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
td.nm{{font-weight:600}} .num{{font-variant-numeric:tabular-nums}}
.sub{{font-size:11px;margin-left:4px;opacity:.85}}
.code{{color:#64748b;font-size:11px;margin-left:5px;font-weight:400}}
.tbl-wrap{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.grid-mini{{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:11px}}
.mini{{background:#263348;border:1px solid #334155;border-radius:10px;padding:10px}}
.mini-h{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px}}
.mini-n{{font-weight:600;font-size:13px}} .mini-p{{font-weight:700;font-size:14px}}
.mini-f{{color:#94a3b8;font-size:10.5px;margin-top:3px;line-height:1.45}}
.spark{{width:100%;height:auto;display:block}}
.barsvg{{width:100%;height:auto;display:block;margin-top:6px}}
.grid-sec{{display:grid;grid-template-columns:repeat(auto-fill,minmax(255px,1fr));gap:11px;margin-bottom:8px}}
.sec{{background:#263348;border-radius:10px;padding:12px;border-left:4px solid #334155}}
.sec-up{{border-left-color:{UP}}} .sec-dn{{border-left-color:{DN}}}
.sec-h{{display:flex;align-items:center;gap:7px;margin-bottom:9px;flex-wrap:wrap}}
.rk{{background:#334155;color:#cbd5e1;width:19px;height:19px;border-radius:5px;font-size:11px;
display:inline-flex;align-items:center;justify-content:center;font-weight:700;flex:none}}
.sec-n{{font-weight:700;font-size:14px}} .sec-p{{margin-left:auto;font-weight:700;font-size:15px}}
.kv{{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;gap:10px}}
.kv span{{color:#94a3b8;flex:none}} .kv b{{text-align:right;font-weight:600}}
.tag{{display:inline-block;font-size:11px;padding:2px 9px;border-radius:20px;margin:8px 0 5px;font-weight:600}}
.s-high{{background:rgba(239,68,68,.16);color:#fca5a5;border:1px solid rgba(239,68,68,.4)}}
.s-mid{{background:rgba(251,146,60,.14);color:#fdba74;border:1px solid rgba(251,146,60,.35)}}
.s-obs{{background:rgba(148,163,184,.14);color:#cbd5e1;border:1px solid rgba(148,163,184,.3)}}
.s-low{{background:rgba(34,197,94,.14);color:#86efac;border:1px solid rgba(34,197,94,.35)}}
.sec-note{{color:#94a3b8;font-size:11px;line-height:1.5}}
.grid-2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
.sub-t{{font-size:13px;font-weight:600;color:#cbd5e1;margin:14px 0 8px;display:flex;align-items:center;gap:7px}}
.sub-t:first-child{{margin-top:0}}
.dot{{width:7px;height:7px;border-radius:50%;flex:none}}
.d-up{{background:{UP}}} .d-dn{{background:{DN}}}
.imp{{font-size:11px;padding:2px 8px;border-radius:5px;font-weight:600;white-space:nowrap}}
.imp-pos{{background:rgba(239,68,68,.16);color:#fca5a5}}
.imp-neg{{background:rgba(34,197,94,.16);color:#86efac}}
.imp-neu{{background:rgba(148,163,184,.14);color:#cbd5e1}}
.sm{{font-size:11px;color:#94a3b8}} td.tm{{color:#64748b;font-size:11.5px}}
td.nt{{white-space:normal;line-height:1.45;min-width:210px}}
.grid-hold{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}}
.hold{{background:#263348;border:1px solid #334155;border-radius:11px;padding:14px}}
.hold-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;gap:8px;flex-wrap:wrap}}
.hold-n{{font-size:16px;font-weight:700}}
.pl{{font-size:12px;font-weight:700;padding:3px 10px;border-radius:20px;background:#0f172a;border:1px solid #334155}}
.hold-price{{display:flex;gap:10px;margin-bottom:9px}}
.hp-now,.hp-am{{flex:1;background:#0f172a;border-radius:8px;padding:8px 10px;border:1px solid #2b3a52}}
.hp-now span,.hp-am span{{display:block;color:#94a3b8;font-size:11px;margin-bottom:1px}}
.hp-now b,.hp-am b{{font-size:19px;font-weight:700}}
.hp-now i,.hp-am i{{font-style:normal;font-size:12px;margin-left:5px;font-weight:600}}
.tny{{font-style:normal;font-size:9.5px;color:#64748b}}
.hold-grid{{display:grid;grid-template-columns:1fr 1fr;gap:2px 14px;margin:9px 0}}
.ma-box{{background:#0f172a;border-radius:8px;padding:9px 10px;margin:9px 0;border:1px solid #2b3a52}}
.ma-t{{color:#94a3b8;font-size:11px;margin-bottom:6px}}
.ma-row{{display:flex;gap:7px;flex-wrap:wrap}}
.ma{{background:#1e293b;border-radius:6px;padding:5px 9px;font-size:11px;flex:1;min-width:66px;text-align:center}}
.ma span{{display:block;color:#64748b;font-size:10px}}
.ma b{{display:block;font-size:12px;margin:1px 0}}
.ma i{{font-style:normal;font-size:10.5px;font-weight:600}}
.hold-note{{background:#0f172a;border-left:3px solid #3b82f6;border-radius:0 7px 7px 0;padding:10px 12px;
font-size:12px;line-height:1.72;color:#cbd5e1}}
.hold-note b{{color:#e2e8f0}}
.pv{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:13px}}
.pv-box{{border-radius:10px;padding:14px;border:1px solid #334155}}
.pv-risk{{background:rgba(34,197,94,.055);border-color:rgba(34,197,94,.28)}}
.pv-op{{background:rgba(239,68,68,.055);border-color:rgba(239,68,68,.28)}}
.pv-h{{font-size:14px;font-weight:700;margin-bottom:10px;display:flex;align-items:center;gap:7px}}
.pv-box ol{{padding-left:19px}} .pv-box li{{font-size:12.5px;line-height:1.68;margin-bottom:9px}}
.pv-box li b{{color:#f1f5f9}}
.lvl{{display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;margin-right:5px;font-weight:700;vertical-align:1px}}
.l-h{{background:rgba(239,68,68,.22);color:#fca5a5}} .l-m{{background:rgba(251,146,60,.2);color:#fdba74}}
.l-l{{background:rgba(148,163,184,.18);color:#cbd5e1}}
.vol-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:11px;margin-bottom:12px}}
.vol{{background:#263348;border-radius:9px;padding:12px;text-align:center;border:1px solid #334155}}
.vol-l{{color:#94a3b8;font-size:11px;margin-bottom:4px}}
.vol-v{{font-size:20px;font-weight:700}} .vol-s{{font-size:10.5px;color:#64748b;margin-top:3px;line-height:1.4}}
.note{{background:#0f172a;border:1px dashed #334155;border-radius:9px;padding:11px 13px;font-size:12px;
color:#94a3b8;line-height:1.7;margin-top:11px}}
.note b{{color:#cbd5e1}}
.src{{background:#1e293b;border:1px solid #334155;border-radius:11px;padding:16px;font-size:11.5px;color:#94a3b8;line-height:1.85}}
.src b{{color:#cbd5e1}}
.src-tag{{display:inline-block;background:#263348;border:1px solid #3b4a63;border-radius:5px;
padding:1px 8px;margin:2px 4px 2px 0;font-size:11px;color:#cbd5e1}}
.warn{{background:rgba(251,146,60,.09);border:1px solid rgba(251,146,60,.32);border-radius:9px;
padding:11px 13px;font-size:12px;color:#fdba74;line-height:1.65;margin-bottom:13px}}
.narr{{counter-reset:s}}
.nr{{display:flex;gap:12px;padding:12px 0;border-bottom:1px solid #2b3a52}}
.nr:last-child{{border-bottom:none}}
.nr-t{{flex:none;width:88px;font-weight:700;font-size:12.5px;color:#60a5fa;font-variant-numeric:tabular-nums}}
.nr-b{{flex:1;font-size:12.5px;line-height:1.7}}
.nr-b b{{color:#f1f5f9}}
.chip{{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:4px;background:#334155;
color:#cbd5e1;margin-right:5px;font-weight:600}}
@media(max-width:640px){{
body{{padding:9px;font-size:13px}} header{{padding:15px}} h1{{font-size:18px}}
.section{{padding:13px}} .hold-grid{{grid-template-columns:1fr}}
.grid-mini{{grid-template-columns:1fr 1fr;gap:8px}} .mini{{padding:8px}}
.nr{{flex-direction:column;gap:3px}} .nr-t{{width:auto}}
.hp-now b,.hp-am b{{font-size:16px}}
}}
</style></head><body><div class="container">

<header>
<h1>A股午间复盘 · {date_cn}</h1>
<div class="hsub">A股{PERIOD}复盘 · 模式 {MODE} · 报告生成 {gen_time}</div>
<div class="hi-badges">
<span class="hb">上证 <b class="{cls(IM['sh000001']['am_pct'])}">{IM['sh000001']['am_close']:.2f} {sgn(IM['sh000001']['am_pct'])}%</b></span>
<span class="hb">创业板 <b class="{cls(IM['sz399006']['am_pct'])}">{sgn(IM['sz399006']['am_pct'])}%</b></span>
<span class="hb">科创50 <b class="{cls(IM['sh000688']['am_pct'])}">{sgn(IM['sh000688']['am_pct'])}%</b></span>
<span class="hb">两市成交 <b>{am_amt_tot:,.0f}亿</b></span>
<span class="hb">涨跌 <b><span class="up">{BR_up}</span> / <span class="down">{BR_dn}</span></b></span>
<span class="hb">涨停 <b class="up">{len(zt)}</b> / 跌停 <b class="down">{len(dt)}</b></span>
</div>
</header>

<div class="warn">
<b>⚠ 数据口径说明（模式 {MODE}）：</b>
{'本报告以 <b>11:30 上午收盘</b>为统一基准——指数、板块、广度、资讯、涨跌停均取自上午口径，可相互比对。'
 if MODE == 'strict-midday' else
 '指数「上午收」列由分时精确还原至 11:30；但<b>板块 / 广度 / 资金流 / 涨跌停 / 资讯均为「当前快照」口径</b>（采集于 '
 + gen_time[-8:-3] + '），并非上午数据，请勿与午间基准混读。'}
{('注意：任务执行于午后（' + gen_time[-8:-3] + '），「现价」列反映最新状态，与「上午收」不可混读。') if reopened else ''}
数据提供方与缺失项见文末「数据来源」。本报告为决策参考，非投资建议。
</div>

<!-- 1. 指数行情 -->
<div class="section">
<div class="section-title"><span class="st-n">01</span>上午主要指数行情</div>
<div class="tbl-wrap"><table>
<thead><tr><th>指数</th><th>上午收盘</th><th>涨跌幅</th><th>开盘</th><th>上午最高</th><th>上午最低</th><th>振幅</th><th>现价</th></tr></thead>
<tbody>{idx_rows}</tbody></table></div>
<div class="note">{narr_s01}</div>
<div class="sub-t" style="margin-top:15px"><span class="dot d-up"></span>各指数分时形态（虚线为昨收基准）</div>
<div class="grid-mini">{idx_cards}</div>
</div>

<!-- 2. 走势叙事 -->
<div class="section">
<div class="section-title"><span class="st-n">02</span>上午走势叙事与阶段驱动</div>
<div class="narr">{narr_s02}</div>
</div>

<!-- 3. 板块 -->
<div class="section">
<div class="section-title"><span class="st-n">03</span>领涨 / 领跌板块 TOP5 与盘中强度评分</div>
<div class="sub-t"><span class="dot d-up"></span>领涨板块 TOP5（东财行业板块，共 {len(ind_v)} 个）</div>
<div class="grid-sec">{top_cards}</div>
<div class="sub-t"><span class="dot d-dn"></span>领跌板块 TOP5</div>
<div class="grid-sec">{bot_cards}</div>
<div class="note">{narr_s03}</div>
<div class="sub-t" style="margin-top:16px"><span class="dot d-up"></span>行业板块涨幅榜 TOP10（条形长度=涨幅，右侧=主力净额）</div>
{barchart(ind_v[:10])}
<div class="sub-t"><span class="dot d-dn"></span>行业板块跌幅榜 TOP10</div>
{barchart(ind_v[-10:][::-1])}
<div class="grid-2" style="margin-top:16px">
<div><div class="sub-t"><span class="dot d-up"></span>概念板块领涨 TOP8</div>
<div class="tbl-wrap"><table><thead><tr><th>概念</th><th>涨幅</th><th>主力净额</th><th>龙头</th></tr></thead>
<tbody>{con_rows(con_v,8)}</tbody></table></div></div>
<div><div class="sub-t"><span class="dot d-dn"></span>概念板块领跌 TOP8</div>
<div class="tbl-wrap"><table><thead><tr><th>概念</th><th>涨幅</th><th>主力净额</th><th>龙头</th></tr></thead>
<tbody>{con_rows(con_v[-8:][::-1],8)}</tbody></table></div></div>
</div>

<!-- 4. 量能 -->
<div class="section">
<div class="section-title"><span class="st-n">04</span>成交额与量能评估</div>
<div class="vol-grid">
<div class="vol"><div class="vol-l">两市上午成交额</div><div class="vol-v">{am_amt_tot:,.0f}<span style="font-size:12px"> 亿</span></div>
<div class="vol-s">沪市 {am_amt_sh:,.0f}亿 + 深市 {am_amt_sz:,.0f}亿</div></div>
<div class="vol"><div class="vol-l">沪市上午成交量</div><div class="vol-v">{VS['am_vol']/1e4:,.0f}<span style="font-size:12px"> 万手</span></div>
<div class="vol-s">占前5日全日均量 <b class="{'down' if VS['ratio']<58 else 'up'}">{VS['ratio']:.0f}%</b></div></div>
<div class="vol"><div class="vol-l">沪市前5日全日均量</div><div class="vol-v">{VS['avg5_full']/1e4:,.0f}<span style="font-size:12px"> 万手</span></div>
<div class="vol-s">{VS['dates'][0]} ~ {VS['dates'][-1]}</div></div>
<div class="vol"><div class="vol-l">按上午占比推算全日</div><div class="vol-v {'down' if VS['proj_vs_avg5']<100 else 'up'}">{VS['proj_vs_avg5']:.0f}%</div>
<div class="vol-s">对比前5日均量（按上午占全日 56.5% 推算）</div></div>
</div>
<div class="tbl-wrap"><table>
<thead><tr><th>市场</th><th>上午成交额</th><th>上午成交量</th><th>前5日全日均量</th><th>上午/前5日均量</th><th>推算全日/均量</th></tr></thead>
<tbody>
<tr><td class="nm">沪市（上证）</td><td class="num">{am_amt_sh:,.0f}亿</td><td class="num">{VS['am_vol']/1e4:,.0f}万手</td>
<td class="num">{VS['avg5_full']/1e4:,.0f}万手</td><td class="num {'down' if VS['ratio']<58 else 'up'}">{VS['ratio']:.1f}%</td>
<td class="num {'down' if VS['proj_vs_avg5']<100 else 'up'}">{VS['proj_vs_avg5']:.0f}%</td></tr>
<tr><td class="nm">深市（深成指）</td><td class="num">{am_amt_sz:,.0f}亿</td><td class="num">{VZ['am_vol']/1e4:,.0f}万手</td>
<td class="num">{VZ['avg5_full']/1e4:,.0f}万手</td><td class="num {'down' if VZ['ratio']<58 else 'up'}">{VZ['ratio']:.1f}%</td>
<td class="num {'down' if VZ['proj_vs_avg5']<100 else 'up'}">{VZ['proj_vs_avg5']:.0f}%</td></tr>
</tbody></table></div>
<div class="note">
<b>量能结论：{_VOL_LAB}（推算全日约前5日均量 {VS['proj_vs_avg5']:.0f}%），与指数{PAT_CY}同时出现。</b>两市上午合计成交 <b>{am_amt_tot:,.0f} 亿元</b>。
以成交量口径衡量（数据可精确比对）：沪市上午 {VS['am_vol']/1e4:,.0f} 万手，
仅相当于前 5 个交易日<b>全日</b>均量（{VS['avg5_full']/1e4:,.0f} 万手）的 <b>{VS['ratio']:.1f}%</b>；
深市为 <b>{VZ['ratio']:.1f}%</b>。A股上午成交通常占全日约 55%–58%，
据此推算全日量能约为前 5 日均量的 <b>{VS['proj_vs_avg5']:.0f}%（沪）/ {VZ['proj_vs_avg5']:.0f}%（深）</b>，
{_VOL_TAIL}。<br>
{DIVERG_NOTE}
<span style="color:#64748b">口径说明：上午成交额取自分时数据 11:30 累计值（精确）；成交量对比采用日K成交量（精确）。
因公开接口未提供历史分时成交额，故未做"上午 vs 历史同期上午"的成交额直接对比，改以成交量占比推算，结论方向一致。</span>
</div>
</div>

<!-- 5. 资讯 -->
<div class="section">
<div class="section-title"><span class="st-n">05</span>{PERIOD}重要资讯与市场影响</div>
<div class="tbl-wrap"><table>
<thead><tr><th>时间</th><th>资讯标题</th><th>影响板块</th><th>方向</th></tr></thead>
<tbody>{news_rows}</tbody></table></div>
<div class="note">{narr_s05}
<br><span style="color:#64748b">说明：上表为按关键词从财经要闻流筛选的上午时段资讯；"方向"为基于板块归属的定性判断，非投资建议。</span>
</div>
</div>

<!-- 6. 涨停 -->
<div class="section">
<div class="section-title"><span class="st-n">06</span>连板 / 涨停情况速览</div>
<div class="vol-grid">
<div class="vol"><div class="vol-l">涨停家数</div><div class="vol-v up">{len(zt)}</div><div class="vol-s">含 ST 与 20cm</div></div>
<div class="vol"><div class="vol-l">跌停家数</div><div class="vol-v down">{len(dt)}</div><div class="vol-s">涨停/跌停比 {len(zt)/max(len(dt),1):.1f}</div></div>
<div class="vol"><div class="vol-l">连板家数（≥2板）</div><div class="vol-v up">{len(multi)}</div><div class="vol-s">最高板 {max(streaks) if streaks else 0} 板</div></div>
<div class="vol"><div class="vol-l">上涨 / 下跌</div><div class="vol-v"><span class="up">{BR_up}</span><span style="color:#64748b">/</span><span class="down">{BR_dn}</span></div>
<div class="vol-s">上涨占比 {up_ratio:.1f}%（平盘 {BR_fl}）</div></div>
</div>
<div class="sub-t"><span class="dot d-up"></span>连板梯队（≥2 板）</div>
<div class="tbl-wrap"><table>
<thead><tr><th>个股</th><th>连板</th><th>首封时间</th><th>开板次数</th><th>所属行业</th></tr></thead>
<tbody>{zt_rows}</tbody></table></div>
<div class="note">{narr_s06}</div>
</div>

<!-- 7. 持仓 -->
<div class="section">
<div class="section-title"><span class="st-n">07</span>持仓诊断</div>
<div class="grid-hold">{hold_cards}</div>
<div class="note">{narr_s07}</div>
</div>

<!-- 8. 下午推演 -->
<div class="section">
<div class="section-title"><span class="st-n">08</span>下午推演：风险点与机会点</div>
<div class="pv">
<div class="pv-box pv-risk">
<div class="pv-h"><span class="dot d-dn"></span>风险点</div>
<ol>{risk_html}</ol>
</div>
<div class="pv-box pv-op">
<div class="pv-h"><span class="dot d-up"></span>机会点</div>
<ol>{opp_html}</ol>
</div>
</div>
<div class="note">
<b>下午操作总纲：</b>
① <b>{'做主线、不做补涨' if up_ratio < 45 else '轻指数、重个股'}</b>——{'资金与宽度双验证的方向胜率最高，其余在 ' + format(up_ratio, '.0f') + '% 上涨占比环境下追高胜率低' if up_ratio < 45 else '上涨占比 ' + format(up_ratio, '.0f') + '% 显示个股层面赚钱效应尚可，可侧重个股alpha，但仍需回避无资金验证的纯题材补涨'}；
② <b>盯关键位</b>——上证 <b>{IM['sh000001']['low']:.2f}</b>（上午低点，破位则降低总仓位）、{KC_KEY}；
③ <b>持仓分级处理</b>——顺主线持有、主力流出标的逢反弹减仓、弱势板块不加仓等资金转向。
<br><span style="color:#64748b">本报告为数据复盘与逻辑推演，不构成投资建议；所有价位均为技术参考，实际操作请结合自身风险承受能力。</span>
</div>
</div>

<!-- 数据来源 -->
<div class="src">
<b>数据来源与采集说明</b><br>
<span class="src-tag">腾讯财经 qt.gtimg.cn</span> 7 大指数与持仓个股实时快照（现价/昨收/开盘/最高最低/成交额）<br>
<span class="src-tag">腾讯财经 web.ifzq.gtimg.cn</span> 分时数据（<b>用于精确还原 11:30 上午收盘价、日内高低点时刻、上午累计成交额</b>）、
日K线（MA5/10/20/60 与前 5 日成交量对比）<br>
<span class="src-tag">东方财富 push2 / push2delay</span> 行业板块（{len(ind_v)} 个）与概念板块（{len(con_v)} 个）涨跌幅及主力净流入、
个股分钟级资金流（主力/超大单/大单/中单/小单）、全市场涨跌家数<br>
<span class="src-tag">东方财富 push2ex</span> 涨停池（{len(zt)} 只，含连板数/首封时间/开板次数）与跌停池（{len(dt)} 只）<br>
<span class="src-tag">东方财富 财经要闻流</span> 资讯 {len(news)} 条（{'09:00–11:30 上午时段' if MODE=='strict-midday' else '当日全时段'}筛选 {len(sel)} 条纳入报告）<br><br>
<b>数据提供方：</b>{MCP_NOTE}
运行模式 <b>{MODE}</b>；数据质量等级 <b>{(D.get('quality') or {}).get('level','未知')}</b>
（核心数据缺失会阻止报告生成，缺失项见各模块口径说明）。<br>
<b>采集技术要点（供复用）：</b>① 东财 push2 的 <code>fs</code> 参数空格必须编码为 <code>+</code>（用 <code>%20</code> 会返回空响应）；
② push2.eastmoney.com 的 IPv6 路由不通，<b>必须强制 IPv4</b>，否则 HTTP 000；③ 大 <code>pz</code> 值易被拒，分页 pz≤100 并配合
push2delay 备用域名兜底。<br>
<b>口径提示：</b>上午收盘 = 11:30 分时末值；涨跌家数：有效样本 {BR_valid} 只（上市 {BR_listed} 只，其中 {BR_missing} 只无报价/停牌未计入占比），上涨占比按有效样本计算；
持仓成本取自本地历史执行记录（{cost_str}）。<br>
<b>生成时间：</b>{gen_time} · 配色遵循中国股市惯例（<span class="up">涨红</span> / <span class="down">跌绿</span>）
</div>

</div></body></html>"""

os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
open(OUT, "w", encoding="utf-8").write(OUT_HTML)
print(f"[OK] 已生成 {OUT} ({len(OUT_HTML)/1024:.1f} KB)")
print(f"  上证 上午收 {IM['sh000001']['am_close']:.2f} ({sgn(IM['sh000001']['am_pct'])}%)")
print(f"  两市上午成交 {am_amt_tot:,.0f} 亿")
print(f"  涨跌家数 {BR_up}/{BR_dn} (上涨占比 {up_ratio:.1f}%)")
print(f"  涨停 {len(zt)} 跌停 {len(dt)} 最高板 {max(streaks) if streaks else 0}")
