# -*- coding: utf-8 -*-
"""午间复盘数据采集（固化版，date-agnostic）。

数据源（westock-mcp / tdx-connector 不可用时启用公开行情 API 降级）：
  腾讯财经 qt.gtimg.cn / web.ifzq.gtimg.cn + 东方财富 push2 / push2delay / push2ex
产出：
  midday_merged_{date}.json   合并后的行情（供 generate_midday_review.py 使用）
  breadth_{date}.json         精确涨跌家数

踩坑固化（务必保留）：
  ① 东财 push2 的 IPv6 路由不通 → 强制 IPv4（socket.getaddrinfo 覆写 AF_INET）
  ② fs 参数空格必须编码为 '+'（%20 会被拒返回空响应）
  ③ 大 pz 易被拒 → 分页 pz<=100 + push2delay 备用域名兜底
  ④ 涨跌家数用"全A逐页精确计数"，行业板块成分股跨层级重复计数不可靠

用法：
  python collect_midday_data.py --date 2026-08-21
  python collect_midday_data.py --selftest            # 离线自检落盘契约，不联网
  python collect_midday_data.py --selftest-fail       # 失败档落盘契约自检
"""
import json, re, ssl, socket, os, sys, urllib.request, urllib.parse, time, argparse
from datetime import datetime

# Windows 默认控制台编码为 GBK，打印中文易抛 UnicodeEncodeError。
# 强制 stdout/stderr 用 UTF-8（Python 3.7+，3.13 必支持）。
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 安全：恢复 TLS 证书校验（金融数据不建议关闭证书验证）。
# 仅保留 IPv4 强制 + 备用域名降级（见 get()）。

# ---- 强制 IPv4（关键修复）----
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_only

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# ---- 与 generate_midday_review.py 保持一致的标的配置 ----
INDEX_MAP = [("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"),
             ("sh000688", "科创50"), ("sh000016", "上证50"), ("sh000905", "中证500"),
             ("sh000852", "中证1000")]
# 持仓为【个人私有配置】，不随 Skill 分发（避免把真实持仓固化进可分享模板）。
# 通过 --holdings 指向 holdings.json（见同目录 holdings.example.json），
# 或在运行目录 / 脚本目录下放置 holdings.json 自动加载。
HOLD = []   # (代码, 名称, 成本) 由 main() 从 holdings.json 加载

def _default_holdings():
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(os.getcwd(), "holdings.json"),
              os.path.join(here, "holdings.json")):
        if os.path.exists(c):
            return c
    return None

def load_holdings(path):
    if not path or not os.path.exists(path):
        return []
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] 持仓配置读取失败：{e}")
        return []
    return [tuple(x) for x in cfg.get("holdings", [])]

def _secid(code):
    # 东财资金流 secid：深圳 0.xxxxxx / 上海 1.xxxxxx
    mkt = "0" if code.startswith("sz") else "1"
    return f"{mkt}.{code[2:]}", code


def get(url, ref=None, retries=4, decode="utf-8"):
    # 自动在 push2 / push2delay 两个 host 间兜底
    hosts = ["push2.eastmoney.com", "push2delay.eastmoney.com"] if "push2.eastmoney.com" in url else [None]
    for attempt in range(retries):
        for host in hosts:
            u = url.replace("push2.eastmoney.com", host, 1) if host else url
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA,
                                                         "Referer": ref or "https://finance.qq.com"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw = r.read()
                # 编码自适应：严格 utf-8 优先；失败或出现 U+FFFD 时退回 GBK。
                # 背景：腾讯接口返回 GBK，若按 utf-8 + errors="replace" 解码不会抛异常，
                # 只会静默把中文替换成 U+FFFD（不可恢复），曾导致证券名称全乱码。
                if decode == "gbk":
                    return raw.decode("gbk", errors="replace")
                try:
                    txt = raw.decode("utf-8")  # 严格模式，失败说明不是 UTF-8
                except UnicodeDecodeError:
                    return raw.decode("gbk", errors="replace")
                if "�" in txt:  # UTF-8 合法但含替换字符 → 多半是 GBK 被误判
                    alt = raw.decode("gbk", errors="replace")
                    if alt.count("�") < txt.count("�"):
                        return alt
                return txt
            except Exception as e:
                if attempt == retries - 1 and host == hosts[-1]:
                    print(f"[FAIL] {u[:90]} -> {e}")
                    return None
                time.sleep(1.0)
    return None


