# -*- coding: utf-8 -*-
"""发布工具的远端孤儿检测单测（缺陷 #43，2026-09-17）。

守护两条：
  1) `find_orphans()` 只报「远端有、本地清单没有」的路径，且 `KEEP_REMOTE`
     命中者一律豁免——防止 `--prune-orphans` 误删仅在网页端维护的
     LICENSE / .github/** / CHANGELOG.md；
  2) `collect_files()` 必须把 `breadth_*.json` / `midday_merged_*.json` 挡在上传清单外
     ——这正是 `tests/breadth_20260824.json` 当年被传上公开仓库的根因。

不联网、秒级完成（`remote_manifest()` / `delete_remote_file()` 涉及网络，不在本测范围）。

用法：python test_deploy_orphans.py     退出码：全通过 0，任一失败 1。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
sys.path.insert(0, os.path.dirname(HERE))
import deploy_to_github as dep                                    # noqa: E402

FAILS = []


def check(desc, got, want):
    ok = got == want
    print(("  [OK]   " if ok else "  [FAIL] ") + desc)
    if not ok:
        print("         got  = %r" % (got,))
        print("         want = %r" % (want,))
        FAILS.append(desc)


def main():
    local = [("a.py", "x"), ("sub/b.md", "y")]

    print("A. find_orphans 基本语义")
    check("远端多出的文件被识别为孤儿",
          dep.find_orphans(local, ["a.py", "sub/b.md", "stale.json",
                                   "tests/breadth_20260824.json"]),
          ["stale.json", "tests/breadth_20260824.json"])
    check("完全一致时无孤儿", dep.find_orphans(local, ["a.py", "sub/b.md"]), [])
    check("远端为空时无孤儿", dep.find_orphans(local, []), [])
    check("本地为空则远端全是孤儿", dep.find_orphans([], ["a.py"]), ["a.py"])

    print("B. KEEP_REMOTE 豁免（防止误删仅在网页端维护的文件）")
    remote = ["a.py", "LICENSE", "NOTICE", ".github/workflows/ci.yml",
              ".github/ISSUE_TEMPLATE/bug.md", "CHANGELOG.md", "stray.json"]
    check("LICENSE/NOTICE/.github/**/CHANGELOG.md 被豁免",
          dep.find_orphans(local, remote), ["stray.json"])
    check("--keep-remote 追加通配可生效",
          dep.find_orphans(local, ["a.py", "assets/logo.png"], keep=("assets/*",)), [])
    check("未被追加通配命中时仍报孤儿",
          dep.find_orphans(local, ["a.py", "assets/logo.png"]), ["assets/logo.png"])

    print("C. collect_files 必须挡住散落采集产物（#43 根因回归）")
    with tempfile.TemporaryDirectory() as td:
        for name in ("keep.md", "breadth_20260824.json", "midday_merged_20260824.json",
                     "holdings.json", "x_review.html"):
            with open(os.path.join(td, name), "w", encoding="utf-8") as f:
                f.write("x")
        check("只保留 keep.md（其余 4 个均被排除）",
              [rel for rel, _ in dep.collect_files(td)], ["keep.md"])

    print("D. 真实场景复现（当年泄漏到远端的那一个路径）")
    check("history 中的孤儿确实会被点名",
          dep.find_orphans(local, ["a.py", "sub/b.md", "tests/breadth_20260824.json"]),
          ["tests/breadth_20260824.json"])

    print("\n==== 发布工具孤儿检测:", "全部通过 ✅" if not FAILS else "%d 项失败 ❌" % len(FAILS), "====")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
