# -*- coding: utf-8 -*-
"""将 midday-a-share-review 模板上传到 GitHub，作为 WorkBuddy / Codex 通用可复用模板。

用法：
  python deploy_to_github.py --owner studyinvestment \
      --repo midday-a-share-review --public --template

说明：
  - 走 GitHub REST API（api.github.com）创建仓库并上传文件，无需 git push，
    规避 github.com:443 上行 TLS 不稳的问题。
  - token 优先用 --token；省略时自动从 git credential store 取（不在命令行暴露）。
  - token 需有 repo 权限（classic PAT 勾选 repo；或 fine-grained 勾选 repository creation）。
  - 自动排除 holdings.json / __pycache__ / *_review.html / 调试日志 / 散落采集产物（见 SKIP_NAMES + SKIP_GLOBS）。
  - **上传前强制执行脱敏闸门**（2026-09-15 新增）：扫描所有待上传文本文件，
    命中真实持仓代码/名称即**中止发布**（退出码 5）。详见 `leak_scan()` 与 `--allow-privacy-leak`。
  - 首次上传会在新仓库 main 分支创建初始提交。
"""
import os, sys, json, base64, re, fnmatch, argparse, subprocess, urllib.request, urllib.error

API = "https://api.github.com"
# 本脚本位于 <skill>/tools/，上传根目录为上一级（即 skill 根）
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 不入仓的运行时/调试产物（显式列出，便于审计；同时见 .gitignore）
SKIP_NAMES = {
    "holdings.json",          # 真实持仓，最高优先级
    # 历史调试产物：2026-09-15 已从本机删除，条目保留作**防御**——
    # 若本机调试再次生成同名文件，仍不得入库。
    ".runlog.txt",            # 回归运行日志（GBK 乱码版）
    "_final.txt",             # 回归运行日志快照
    "_runlog_utf8.txt",       # 回归运行日志（UTF-8 版）
    "_run_utf8.py",           # 本机 GBK 绕行小工具
}
# 通配排除（fnmatch）：散落的采集/自检产物，均非测试夹具
# （夹具命名形如 `<形态名>_merged.json`，不会与之冲突）
SKIP_GLOBS = ("breadth_*.json", "midday_merged_*.json")
# 明显二进制、无需做文本扫描的扩展名
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip",
    ".gz", ".7z", ".xlsx", ".xls", ".pptx", ".docx", ".woff", ".woff2", ".ttf",
    ".otf", ".mp3", ".mp4", ".pyc", ".so", ".dll", ".exe",
}


def _norm_code(c):
    """归一化：带市场前缀/后缀的代码取其末 6 位数字；其余原样返回。"""
    c = (c or "").strip().lower()
    return c[-6:] if len(c) >= 6 and c[-6:].isdigit() else c


def privacy_tokens():
    """解析脱敏名单（真实持仓「代码 + 名称」）。

    按序取：① 环境变量 MIDDAY_PRIVACY（逗号分隔）→ ② skill 目录 / 其上一级 / 当前工作目录的
    holdings.json。与 `tests/build_fixtures.py` 共用同一份外部配置，**源码内不保留任何真实持仓**。
    """
    toks = [c for c in (os.environ.get("MIDDAY_PRIVACY") or "").split(",") if c.strip()]
    if not toks:
        for cand in (os.path.join(SKILL_DIR, "holdings.json"),
                     os.path.join(os.path.dirname(SKILL_DIR), "holdings.json"),
                     os.path.join(os.getcwd(), "holdings.json")):
            if os.path.exists(cand):
                try:
                    hj = json.load(open(cand, encoding="utf-8"))
                    out = []
                    for row in hj.get("holdings", []):
                        if row:
                            out += [str(x) for x in row[:2] if x]   # 代码 + 名称都参与剔除
                    toks = out
                except Exception:
                    toks = []
                if toks:
                    break
    return tuple(dict.fromkeys(_norm_code(t) for t in toks if _norm_code(t)))


def leak_scan(text, tokens):
    """在文本中查找隐私 token，返回命中列表。

    **6 位纯数字必须按「数字边界」匹配**：朴素子串会把 JSON 里长数字的尾段当成代码命中
    （实测某持仓代码与板块资金流 `-1234568816.0` 尾段相同 → 脱敏成功却误报失败）。
    安全闸门的误报比漏报更危险——它会训练使用者忽略告警。故数字代码用
    `(?<![\\d.])CODE(?!\\d)`：`"lead_code": "123456"` / `sz123456` 仍命中，`-1234568816.0` 不命中。
    非数字 token（中文名称）沿用子串匹配。
    """
    hits = []
    for t in tokens:
        t = str(t)
        if re.fullmatch(r"\d{6}", t):
            if re.search(r"(?<![\d.])" + t + r"(?!\d)", text):
                hits.append(t)
        elif t and t in text:
            hits.append(t)
    return hits


def audit_privacy(files, tokens):
    """发布前评审：逐文件扫描隐私残留，返回 [(相对路径, [命中 token]), ...]。"""
    findings = []
    for rel, full in files:
        if os.path.splitext(full)[1].lower() in BINARY_EXT:
            continue
        try:
            raw = open(full, "rb").read()
        except Exception:
            continue
        if b"\x00" in raw[:8192]:           # 二进制兜底探测
            continue
        hits = leak_scan(raw.decode("utf-8", "ignore"), tokens)
        if hits:
            findings.append((rel, hits))
    return findings


