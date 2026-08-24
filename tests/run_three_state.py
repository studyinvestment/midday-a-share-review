# -*- coding: utf-8 -*-
"""三态回归测试 harness。

对三份夹具（普涨/分化/普跌）分别调用 generate_midday_review.py 渲染，
断言：
  1) 进程退出码 = 0，HTML 正常生成；
  2) 各行情下 breadth_regime / zt_regime 对应的叙事分支文案正确出现；
  3) HTML 中「上涨占比 X%」与夹具注入的宽度一致；
  4) 额外用非周五日期跑一次分化夹具，验证 weekday 分支（隔夜外盘）。

用法：
  python run_three_state.py
退出码：全部通过 0；任一失败 1。
"""
import json, os, re, subprocess, sys

# Windows 默认控制台编码为 GBK，打印中文易抛 UnicodeEncodeError。
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(SKILL, "scripts", "generate_midday_review.py")
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# 用当前解释器自身，避免写死绝对路径导致换机器直接失败
PY = sys.executable

# 每个工况：regime, date, 期望出现的关键文案, 期望的上涨占比(±容差)
CASES = [
    ("broad_rise", "2026-08-21",
     {"行情具备一定广度支撑",            # breadth_regime = strong
      "赚钱效应尚可", "涨停结构尚可"},   # zt_regime = active
     82.0),
    ("differentiation", "2026-08-21",
     {"行情结构偏均衡",                  # breadth_regime = neutral
      "赚钱效应一般"},                   # zt_regime 受 breadth 驱动
     46.0),
    ("broad_fall", "2026-08-21",
     {"上午上涨主要由少数权重与主线拉动",  # breadth_regime = weak
      "赚钱效应显著恶化", "涨停结构脆弱"}, # zt_regime = weak
     18.0),
]
# 额外：非周五日期验证 weekday 分支
BONUS = ("differentiation", "2026-08-19", {"隔夜外盘不确定性"})


def run_one(reg, date):
    merged = os.path.join(FIX, f"{reg}_merged.json")
    out = os.path.join(FIX, f"{reg}_{date}_review.html")
    r = subprocess.run([PY, GEN, "--merged", merged, "--date", date, "--out", out],
                       capture_output=True, text=True, timeout=120)
    html = open(out, encoding="utf-8").read() if os.path.exists(out) else ""
    return r.returncode, out, html, r.stderr


def check_case(reg, date, expects, expect_ratio):
    rc, out, html, err = run_one(reg, date)
    probs = []
    if rc != 0:
        probs.append(f"进程退出码={rc}（stderr 末 400 字）\n{err[-400:]}")
    if not html:
        probs.append("HTML 未生成")
    for s in expects:
        if s not in html:
            probs.append(f"缺失文案: 「{s}」")
    m = re.search(r"上涨占比\s*([\d.]+)%", html)
    if m:
        got = float(m.group(1))
        if abs(got - expect_ratio) > 1.0:
            probs.append(f"上涨占比不符: 期望≈{expect_ratio}% 实际={got}%")
    else:
        probs.append("未找到『上涨占比 X%』字段")
    ok = not probs
    print(f"[{'PASS' if ok else 'FAIL'}] {reg:14s} ({date})  rc={rc}  "
          f"size={len(html)//1024}KB")
    for p in probs:
        print("      - " + p)
    return ok


def main():
    all_ok = True
    for reg, date, expects, ratio in CASES:
        if not check_case(reg, date, expects, ratio):
            all_ok = False
    # 非周五 weekday 分支
    rc, out, html, err = run_one(*BONUS[:2])
    need = list(BONUS[2])[0]
    ok = rc == 0 and os.path.exists(out) and (need in html)
    print(f"[{'PASS' if ok else 'FAIL'}] weekday-非周五 ({BONUS[1]})  rc={rc}  "
          f"期望文案存在={ok}")
    if not ok:
        all_ok = False
    print("\n==== 三态回归结果:", "全部通过 ✅" if all_ok else "存在失败 ❌", "====")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