# ============ 1. 指数/持仓快照（腾讯） ============
def parse_tencent(txt):
    res = {}
    for m in re.finditer(r'v_([a-z]{2}\d{6})="([^"]*)"', txt or ""):
        code, body = m.group(1), m.group(2)
        f = body.split("~")
        if len(f) < 40:
            continue
        def fl(i):
            try: return float(f[i])
            except Exception: return 0.0
        res[code] = {"code": code, "name": f[1], "price": fl(3), "prev_close": fl(4),
                     "open": fl(5), "volume_hand": fl(6), "chg": fl(31), "pct": fl(32),
                     "high": fl(33), "low": fl(34), "amount_wan": fl(37),
                     "turnover": fl(38), "amplitude": fl(43) if len(f) > 43 else 0.0,
                     "vol_ratio": fl(49) if len(f) > 49 else 0.0,
                     "pb": fl(46) if len(f) > 46 else 0.0, "time": f[30]}
    return res


def collect_snapshot():
    codes = ",".join([c for c, _ in INDEX_MAP] + [c for c, _, _ in HOLD])
    # 关键：腾讯 qt.gtimg.cn 返回 GBK，必须显式 decode="gbk"。
    # 用默认 utf-8 + errors="replace" 不会抛异常（静默替换成 U+FFFD），
    # 导致证券名称变成不可恢复的乱码（实测 2026-09-02 踩坑）。
    return parse_tencent(get(f"https://qt.gtimg.cn/q={codes}", decode="gbk"))


# ============ 2. 分时（腾讯，保留 0930-1130） ============
def get_minute(code):
    txt = get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}")
    if not txt:
        return None
    try:
        j = json.loads(txt)
        arr = j["data"][code]["data"]["data"]
    except Exception as e:
        print(f"[FAIL] minute {code} {e}")
        return None
    pts = []
    for line in arr:
        p = line.split()
        if len(p) < 3:
            continue
        try:
            pts.append({"t": p[0], "p": float(p[1]), "v": float(p[2]),
                        "amt": float(p[3]) if len(p) > 3 else 0.0})
        except Exception:
            pass
    return [x for x in pts if x["t"] <= "1130"]


# ============ 3. 东财板块 ============
def em_rows(url, ref):
    txt = get(url, ref=ref)
    if not txt:
        return []
    try:
        j = json.loads(txt)
        d = j.get("data") or {}
        diff = d.get("diff") or {}
        return list(diff.values()) if isinstance(diff, dict) else diff
    except Exception as e:
        print(f"[FAIL] em_rows {e}")
        return []

def collect_sector(t="2", page_size=100, max_pages=10):
    fs = ("m:90+t:%s" % t).replace(" ", "+")
    fs_q = urllib.parse.quote(fs, safe="+")
    raw = []
    for pn in range(1, max_pages + 1):
        url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz={page_size}&po=1&np=1&fltt=2&invt=2"
               f"&fid=f3&fs={fs_q}"
               f"&fields=f2,f3,f12,f14,f62,f104,f105,f128,f136,f140,f141,f207,f222")
        rows = em_rows(url, "https://quote.eastmoney.com/center/boardlist.html")
        if not rows:
            break
        raw.extend(rows)
        if len(rows) < page_size:
            break
        time.sleep(0.15)
    seen = {}
    for r in raw:
        seen[r.get("f12")] = r
    out = []
    for r in seen.values():
        out.append({"name": r.get("f14"), "code": r.get("f12"), "pct": r.get("f3"),
                    "netflow": r.get("f62"), "up": r.get("f104"), "down": r.get("f105"),
                    "lead": r.get("f128"), "lead_pct": r.get("f136"), "lead_code": r.get("f140")})
    return out