def _api_urllib(method, path, token, body):
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


def _api_curl(method, path, token, body):
    """curl 子进程后端：Windows 沙箱内 Python 直连常 getaddrinfo failed（DNS），
    curl 子进程却正常（见 SKILL.md「Windows/沙箱硬约束」）。body 经 stdin 传入，
    响应体写临时文件，HTTP 状态码经 -w 取回。"""
    import tempfile
    url = API + path
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    args = ["curl", "-s", "-m", "40", "-X", method, "-o", tmp, "-w", "%{http_code}",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/vnd.github+json",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: midday-skill-deploy",
            url]
    try:
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            r = subprocess.run(args + ["--data-binary", "@-"], input=data,
                               capture_output=True, timeout=90)
        else:
            r = subprocess.run(args, capture_output=True, timeout=90)
        try:
            code = int((r.stdout or b"0").decode("ascii", "replace").strip() or 0)
        except ValueError:
            code = 0
        try:
            with open(tmp, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception:
            content = ""
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return content, code


def api(method, path, token, body=None):
    """统一入口：urllib 优先；若 DNS 直连失败（Windows 沙箱常见）自动降级 curl 后端。"""
    try:
        return _api_urllib(method, path, token, body)
    except urllib.error.URLError as e:
        if "getaddrinfo" in str(e) or "Name or service not known" in str(e):
            return _api_curl(method, path, token, body)
        raise


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
            if f in SKIP_NAMES:
                continue
            # 测试运行时产物（run_three_state.py 每次重跑生成，.gitignore 亦忽略）
            if f.endswith("_review.html"):
                continue
            # 散落的采集/自检产物（如 --selftest 在工作目录留下的 breadth_*.json）
            if any(fnmatch.fnmatch(f, g) for g in SKIP_GLOBS):
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
    ap.add_argument("--audit-only", action="store_true", help="只跑脱敏闸门并列出待上传清单，不上传")
    ap.add_argument("--allow-privacy-leak", action="store_true",
                    help="❗闸门发现隐私命中时仍继续上传（极不推荐，仅用于已知情的一次性补救）")
    ap.add_argument("--allow-no-privacy", action="store_true",
                    help="❗未取得脱敏名单时仍继续（等于放弃本次隐私校验）")
    args = ap.parse_args()
    private = not args.public

    files = collect_files(SKILL_DIR)
    print(f"待上传 {len(files)} 个文件（已排除 holdings.json / __pycache__ / *_review.html / 调试日志 / 散落采集产物）")

    # ★★ 脱敏闸门：必须发生在 create_repo 等任何外部动作之前。
    # 2026-09-15 事故教训：真实持仓（含 fixture JSON 第一行的 sector 龙头字段）一路走到了
    # 「准备发布到公开仓库」这一步才被发现。发布工具此前**没有任何隐私校验**，
    # 全靠人工 review——而该字段在 400KB 单行 JSON 里肉眼不可见。闸门补在工具里才算真的有约束。
    tokens = privacy_tokens()
    if not tokens:
        print("[WARN] 未取得脱敏名单：环境变量 MIDDAY_PRIVACY 与 holdings.json 均不可用，")
        print("       本次无法证明待上传内容不含真实持仓。请补上名单后重试；")
        print("       确属无持仓可用时追加 --allow-no-privacy 表示已知悉。")
        if not args.allow_no_privacy:
            sys.exit(6)
    else:
        findings = audit_privacy(files, tokens)
        if findings:
            print(f"\n[FAIL] 脱敏闸门未通过：{len(findings)} 个文件命中真实持仓代码/名称，已中止发布。")
            for rel, hits in findings:
                print(f"  ✗ {rel}  →  {', '.join(sorted(set(hits)))}")
            print("  请先脱敏（改用中性示例代码/名称）再重跑；夹具请用 "
                  "`tests/build_fixtures.py --real` 重生成。")
            if not args.allow_privacy_leak:
                sys.exit(5)
            print("  ⚠️ 已指定 --allow-privacy-leak：明知命中仍继续上传（不推荐）。")
        else:
            print(f"[OK] 脱敏闸门通过：{len(files)} 个文件未命中 {len(tokens)} 项隐私 token。")

    if args.audit_only:
        print("\n待上传清单（人工复核用）：")
        total = 0
        for rel, full in files:
            sz = os.path.getsize(full)
            total += sz
            print(f"  {rel}  ({sz:,} B)")
        print(f"  共 {len(files)} 个文件 / {total:,} B")
        print("\n--audit-only：仅评审与清单，未执行任何上传动作。")
        return

    token = get_token(args.token)
    if not token:
        print("[FAIL] 未提供 --token 且无法从 git credential 取得 github.com token。")
        sys.exit(1)

    ok, msg = create_repo(token, args.owner, args.repo, private, args.desc, args.template)
    if not ok:
        print(f"[FAIL] 创建仓库失败：\n{msg}")
        sys.exit(1)
    print(f"[OK] 仓库就绪：https://github.com/{args.owner}/{args.repo} {('('+msg+')') if msg else ''}")

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
