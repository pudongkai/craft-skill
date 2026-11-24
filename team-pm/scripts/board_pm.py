"""PM 会话登记、项目导出导入：board.py 的 pm / project export / project import 子命令。

同一个仓库的项目由同一个 PM 会话管。~/.claude/team/pm.json 记录 仓库 → PM 会话所在的终端（tty 或 tmux 会话名），
接力时先找对方机器上负责这个仓库、还在运行的 PM 会话，有就把交接消息发给它，没有才新开。
"""
import json
import os
import shutil
import subprocess
import tarfile
import tempfile

import board_store as bs

RESET_STATUSES = {"进行中"}


# ---------- PM 会话登记 ----------

def pm_path():
    return os.path.join(bs.board_root(), "pm.json")


def load_pm():
    try:
        with open(pm_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def repo_key(repo):
    return os.path.realpath(os.path.expanduser(repo))


def process_info(pid):
    """返回 (父进程 pid, tty, 命令名)；进程不存在时返回 None。"""
    out = subprocess.run(["ps", "-o", "ppid=,tty=,comm=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.split(None, 2)
    if len(out) < 3:
        return None
    return int(out[0]), out[1], os.path.basename(out[2].strip())


def current_claude_terminal():
    """沿父进程往上找到运行本命令的 claude 进程，返回它的终端（/dev/ttys…）；找不到返回 None。"""
    pid = os.getppid()
    for _ in range(12):
        info = process_info(pid)
        if not info:
            return None
        ppid, tty, comm = info
        if comm == "claude" and tty not in ("", "??", "-"):
            return tty if tty.startswith("/dev/") else f"/dev/{tty}"
        if ppid <= 1:
            return None
        pid = ppid
    return None


def terminal_alive(terminal):
    """终端里还有 claude 在运行：/dev/ttys… 看这个 tty 上的进程，其余当作 tmux 会话名。"""
    if terminal.startswith("/dev/"):
        out = subprocess.run(["ps", "-t", terminal[len("/dev/"):], "-o", "comm="],
                             capture_output=True, text=True).stdout
        return any(os.path.basename(c.strip()) == "claude" for c in out.splitlines())
    cmd = ["tmux", "list-panes", "-t", terminal, "-F", "#{pane_current_command}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return False
    return "claude" in out


def register_pm(repo, terminal):
    with bs.file_lock(".pm.lock"):
        data = load_pm()
        data[repo_key(repo)] = {"终端": terminal, "登记时间": bs.now()}
        with open(pm_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def find_pm(repo):
    """负责这个仓库、还在运行的 PM 会话的终端；没有返回 None。"""
    entry = load_pm().get(repo_key(repo))
    if entry and terminal_alive(entry.get("终端", "")):
        return entry["终端"]
    return None


def register_current_pm(repo):
    """立项时调用：把当前 PM 会话登记为这个仓库的负责人。找不到当前终端时不登记，返回终端或 None。"""
    terminal = current_claude_terminal()
    if terminal:
        register_pm(repo, terminal)
    return terminal


# ---------- 项目导出导入 ----------

def export_project(name, out_path):
    """把项目卡和任务单打包，用于交给另一台机器。打包前项目不能有进行中的单。"""
    running = [bs.item_number(d) for d in bs.list_items(name) if d.get("状态") == "进行中"]
    if running:
        raise bs.BoardError(f"{name} 还有进行中的单：{'、'.join(running)}，等它们收尾或停掉后再导出")
    with tarfile.open(out_path, "w:gz") as tar:
        tar.add(bs.project_dir(name), arcname=name,
                filter=lambda t: None if os.path.basename(t.name).startswith(".") else t)
    return out_path


def import_project(archive, repo):
    """导入别的机器导出的项目：仓库和工作目录换成本机路径，进行中的单改回待领取。返回项目名。"""
    repo = os.path.abspath(os.path.expanduser(repo))
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(archive) as tar:
            tar.extractall(tmp, filter="data")
        names = os.listdir(tmp)
        if len(names) != 1 or not os.path.isfile(os.path.join(tmp, names[0], "project.md")):
            raise bs.BoardError(f"{archive} 不是 board.py project export 导出的文件")
        name = names[0]
        with bs.board_lock():
            if os.path.exists(bs.project_path(name)):
                raise bs.BoardError(f"本机看板已有项目 {name}")
            os.makedirs(os.path.dirname(bs.project_dir(name)), exist_ok=True)
            shutil.copytree(os.path.join(tmp, name), bs.project_dir(name))
    localize_project(name, repo)
    return name


def localize_project(name, repo):
    doc = bs.load_project(name)
    doc.set("仓库", repo)
    doc.set("工作目录", os.path.join(repo, ".claude", "worktrees", name))
    doc.append_to_section("记录", f"- {bs.today()} 从别的机器接手")
    doc.save()
    for item in bs.list_items(name):
        if item.get("状态") in RESET_STATUSES:
            bs.update_item(name, bs.item_number(item), {"状态": "待领取", "会话": ""})


# ---------- 命令行 ----------

def cmd_pm(a):
    if a.action == "register":
        terminal = a.terminal or current_claude_terminal()
        if not terminal:
            raise bs.BoardError("找不到当前 PM 会话所在的终端，用 --terminal 指定")
        register_pm(a.repo, terminal)
        print(f"{repo_key(a.repo)} 的 PM 会话：{terminal}")
    elif a.action == "find":
        terminal = find_pm(a.repo)
        print(terminal or "没有")


def register(sub, project_actions, commands):
    x = sub.add_parser("pm", help="登记、查找负责某个仓库的 PM 会话").add_subparsers(dest="action", required=True)
    y = x.add_parser("register", help="登记 PM 会话；不写 --terminal 时取当前会话所在的终端")
    y.add_argument("--repo", required=True)
    y.add_argument("--terminal", default="", help="/dev/ttys… 或 tmux 会话名")
    x.add_parser("find", help="打印负责这个仓库、还在运行的 PM 会话的终端，没有时打印“没有”").add_argument(
        "--repo", required=True)
    y = project_actions.add_parser("export", help="打包项目卡和任务单，交给别的机器")
    y.add_argument("name")
    y.add_argument("--out", required=True)
    y = project_actions.add_parser("import", help="导入别的机器导出的项目")
    y.add_argument("archive")
    y.add_argument("--repo", required=True, help="本机上这个项目的仓库目录")
    commands["pm"] = cmd_pm