# ============ 4. 精确涨跌家数（全A逐页计数） ============
def collect_breadth():
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    fs_q = urllib.parse.quote(fs, safe="+")
    up = dn = fl = 0
    total = 0
    page = 1
    while page <= 80:
        url = (f"https://push2.eastmoney.com/api/qt/clist/get?pn={page}&pz=100&po=0&np=1&fltt=2&invt=2"
               f"&fid=f3&fs={fs_q}&fields=f2,f3,f12,f14")
        rows = em_rows(url, "https://quote.eastmoney.com/center/gridlist.html")
        if not rows:
            break
        for r in rows:
            p = r.get("f3")
            try:
                p = float(p)
            except Exception:
                continue
            if p > 0: up += 1
            elif p < 0: dn += 1
            else: fl += 1
        total += len(rows)
        if len(rows) < 100:
            break
        page += 1
        time.sleep(0.15)
    valid = up + dn + fl
    missing = total - valid  # 无报价/停牌/无法判定涨跌的标的，不计入有效样本
    return {"listed_total": total, "valid_total": valid, "missing": missing,
            "up": up, "down": dn, "flat": fl,
            "up_ratio_valid": (up / valid) if valid else 0.0,
            "method": "全A逐页精确计数(pz=100)，上涨占比按有效样本计算"}


# ============ 5. 涨停/跌停池 ============
def collect_zt(date_c):
    url = ("https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
           "&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt%3Aasc&date=" + date_c)
    txt = get(url, ref="https://quote.eastmoney.com/ztb/detail")
    if not txt:
        return []
    try:
        j = json.loads(txt)
        pool = (j.get("data") or {}).get("pool") or []
    except Exception as e:
        print(f"[FAIL] ztpool {e}")
        return []
    return [{"code": r.get("c"), "name": r.get("n"), "pct": r.get("zdp"),
             "price": r.get("p", 0) / 1000 if r.get("p") else 0,
             "first_time": str(r.get("fbt", "")).zfill(6), "open_times": r.get("zbc"),
             "streak": r.get("lbc"), "industry": r.get("hybk")} for r in pool]

def collect_dt(date_c):
    url = ("https://push2ex.eastmoney.com/getTopicDTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
           "&dpt=wz.ztzt&Pageindex=0&pagesize=200&sort=fund%3Aasc&date=" + date_c)
    txt = get(url, ref="https://quote.eastmoney.com/ztb/detail")
    try:
        j = json.loads(txt or "{}")
        pool = (j.get("data") or {}).get("pool") or []
        return [{"code": r.get("c"), "name": r.get("n"), "pct": r.get("zdp"), "industry": r.get("hybk")} for r in pool]
    except Exception:
        return []


# ============ 6. 日K线 ============
def collect_kline(code, num=70):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{num},qfq"
    txt = get(url)
    if not txt:
        return []
    try:
        j = json.loads(txt)
        d = j["data"][code]
        arr = d.get("qfqday") or d.get("day") or []
    except Exception as e:
        print(f"[FAIL] kline {code} {e}")
        return []
    out = []
    for r in arr:
        try:
            out.append({"date": r[0], "open": float(r[1]), "close": float(r[2]),
                        "high": float(r[3]), "low": float(r[4]), "vol": float(r[5])})
        except Exception:
            pass
    return out


