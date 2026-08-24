# -*- coding: utf-8 -*-
"""midday-a-share-review 统一测试入口。

依次运行：
  1) test_collector_contract.py —— 采集器落盘契约（成功/失败均必写文件 + v2 关键字段）
  2) run_three_state.py        —— 渲染器三态回归（普涨/分化/普跌 + 工作日分支）

用法：
  python run_tests.py
退出码：全部通过 0；任一失败 1。
"""
import os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["test_collector_contract.py", "run_three_state.py"]


def main():
    # Windows 默认 GBK 控制台易因中文输出报 UnicodeEncodeError，强制 UTF-8。
    os.environ["PYTHONUTF8"] = "1"
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    all_ok = True
    for s in SUITES:
        path = os.path.join(HERE, s)
        print("\n" + "=" * 60)
        print(f"▶ 运行 {s}")
        print("=" * 60)
        r = subprocess.run([sys.executable, path], cwd=HERE,
                           timeout=300, env=os.environ)
        if r.returncode != 0:
            all_ok = False

    print("\n" + "=" * 60)
    print("总结果:", "全部通过 ✅" if all_ok else "存在失败 ❌")
    print("=" * 60)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
