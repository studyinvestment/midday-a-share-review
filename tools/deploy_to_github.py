# -*- coding: utf-8 -*-
"""将 midday-a-share-review 模板上传到 GitHub，作为 WorkBuddy / Codex 通用可复用模板。

用法：
  python deploy_to_github.py --token ghp_xxx --owner studyinvestment \
      --repo midday-a-share-review --private --template

说明：
  - 走 GitHub REST API（api.github.com）创建仓库并上传文件，无需 git push，
    规避 github.com:443 上行 TLS 不稳的问题。
  - token 需有 repo 权限（classic PAT 勾选 repo；或 fine-grained 勾选 repository creation）。
  - 自动排除 holdings.json / __pycache__（避免把个人真实持仓推到公开/共享仓库）。
  - 首次上传会在新仓库 main 分支创建初始提交。
"""
import os, sys, json, base64, argparse, subprocess, urllib.request, urllib.error

API = "https://api.github.com"
# 本脚本位于 <skill>/tools/，上传根目录为上一级（即 skill 根）
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def api(method, path, token, body=None):
    url = API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "midday-skill-deploy")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), e.code


def get_token(explicit):
    """优先用显式 --token；否则从 git credential store 取 github.com 的 token，避免命令行暴露。"""
    if explicit:
        return explicit
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=20,
        ).stdout
        for line in out.splitlines():
            if line.startswith("password="):
                return line[len("password="):]
    except Exception:
        pass
    return None


def collect_files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", ".git")]
        for f in fn:
            if f == "holdings.json":
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            out.append((rel, full))
    return sorted(out)


def create_repo(token, owner, repo, private, desc, template):
    body = {"name": repo, "private": private, "description": desc,
            "auto_init": False, "is_template": bool(template)}
    resp, code = api("POST", "/user/repos", token, body)
    if code in (200, 201):
        return True, None
    if code == 422:  # 已存在
        return True, "已存在，继续上传"
    # 个人创建失败（可能 owner 是 org 或 token 无个人建仓权限）→ 试 org
    resp2, code2 = api("POST", f"/orgs/{owner}/repos", token, body)
    if code2 in (200, 201):
        return True, None
    if code2 == 422:
        return True, "已存在，继续上传"
    return False, f"user({code}): {resp}\norg({code2}): {resp2}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None, help="GitHub PAT（repo 权限）；省略时自动从 git credential 取")
    ap.add_argument("--owner", required=True, help="GitHub 用户名或组织名")
    ap.add_argument("--repo", default="midday-a-share-review")
    ap.add_argument("--public", action="store_true", help="公开仓库（默认私有）")
    ap.add_argument("--template", action="store_true", help="设为 GitHub Template 仓库")
    ap.add_argument("--desc", default="A股午间复盘 Skill 模板（WorkBuddy/Codex 通用，持仓外置）")
    args = ap.parse_args()
    private = not args.public
    token = get_token(args.token)
    if not token:
        print("[FAIL] 未提供 --token 且无法从 git credential 取得 github.com token。")
        sys.exit(1)

    ok, msg = create_repo(token, args.owner, args.repo, private, args.desc, args.template)
    if not ok:
        print(f"[FAIL] 创建仓库失败：\n{msg}")
        sys.exit(1)
    print(f"[OK] 仓库就绪：https://github.com/{args.owner}/{args.repo} {('('+msg+')') if msg else ''}")

    files = collect_files(SKILL_DIR)
    print(f"待上传 {len(files)} 个文件（已排除 holdings.json / __pycache__）")
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        get, gc = api("GET", f"/repos/{args.owner}/{args.repo}/contents/{rel}", token)
        body = {"message": f"add {rel}", "content": content, "branch": "main"}
        if gc == 200:
            try:
                body["sha"] = json.loads(get)["sha"]
            except Exception:
                pass
        r, st = api("PUT", f"/repos/{args.owner}/{args.repo}/contents/{rel}", token, body)
        if st in (200, 201):
            print(f"  [OK] {rel}")
        else:
            print(f"  [FAIL] {rel} ({st}): {r[:200]}")

    print("完成。克隆后放入自己的 holdings.json 即可使用。")


if __name__ == "__main__":
    main()