# ============ 7. 个股资金流 ============
def collect_fundflow(secid):
    url = ("https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?lmt=0&klt=1&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&secid=" + secid)
    txt = get(url, ref="https://data.eastmoney.com/")
    try:
        j = json.loads(txt or "{}")
        kls = (j.get("data") or {}).get("klines") or []
        if not kls:
            return None
        last = kls[-1].split(",")
        return {"main": float(last[1]), "small": float(last[2]), "mid": float(last[3]),
                "big": float(last[4]), "huge": float(last[5]), "time": last[0]}
    except Exception:
        return None


# ============ 8. 资讯 ============
def collect_news(n=40):
    url = ("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns?client=web&biz=web_news_col"
           "&column=350&order=1&needInteractData=0&page_index=1&page_size=%d&req_trace=1"
           "&fields=code%%2CshowTime%%2Ctitle%%2CmediaName%%2Csummary%%2CuniqueUrl") % n
    txt = get(url, ref="https://finance.eastmoney.com/")
    try:
        j = json.loads(txt or "{}")
        items = (j.get("data") or {}).get("list") or []
        return [{"title": i.get("title"), "time": i.get("showTime"), "media": i.get("mediaName"),
                 "summary": (i.get("summary") or "")[:200], "url": i.get("uniqueUrl")} for i in items]
    except Exception as e:
        print(f"[FAIL] news {e}")
        return []


# ============ 8b. 资讯过滤 + 同事件聚类 ============
def _norm_title(t):
    # 去掉标点/空白，取前 12 字符作为"同事件"指纹
    return re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t or "")[:12]

def filter_news(news, date, mode):
    """按报告日期 + 时段过滤，并对同一事件的重复标题聚类（只保留首次出现）。
    - strict-midday：仅保留 当日 09:00–11:30 的资讯；
    - late-snapshot / render-archive：保留当日全部，但 as_of 标注为采集时刻。
    """
    out, seen = [], set()
    for n in news:
        tm = n.get("time") or ""
        if date not in tm:                       # 必须是报告当日
            continue
        hhmm = tm[-8:-3].replace(":", "")
        if mode == "strict-midday" and hhmm > "1130":
            continue
        key = _norm_title(n.get("title"))
        if key in seen:                          # 同事件聚类
            continue
        seen.add(key)
        out.append(n)
    return out


def detect_mode(now=None):
    now = now or datetime.now()
    hm = now.hour * 100 + now.minute
    return "strict-midday" if 1130 <= hm < 1300 else "late-snapshot"


def build_quality(DATE, snapshot, minutes, br, news, mode):
    errors, warnings = [], []
    miss_idx = [n for c, n in INDEX_MAP if c not in (snapshot or {})]
    if miss_idx:
        errors.append("指数缺失: " + ",".join(miss_idx))
    bad_min = []
    for c, n in INDEX_MAP:
        ms = (minutes or {}).get(c) or []
        if not ms or ms[-1]["t"] > "1130":
            bad_min.append(n)
    if bad_min:
        warnings.append("分时末点非11:30: " + ",".join(bad_min))
    if br:
        if (br.get("up", 0) + br.get("down", 0) + br.get("flat", 0) + br.get("missing", 0)
                != br.get("listed_total", 0)):
            errors.append("广度恒等式不成立 up+down+flat+missing != listed_total")
    bad_news = [n for n in (news or []) if DATE not in (n.get("time") or "")]
    if bad_news:
        warnings.append(f"{len(bad_news)} 条资讯非报告当日")
    level = "fail" if errors else ("warn" if warnings else "pass")
    return {"level": level, "errors": errors, "warnings": warnings}


def save_outputs(OUT, br, DC):
    """落盘合并 JSON + 单独涨跌家数文件。

    成功 / 失败路径共用，确保「采集成功 → 文件必然写出」契约闭环，
    杜绝旧版"打印完成但不写文件"导致下游读到旧文件/空文件的假成功。
    """
    path = f"midday_merged_{DC}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=1)
    if br:
        bpath = f"breadth_{DC}.json"
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(br, f, ensure_ascii=False, indent=1)
    return path


