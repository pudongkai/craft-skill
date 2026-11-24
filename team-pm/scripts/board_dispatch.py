"""派单、等待事件、回收会话：board.py 的 dispatch / wait / reap 子命令。

角色会话用 `claude --bg` 在项目 worktree 里启动，一张任务单一个会话。
会话的 Stop / Notification hook 通过 `--settings` 只注册给这个会话，命令里带上项目名和单号。
"""
import json
import os
import re
import subprocess
import time

import board_store as bs

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
PROMPT_FILE = os.path.join(SKILL_DIR, "references", "dispatch-prompt.md")
PROTOCOL_FILE = os.path.join(SKILL_DIR, "references", "protocol.md")
ROLE_SKILLS = {"产品": "team-product", "开发": "team-dev", "QA": "team-qa"}
MAX_RETRIES = 2
BG_ID_RE = re.compile(r"backgrounded\s*·\s*([0-9a-f]+)")
FINISHED_STATUSES = {"完成", "阻塞", "待人确认", "取消"}
# 刚启动的会话在 claude agents 里短时间没有 pid，只有 state
STARTING_STATES = {"working", "starting", "queued"}
DISPATCH_GRACE_SECONDS = 90


def run(cmd, cwd=None, timeout=120, stdout_only=False):
    """运行命令，返回 (退出码, 输出)；命令不存在或超时转成 BoardError。"""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise bs.BoardError(f"找不到命令 {cmd[0]}，检查 PATH")
    except subprocess.TimeoutExpired:
        raise bs.BoardError(f"{' '.join(cmd[:3])} 超过 {timeout} 秒没有返回")
    out = p.stdout or ""
    return p.returncode, out if stdout_only else out + (p.stderr or "")


# ---------- worktree ----------

def exclude_worktrees(repo):
    """把 .claude/worktrees/ 写进仓库本地的 info/exclude，主工作区里不显示成未跟踪目录。"""
    code, git_dir = run(["git", "-C", repo, "rev-parse", "--git-common-dir"])
    if code != 0:
        return
    path = os.path.join(repo, git_dir.strip(), "info", "exclude")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = bs.read_file(path) if os.path.exists(path) else ""
    if ".claude/worktrees/" not in text.split():
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n.claude/worktrees/\n")


def ensure_worktree(name):
    """项目工作目录不存在时建 worktree 和分支，并在项目卡记下基线提交。"""
    p = bs.load_project(name)
    repo, workdir, branch = p.get("仓库"), p.get("工作目录"), p.get("分支")
    if os.path.isdir(workdir):
        return workdir
    exclude_worktrees(repo)
    code, _ = run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
    if code == 0:
        cmd = ["git", "-C", repo, "worktree", "add", workdir, branch]
    else:
        cmd = ["git", "-C", repo, "worktree", "add", "-b", branch, workdir, "HEAD"]
    code, out = run(cmd)
    if code != 0:
        raise bs.BoardError(f"建 worktree 失败：{out.strip()}")
    _, head = run(["git", "-C", workdir, "rev-parse", "--short", "HEAD"])
    bs.set_project_field(name, "基线", head.strip())
    return workdir


def repo_trusted(repo):
    """仓库是否已在 Claude Code 里接受过信任提示；没接受过的目录不能启动后台会话。读不到配置时按已信任处理。"""
    try:
        with open(os.path.expanduser("~/.claude.json"), encoding="utf-8") as f:
            projects = json.load(f).get("projects") or {}
    except (OSError, ValueError):
        return True
    return bool((projects.get(repo) or {}).get("hasTrustDialogAccepted"))


# ---------- 会话 ----------

