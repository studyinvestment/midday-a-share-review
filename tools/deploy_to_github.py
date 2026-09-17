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
  - **远端孤儿检测/清理**（2026-09-17 新增，缺陷 #43）：本脚本只做 PUT，**没有任何 DELETE 语义**，
    因此"本地删了"≠"远端删了" —— 历史上删掉的 `tests/breadth_20260824.json` 就在远端留尸至今。
    现补 `--list-orphans`（只报告，零写入）与 `--prune-orphans`（**显式**删除远端多余文件）。
  - 首次上传会在新仓库 main 分支创建初始提交。
"""
import os, sys, json, base64, re, fnmatch, argparse, subprocess, time, urllib.request, urllib.error

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
# 允许**只存在于远端**、不得被 --prune-orphans 清理的路径（fnmatch 通配）。
# 本地上传清单不可能包含它们，故必须显式豁免，否则会被当成孤儿误删。
KEEP_REMOTE = (
    "LICENSE", "LICENSE.*", "NOTICE",
    ".github/*", ".github/**",          # issue/PR 模板、workflows
    "CHANGELOG.md",                      # 允许在网页端维护
)
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


def _api_urllib(method, path, token, body, proxy=None):
    """proxy=None 表示**显式直连**（禁用 env 代理）；传 URL 表示经该代理。"""
    url = API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "midday-skill-deploy")
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    try:
        with op.open(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace"), e.code


def _api_curl(method, path, token, body, proxy=None):
    """curl 子进程后端：Windows 沙箱内 Python 直连常 getaddrinfo failed（DNS），
    curl 子进程却正常（见 SKILL.md「Windows/沙箱硬约束」）。body 经 stdin 传入，
    响应体写临时文件，HTTP 状态码经 -w 取回。

    proxy=None 时**强制 `--noproxy '*'`**：curl 默认会读 env 代理，
    而沙箱注入的本地 egress 代理对 GitHub 返回 502，必须显式绕开。
    """
    import tempfile
    url = API + path
    fd, tmp = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    args = ["curl", "-s", "-m", "40", "-X", method, "-o", tmp, "-w", "%{http_code}",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Accept: application/vnd.github+json",
            "-H", "Content-Type: application/json",
            "-H", "User-Agent: midday-skill-deploy"]
    args += ["-x", proxy] if proxy else ["--noproxy", "*"]
    args.append(url)
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


# 网络类可重试异常标记：本机（家宽/沙箱）对 api.github.com 是**间歇可达**，
# 一次抖动就会让整次发布在中间文件上中断，故对连接类错误做退避重试 + 传输降级。
# 「代理隧道 / 网关」类（2026-09-17 新增，缺陷 #44）：沙箱会注入 HTTP(S)_PROXY，
# 该代理对 GitHub 返回 `Tunnel connection failed: 502 Bad Gateway`，属网络类，必须可降级。
_RETRY_MARKS = ("getaddrinfo", "name or service not known", "timed out", "timeout",
                "10060", "10054", "10053", "10051", "connection reset", "connection aborted",
                "connection refused", "temporary failure", "eof occurred", "ssl", "handshake",
                "network is unreachable", "no route to host", "remote end closed",
                "tunnel connection failed", "bad gateway", "proxy", "502", "503", "407")


def _is_retryable(exc):
    s = str(exc).lower()
    return any(k in s for k in _RETRY_MARKS)


# 沙箱/系统可能注入 HTTP(S)_PROXY 指向本地 egress 代理。实测（2026-09-17）
# 该代理解析 api.github.com 后 CONNECT 隧道 502，而**直连正常**，
# 故默认**直连优先**，env 代理仅作兜底；显式 --proxy 时只用指定代理。
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                   "ALL_PROXY", "all_proxy")
_FORCE_PROXY = None          # 由 main() 根据 --proxy 设置


def env_proxy():
    for k in _PROXY_ENV_KEYS:
        v = os.environ.get(k)
        if v:
            return v
    return None


def api(method, path, token, body=None, tries=4, proxy=None):
    """统一入口：**直连优先**，网络类错误**退避重试**并逐级降级传输。

    传输顺序：① urllib 直连（显式禁用 env 代理）→ ② env/--proxy 代理（若有）
    → ③ curl（显式 `--noproxy '*'` 直连）。

    2026-09-17 修复（缺陷 #41）：原实现只在 DNS（`getaddrinfo`）失败时降级 curl，
    对**超时 / 连接重置**（WinError 10060 等）直接抛异常 —— 实测本机对 api.github.com
    间歇可达，一次抖动即让整次发布在中间文件上 abort（26 个文件中途失败，需人工重跑）。

    2026-09-17 修复（缺陷 #44，本次实测撞到）：原实现用 `urllib.urlopen` 的**隐式 env 代理**。
    沙箱注入 `HTTP_PROXY=http://127.0.0.1:63370` 后，请求变成 CONNECT 隧道并返回
    `502 Bad Gateway`，**连 `--list-orphans` 这种只读动作都直接抛 traceback**。
    现改为显式 `ProxyHandler`：默认 `{}`（直连），env 代理降为兜底候选，
    并给 curl 后端加 `--noproxy '*'`。

    **只重试网络类异常**；已拿到 HTTP 状态码（含 4xx/5xx）一律直接返回，不重试也不掩盖。
    """
    envp = proxy or _FORCE_PROXY or env_proxy()
    if _FORCE_PROXY:                      # 显式指定：只用它，不试直连
        transports = [envp]
    else:
        transports = [None] + ([envp] if envp else [])
    last = None
    for i in range(tries):
        for px in transports:
            try:
                resp, code = _api_urllib(method, path, token, body, proxy=px)
                if code:
                    return resp, code
                last = RuntimeError("空响应（状态码 0）")
            except Exception as e:        # HTTPError 已在 _api_urllib 内转为 (body, code)
                if not _is_retryable(e):
                    raise
                last = e
        # 网络抖动：最后再试 curl 后端（TCP 栈行为不同，常能通过）
        try:
            resp, code = _api_curl(method, path, token, body, proxy=_FORCE_PROXY)
            if code:
                return resp, code
        except Exception as e:
            last = e
        if i < tries - 1:
            time.sleep(1.5 * (i + 1))
    raise urllib.error.URLError(f"api 连续 {tries} 次不可达（网络窗口期？）: {last}")


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


def remote_manifest(owner, repo, token, branch="main"):
    """取远端分支上全部 blob 路径（递归）。

    返回 (paths, err)：成功时 err 为 None；任何失败都返回 ([], 原因)——
    **取不到远端清单时必须让调用方显式失败，绝不可退化成"远端为空"**，
    否则 --prune-orphans 会拿着空清单去比对（虽然结果同样是空删，但语义是错的）。
    """
    try:
        resp, code = api("GET", f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", token)
    except Exception as e:            # 网络彻底不可达：返回原因，**不抛 traceback**
        return [], f"API 不可达: {e}"
    if code != 200:
        return [], f"HTTP {code}: {str(resp)[:200]}"
    try:
        tree = json.loads(resp).get("tree") or []
    except Exception as e:
        return [], f"响应解析失败: {e}"
    return sorted(t["path"] for t in tree if t.get("type") == "blob"), None


def find_orphans(local_files, remote_paths, keep=KEEP_REMOTE):
    """找出**远端有、本地清单没有**的路径（= 孤儿）。

    纯函数，便于单测。`local_files` 为 collect_files() 的 [(rel, full), ...]；
    `remote_paths` 为远端 blob 路径列表。命中 KEEP_REMOTE 通配的一律豁免，
    避免误删仅在网页端维护的文件（LICENSE / .github 等）。
    """
    local = {rel for rel, _ in local_files}
    out = []
    for p in remote_paths:
        if p in local:
            continue
        if any(fnmatch.fnmatch(p, g) for g in keep):
            continue
        out.append(p)
    return sorted(out)


def delete_remote_file(owner, repo, rel, token, branch="main"):
    """删除远端单文件。contents API 要求提供当前 blob sha，故先 GET 再 DELETE。

    返回 (ok, detail)。404 视为成功（目标本就不存在，幂等）。
    """
    get, gc = api("GET", f"/repos/{owner}/{repo}/contents/{rel}?ref={branch}", token)
    if gc == 404:
        return True, "已不存在（跳过）"
    if gc != 200:
        return False, f"取 sha 失败 HTTP {gc}"
    try:
        sha = json.loads(get)["sha"]
    except Exception as e:
        return False, f"sha 解析失败: {e}"
    body = {"message": f"chore: remove orphan {rel} (not in local manifest)",
            "sha": sha, "branch": branch}
    resp, code = api("DELETE", f"/repos/{owner}/{repo}/contents/{rel}", token, body)
    if code in (200, 204):
        return True, "deleted"
    return False, f"HTTP {code}: {str(resp)[:160]}"


def list_or_prune(owner, repo, files, token, do_prune=False, keep=KEEP_REMOTE):
    """远端孤儿体检：只读报告（默认）或显式删除（do_prune=True）。

    返回进程退出码。**取不到远端清单时绝不继续**；do_prune 下视为失败（返回 1）。
    删除是逐文件的，幂等，中途失败会把失败清单打全再整体返回非零。
    """
    paths, err = remote_manifest(owner, repo, token)
    if err:
        print(f"[WARN] 无法获取远端清单，跳过孤儿检查：{err}")
        return 1 if do_prune else 0
    orph = find_orphans(files, paths, keep=keep)
    if not orph:
        print(f"[OK] 远端孤儿检查：{len(paths)} 个远端文件与本地清单一致，无孤儿。")
        return 0
    print(f"\n[{'PRUNE' if do_prune else 'ORPHAN'}] 远端有 {len(orph)} 个文件不在本地上传清单中：")
    for p in orph:
        print(f"  - {p}")
    if not do_prune:
        print("  仅为报告（零写入）。确认无误后加 --prune-orphans 执行删除。")
        return 0
    print("  开始删除（逐文件 DELETE，幂等）…")
    bad = []
    for p in orph:
        ok, detail = delete_remote_file(owner, repo, p, token)
        print(f"  [{'OK' if ok else 'FAIL'}] {p} — {detail}")
        if not ok:
            bad.append(p)
    if bad:
        print(f"\n[FAIL] {len(bad)}/{len(orph)} 个孤儿删除失败：{', '.join(bad)}")
        print("       可原样重跑 --prune-orphans（幂等）。")
        return 1
    print(f"[OK] 已清理 {len(orph)} 个远端孤儿。")
    return 0


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
    ap.add_argument("--list-orphans", action="store_true",
                    help="列出远端有、本地清单没有的文件（只读，零写入；可与 --audit-only 同用）")
    ap.add_argument("--prune-orphans", action="store_true",
                    help="❗上传成功后**删除**远端孤儿文件（写操作；默认关闭，需显式指定）")
    ap.add_argument("--keep-remote", default=None,
                    help="追加豁免通配（逗号分隔），命中者不视为孤儿，如 'LICENSE,assets/*'")
    ap.add_argument("--proxy", default=None,
                    help="强制经该代理访问 api.github.com（默认直连优先，env 代理仅兜底）")
    args = ap.parse_args()
    private = not args.public
    keep = KEEP_REMOTE + tuple(x.strip() for x in (args.keep_remote or "").split(",") if x.strip())
    global _FORCE_PROXY
    _FORCE_PROXY = args.proxy
    print(f"传输：{'经代理 ' + args.proxy if _FORCE_PROXY else '直连优先'}"
          f"（env 代理: {env_proxy() or '未设置'}）")

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
        if args.prune_orphans:
            print("\n[FAIL] --prune-orphans 与 --audit-only 语义冲突：前者是写操作，"
                  "后者承诺零外部动作。请去掉其一。")
            sys.exit(1)
        if args.list_orphans:
            token0 = get_token(args.token)
            if not token0:
                print("[FAIL] 未提供 --token 且无法从 git credential 取得 github.com token。")
                sys.exit(1)
            sys.exit(list_or_prune(args.owner, args.repo, files, token0, do_prune=False, keep=keep))
        print("\n--audit-only：仅评审与清单，未执行任何上传动作。")
        return

    token = get_token(args.token)
    if not token:
        print("[FAIL] 未提供 --token 且无法从 git credential 取得 github.com token。")
        sys.exit(1)

    try:
        ok, msg = create_repo(token, args.owner, args.repo, private, args.desc, args.template)
    except Exception as e:                  # 网络不可达：给干净提示，不抛 traceback
        print(f"[FAIL] 无法访问 api.github.com：{e}")
        print("       本机对 GitHub 为**间歇可达**，原样重跑通常即可；"
              "若持续失败可加 --proxy <url> 走代理。")
        sys.exit(1)
    if not ok:
        print(f"[FAIL] 创建仓库失败：\n{msg}")
        sys.exit(1)
    print(f"[OK] 仓库就绪：https://github.com/{args.owner}/{args.repo} {('('+msg+')') if msg else ''}")

    failed = []
    for rel, full in files:
        with open(full, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        try:
            get, gc = api("GET", f"/repos/{args.owner}/{args.repo}/contents/{rel}", token)
            body = {"message": f"add {rel}", "content": content, "branch": "main"}
            if gc == 200:
                try:
                    body["sha"] = json.loads(get)["sha"]
                except Exception:
                    pass
            r, st = api("PUT", f"/repos/{args.owner}/{args.repo}/contents/{rel}", token, body)
        except Exception as e:                      # 网络窗口期抖动：记录并继续，最后统一报错
            r, st = str(e), 0
        if st in (200, 201):
            print(f"  [OK]   {rel}")
        else:
            failed.append(rel)
            print(f"  [FAIL] {rel} ({st}): {str(r)[:200]}")

    # 2026-09-17 修复（缺陷 #42，假成功）：原实现即使有文件上传失败，也照样打印"完成"
    # 并以退出码 0 结束 —— 调用方无法从退出码分辨"全部成功"与"部分失败"。
    if failed:
        print(f"\n[FAIL] {len(failed)}/{len(files)} 个文件上传失败：{', '.join(failed)}")
        print("       多为网络窗口期抖动，**重跑本命令即可**（逐文件 PUT、幂等，已成功的不受影响）。")
        sys.exit(1)

    # 孤儿处理放在上传**全部成功之后**：上传失败时远端处于中间态，
    # 此时比对"远端有而本地无"会把刚失败的文件也当成孤儿，语义混乱。
    if args.prune_orphans or args.list_orphans:
        rc = list_or_prune(args.owner, args.repo, files, token,
                           do_prune=args.prune_orphans, keep=keep)
        if rc:
            sys.exit(rc)

    print(f"完成（{len(files)} 个文件全部上传成功）。克隆后放入自己的 holdings.json 即可使用。")


if __name__ == "__main__":
    main()