def _run_selftest(DC, fail=False):
    """离线自检：合成一份 v2 形态的 OUT，走真实 save_outputs 落盘路径。

    用于回归测试"采集成功 / 失败后 midday_merged_{DC}.json 是否存在且含关键字段"，
    不联网、秒级完成。直接复用正式落盘函数，确保测的是同一段代码。
    """
    level = "fail" if fail else "pass"
    OUT = {
        "snapshot": {"sh000001": {"price": 3000.0, "pct": 0.5, "prev_close": 2985.0}},
        "minutes": {"sh000001": [{"t": "11:30", "p": 3000.0, "v": 1, "amt": 1}]},
        "sector_industry": [], "sector_concept": [],
        "zt_pool": [], "dt_pool": [],
        "kline": {}, "fundflow": {}, "news": [],
        "market_amount": {"sh_wan": 60000000, "sz_wan": 70000000, "total_yi": 13000.0},
        "breadth": {"listed_total": 5901, "valid_total": 5543, "missing": 358,
                    "up": 4544, "down": 550, "flat": 449,
                    "up_ratio_valid": 0.82, "method": "selftest"},
        "meta": {"schema_version": 2, "report_date": DC[:4] + "-" + DC[4:6] + "-" + DC[6:],
                 "collected_at": "2026-08-24 11:30:00", "mode": "strict-midday",
                 "as_of": "11:30"},
        "quality": {"level": level, "errors": [], "warnings": []},
        "sources": {"minutes": {"status": "ok", "as_of": "11:30", "coverage": "8/8"},
                    "breadth": {"status": "ok", "as_of": "11:30", "listed_total": 5901},
                    "news": {"status": "ok", "as_of": "11:30"}},
    }
    save_outputs(OUT, OUT["breadth"], DC)
    print(f"[SELFTEST] wrote midday_merged_{DC}.json (quality={level})")
    sys.exit(3 if fail else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--mode", default=None,
                    choices=["strict-midday", "late-snapshot", "render-archive"],
                    help="运行模式；不传则按当前时刻自动判定（11:30-13:00→strict，其余→late）")
    ap.add_argument("--holdings", default=None,
                    help="持仓配置文件 JSON 路径；持仓为个人私有配置不随 Skill 分发，见 holdings.example.json")
    ap.add_argument("--selftest", action="store_true",
                    help="离线自检：合成 v2 数据并走真实落盘路径，不联网、不触发时间/日期闸门")
    ap.add_argument("--selftest-fail", action="store_true",
                    help="离线自检（失败档）：验证质量 fail 时仍落盘供排查")
    args = ap.parse_args()
    DATE = args.date
    DC = DATE.replace("-", "")
    if args.selftest or args.selftest_fail:
        _run_selftest(DC, fail=args.selftest_fail)
    MODE = args.mode or detect_mode()
    collected_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"=== 午间复盘采集 {DATE} | 模式={MODE} ===")

    # —— 运行闸门 ——
    if MODE == "strict-midday":
        hm = datetime.now().hour * 100 + datetime.now().minute
        if not (1130 <= hm < 1300):
            print("[FAIL] strict-midday 仅允许 11:30–13:00 采集；"
                  "午后请用 --mode late-snapshot，或保留中午 JSON 后 --mode render-archive 重渲。")
            sys.exit(2)
    # 禁止用今天的分时写入非今日文件（腾讯分时接口只返回当日数据）
    today = datetime.now().strftime("%Y-%m-%d")
    if DATE != today:
        print(f"[FAIL] --date({DATE}) 必须等于采集当日({today})；"
              f"否则分时/快照为今日数据却写入历史日期，造成伪历史。")
        sys.exit(2)

    global HOLD
    HOLD = load_holdings(args.holdings or _default_holdings())

    OUT = {}
    OUT["snapshot"] = collect_snapshot()
    print(f"[OK] 快照 {len(OUT['snapshot'])} 条")

    minutes = {}
    for item in INDEX_MAP + HOLD:
        c, n = item[0], item[1]
        m = get_minute(c)
        if m:
            minutes[c] = m
            print(f"[OK] 分时 {n} {len(m)}点 末点{m[-1]['t']}={m[-1]['p']}")
        time.sleep(0.2)
    OUT["minutes"] = minutes

    ind = collect_sector("2"); OUT["sector_industry"] = ind
    print(f"[OK] 行业板块 {len(ind)} 个")
    con = collect_sector("3"); OUT["sector_concept"] = con
    print(f"[OK] 概念板块 {len(con)} 个")

    br = collect_breadth()
    print(f"[OK] 涨跌家数 有效样本{br.get('valid_total')}（listed={br.get('listed_total')}, missing={br.get('missing')}）")

    zt = collect_zt(DC); OUT["zt_pool"] = zt
    print(f"[OK] 涨停池 {len(zt)} 只")
    dt = collect_dt(DC); OUT["dt_pool"] = dt
    print(f"[OK] 跌停池 {len(dt)} 只")

    kl = {}
    for item in INDEX_MAP + HOLD:
        c, n = item[0], item[1]
        k = collect_kline(c)
        if k:
            kl[c] = k
            print(f"[OK] K线 {n} {len(k)}根")
        time.sleep(0.2)
    OUT["kline"] = kl

    ff = {}
    for hc in HOLD:
        secid, key = _secid(hc[0])
        f = collect_fundflow(secid)
        if f:
            ff[key] = f
            print(f"[OK] 资金流 {key} 主力{f['main']/1e4:.0f}万")
        time.sleep(0.3)
    OUT["fundflow"] = ff

    raw_news = collect_news(40)
    nw = filter_news(raw_news, DATE, MODE)
    OUT["news"] = nw
    print(f"[OK] 资讯 原始{len(raw_news)} → 过滤后{len(nw)} 条（模式={MODE}）")

    # 两市成交额
    d = parse_tencent(get("https://qt.gtimg.cn/q=sh000001,sz399001", decode="gbk"))
    sh = d.get("sh000001", {}).get("amount_wan", 0)
    sz = d.get("sz399001", {}).get("amount_wan", 0)
    OUT["market_amount"] = {"sh_wan": sh, "sz_wan": sz, "total_yi": (sh + sz) / 1e4}

    # —— 数据质量闸门 ——
    q = build_quality(DATE, OUT["snapshot"], OUT["minutes"], br, OUT["news"], MODE)
    sources = {
        "minutes": {"status": "ok" if OUT["minutes"] else "missing", "as_of": "11:30",
                    "coverage": f"{len(OUT['minutes'])}/{len(INDEX_MAP) + len(HOLD)}"},
        "breadth": {"status": "ok" if br else "missing",
                    "as_of": collected_at[-8:-3], "listed_total": br.get("listed_total") if br else None},
        "news": {"status": "ok" if OUT["news"] else "missing",
                 "as_of": "11:30" if MODE == "strict-midday" else collected_at[-8:-3]},
    }
    OUT["breadth"] = br
    OUT["meta"] = {"schema_version": 2, "report_date": DATE,
                  "collected_at": collected_at, "mode": MODE}
    OUT["quality"] = q
    OUT["sources"] = sources

    # 核心数据缺失 → 非零退出，不生成"伪完整"报告（仍落盘供排查）
    if q["level"] == "fail":
        print(f"\n[FAIL] 数据质量不达标，已终止生成。errors={q['errors']}")
        save_outputs(OUT, br, DC)
        sys.exit(3)
    # 成功路径：落盘合并 JSON + 单独涨跌家数文件（供 generate 脚本 --breadth 使用）
    save_outputs(OUT, br, DC)
    print(f"\n=== 完成 -> midday_merged_{DC}.json (quality={q['level']}, "
          f"warnings={len(q['warnings'])}) ===")


if __name__ == "__main__":
    main()
