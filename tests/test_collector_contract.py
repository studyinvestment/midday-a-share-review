# -*- coding: utf-8 -*-
"""采集器落盘契约回归测试（最小、离线、秒级）。

只测一件事：采集器"成功 / 失败"两种结局下，midday_merged_{DC}.json
【必然写出】且含 v2 关键字段（quality / sources / meta.as_of / breadth）。

这正是 2026-08-24 发现的 P0 bug（成功路径只打印"完成"不写文件）的回归锁。
通过 --selftest / --selftest-fail 走真实 save_outputs 落盘代码，不联网。

用法：
  python test_collector_contract.py
退出码：全部通过 0；任一失败 1。
"""
import json, os, subprocess, sys, tempfile

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECT = os.path.join(SKILL, "scripts", "collect_midday_data.py")
DATE = "2026-08-24"
DC = DATE.replace("-", "")
MERGED = f"midday_merged_{DC}.json"

V2_KEYS = ["quality", "sources", "meta", "breadth"]


def _run_selftest(fail, workdir):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, COLLECT, "--date", DATE,
         "--selftest-fail" if fail else "--selftest"],
        cwd=workdir, capture_output=True, text=True, timeout=60, env=env)


def _check_success(workdir):
    rc = _run_selftest(False, workdir)
    probs = []
    if rc.returncode != 0:
        probs.append(f"退出码={rc.returncode}（stderr 末 300 字）\n{rc.stderr[-300:]}")
    path = os.path.join(workdir, MERGED)
    if not os.path.exists(path):
        probs.append(f"文件未写出：{path}")
        return False, probs
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        probs.append(f"JSON 解析失败：{e}")
        return False, probs
    for k in V2_KEYS:
        if k not in data:
            probs.append(f"缺失 v2 关键字段：{k}")
    if not isinstance(data.get("quality"), dict) or "level" not in data["quality"]:
        probs.append("quality.level 缺失或非 dict")
    meta = data.get("meta")
    if not isinstance(meta, dict) or "as_of" not in meta:
        probs.append("meta.as_of 缺失")
    ok = not probs
    print(f"[{'PASS' if ok else 'FAIL'}] 成功路径落盘契约  rc={rc.returncode}")
    for p in probs:
        print("      - " + p)
    return ok, probs


def _check_fail_still_writes(workdir):
    """失败档：质量 fail 时仍应落盘供排查（不得静默丢失）。"""
    rc = _run_selftest(True, workdir)
    probs = []
    if rc.returncode == 0:
        probs.append("失败档竟以退出码 0 结束（应非零）")
    path = os.path.join(workdir, MERGED)
    if not os.path.exists(path):
        probs.append(f"失败档也未写出文件：{path}（违背'仍落盘供排查'契约）")
    else:
        try:
            data = json.load(open(path, encoding="utf-8"))
            if data.get("quality", {}).get("level") != "fail":
                probs.append("失败档 quality.level 应为 fail")
        except Exception as e:
            probs.append(f"失败档 JSON 解析失败：{e}")
    ok = not probs
    print(f"[{'PASS' if ok else 'FAIL'}] 失败档仍落盘契约  rc={rc.returncode}")
    for p in probs:
        print("      - " + p)
    return ok, probs


def main():
    workdir = tempfile.mkdtemp(prefix="midday_contract_")
    all_ok = True
    ok1, _ = _check_success(workdir)
    ok2, _ = _check_fail_still_writes(workdir)
    all_ok = ok1 and ok2
    print("\n==== 采集器落盘契约回归:", "全部通过 ✅" if all_ok else "存在失败 ❌", "====")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