def list_sessions():
    """claude agents 列出的会话，按短 id 索引（含已结束的后台会话）。"""
    code, out = run(["claude", "agents", "--json", "--all"], timeout=30, stdout_only=True)
    if code != 0:
        raise bs.BoardError(f"claude agents 运行失败：{out.strip()[:200]}")
    start = out.find("\n[") + 1 if not out.lstrip().startswith("[") else 0
    try:
        rows = json.loads(out[start:])
    except ValueError:
        raise bs.BoardError(f"claude agents 的输出不是 JSON：{out.strip()[:200]}")
    return {r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def session_alive(row):
    if not row:
        return False
    return pid_alive(row.get("pid")) if row.get("pid") else row.get("state") in STARTING_STATES


def recently_dispatched(item):
    try:
        t = time.mktime(time.strptime(item.get("派出时间"), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return False
    return time.time() - t < DISPATCH_GRACE_SECONDS


def stop_session(short_id):
    run(["claude", "stop", short_id], timeout=30)


def user_theme():
    """主会话的主题。auto 时按 macOS 深浅色模式换算成具体主题：后台会话没有终端，自己检测不到。"""
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as f:
            theme = json.load(f).get("theme") or "auto"
    except (OSError, ValueError):
        theme = "auto"
    if theme != "auto":
        return theme
    try:
        code, out = run(["defaults", "read", "-g", "AppleInterfaceStyle"], timeout=5, stdout_only=True)
    except bs.BoardError:
        return None
    return "dark" if code == 0 and "Dark" in out else "light"


def session_settings(project, number):
    """角色会话的 --settings：Stop / Notification hook，以及和主会话一致的主题。"""
    guard = os.path.join(SCRIPTS_DIR, "worker_guard.py")

    def hook(extra=""):
        cmd = f'python3 "{guard}"{extra} --project {project} --item {number}'
        return [{"hooks": [{"type": "command", "command": cmd, "timeout": 30}]}]

    settings = {"hooks": {"Stop": hook(), "Notification": hook(" --event notification")}}
    theme = bs.load_config().get("theme") or user_theme()
    if theme:
        settings["theme"] = theme
    return json.dumps(settings, ensure_ascii=False)


# ---------- 提示词 ----------

def load_prompt(kind):
    with open(PROMPT_FILE, encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"<!-- prompt:{kind} -->\n(.*?)<!-- /prompt:{kind} -->", text, re.S)
    if not m:
        raise bs.BoardError(f"{PROMPT_FILE} 里没有 prompt:{kind} 模板")
    return m.group(1).strip()


def render_prompt(kind, project_doc, item_doc):
    role = item_doc.get("角色")
    return load_prompt(kind).format(
        project=project_doc.get("项目"), number=bs.item_number(item_doc), role=role,
        type=item_doc.get("类型"), name=bs.item_name(item_doc), skill=ROLE_SKILLS[role],
        workdir=project_doc.get("工作目录"), branch=project_doc.get("分支"),
        board=os.path.join(SCRIPTS_DIR, "board.py"), protocol=PROTOCOL_FILE)


# ---------- dispatch ----------

def check_dispatchable(project, item, retry):
    """返回本次是否算一次重试；不能派时抛错。重试满次数时把单转成阻塞。"""
    status, role, no = item.get("状态"), item.get("角色"), bs.item_number(item)
    if role not in ROLE_SKILLS:
        raise bs.BoardError(f"{no} 的角色是 {role}，不派会话")
    if status == "待领取":
        return False
    if status == "进行中" and retry:
        if int(item.get("重试") or 0) >= MAX_RETRIES:
            reason = f"会话重试 {MAX_RETRIES} 次仍没完成，需要人看一下"
            bs.update_item(project, no, {"状态": "阻塞"}, reason, "阻塞", reason)
            raise bs.BoardError(f"{no} 已重试 {MAX_RETRIES} 次，已转为阻塞，请交给人处理")
        return True
    hint = "；会话已退出要重派时加 --retry" if status == "进行中" else ""
    raise bs.BoardError(f"{bs.item_number(item)} 状态是 {status}，不能派{hint}")


def launch_command(project_doc, item, prompt):
    cfg = bs.load_config()
    role = item.get("角色")
    cmd = ["claude", "--bg", "-n", f"team-{project_doc.get('项目')}-{bs.item_number(item)}",
           "--permission-mode", cfg["permission_mode"],
           "--settings", session_settings(project_doc.get("项目"), bs.item_number(item))]
    effort = (cfg.get("effort") or {}).get(role)
    model = (cfg.get("model") or {}).get(role)
    if effort:
        cmd += ["--effort", effort]
    if model:
        cmd += ["--model", model]
    if cfg.get("autocompact"):
        cmd += ["--autocompact", str(cfg["autocompact"])]
    return cmd + [prompt]


def stop_previous_session(item):
    """这张单以前派过时停掉旧会话（还开着的话），返回是否派过。
    每次派单都开新会话，上下文干净，接着做靠任务单和工作目录。"""
    sid = item.get("会话")
    if not sid:
        return False
    if session_alive(list_sessions().get(sid)):
        stop_session(sid)
    return True


def dispatch(project, number, retry=False):
    project_doc = bs.load_project(project)
    item = bs.load_item(project, number)
    is_retry = check_dispatchable(project, item, retry)
    workdir = ensure_worktree(project)
    continued = stop_previous_session(item)
    prompt = render_prompt("new", project_doc, item)
    if continued:
        prompt += "\n\n" + load_prompt("continue")
    code, out = run(launch_command(project_doc, item, prompt), cwd=workdir, timeout=120)
    m = BG_ID_RE.search(out)
    if code != 0 or not m:
        if "not trusted" in out:
            raise bs.BoardError(f"仓库没有被信任，请人在 {project_doc.get('仓库')} 运行一次 claude 并接受信任提示后再派")
        raise bs.BoardError(f"启动会话失败：{out.strip()[:300]}")
    fields = {"状态": "进行中", "会话": m.group(1), "派出时间": bs.now()}
    if is_retry:
        fields["重试"] = str(int(item.get("重试") or 0) + 1)
    bs.update_item(project, number, fields)
    return m.group(1), continued


# ---------- reap / wait ----------

def reap_state_path():
    return os.path.join(bs.board_root(), ".reap.json")


def load_reported():
    try:
        with open(reap_state_path(), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def save_reported(reported):
    with open(reap_state_path(), "w", encoding="utf-8") as f:
        json.dump(sorted(reported), f, ensure_ascii=False)


def reap():
    """回收会话：进行中但会话已不在的单记“会话退出”；已收尾的单停掉还开着的会话。返回处理记录。

    每次派出（会话 + 派出时间）只报一次退出；记录只保留仍在进行中的单，重派后会重新计。
    """
    sessions = list_sessions()
    with bs.file_lock(".reap.lock"):
        reported, keep, notes = load_reported(), set(), []
        for name in bs.project_names():
            for item in bs.list_items(name):
                note = reap_item(name, item, sessions.get(item.get("会话")), reported, keep)
                if note:
                    notes.append(note)
        save_reported(keep)
    return notes


def dispatch_key(name, item):
    """一次派出的标识：同一张单每次派出（会话 + 派出时间）只报一次停下或退出。"""
    return f"{name}/{bs.item_number(item)}/{item.get('会话')}/{item.get('派出时间')}"


def mark_reported(name, item):
    """hook 已经为这次派出写过“会话停下”时调用，reap 不再为同一次停机写“会话退出”。"""
    with bs.file_lock(".reap.lock"):
        reported = load_reported()
        reported.add(dispatch_key(name, item))
        save_reported(reported)


def reap_item(name, item, row, reported, keep):
    sid, status, no = item.get("会话"), item.get("状态"), bs.item_number(item)
    if not sid:
        return None
    if status in FINISHED_STATUSES and session_alive(row):
        stop_session(sid)
        return f"{name} {no} 已{status}，停掉会话 {sid}"
    if status != "进行中":
        return None
    key = dispatch_key(name, item)
    if key in reported:
        keep.add(key)
        return None
    if session_alive(row) or recently_dispatched(item):
        return None
    keep.add(key)
    bs.append_event(name, no, "会话退出", f"会话 {sid} 已不在运行，任务单还是进行中")
    return f"{name} {no} 会话 {sid} 已退出"


def cursor_path():
    return os.path.join(bs.board_root(), ".wait-cursor")


def read_cursor():
    try:
        with open(cursor_path(), encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def wait(since=None, timeout=3600, reap_interval=60, poll=3):
    """阻塞到有新事件再返回事件列表；期间定时 reap，超时返回空列表。"""
    since = read_cursor() if since is None else since
    deadline, next_reap = time.time() + timeout, 0
    while time.time() < deadline:
        if time.time() >= next_reap:
            try:
                reap()
            except (bs.BoardError, OSError, ValueError):
                pass  # 回收失败不影响等事件，下次再试
            next_reap = time.time() + reap_interval
        events = bs.read_events(since)
        if events:
            with open(cursor_path(), "w", encoding="utf-8") as f:
                f.write(str(events[-1]["序号"]))
            return events
        time.sleep(poll)
    return []


# ---------- 命令行 ----------

def cmd_dispatch(a):
    sid, continued = dispatch(a.project, a.number, a.retry)
    how = "新会话，接着之前的进度" if continued else "新会话"
    print(f"已派出 {a.project} {bs.format_no(a.number)}（{how}）：会话 {sid}；需要时 claude attach {sid}")


def cmd_reap(a):
    for note in reap() or ["没有要回收的会话"]:
        print(note)


def cmd_wait(a):
    from board import print_event  # board 导入本模块，放在函数里避免循环导入
    events = wait(a.since, a.timeout)
    for e in events:
        print_event(e)
    if not events:
        print(f"{a.timeout} 秒内没有新事件")


def register(sub, commands):
    x = sub.add_parser("dispatch", help="启动角色会话处理一张任务单")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--retry", action="store_true", help="单还在进行中但会话已退出时重派")
    sub.add_parser("reap", help="回收会话：报告退出的、停掉已收尾的")
    x = sub.add_parser("wait", help="等到有新事件再退出（PM 放在后台运行）")
    x.add_argument("--since", type=int, default=None, help="默认从上次 wait 读到的位置之后开始")
    x.add_argument("--timeout", type=int, default=3600)
    commands.update({"dispatch": cmd_dispatch, "reap": cmd_reap, "wait": cmd_wait})
