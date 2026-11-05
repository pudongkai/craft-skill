#!/usr/bin/env python3
"""按 iCloud 云盘里的登记找到另一台 Mac，并在上面执行接力需要的操作。

  peer.py list                                   列出登记过的机器
  peer.py resolve <机器>                          找到并连通，输出 用户@地址
  peer.py ssh <机器> <命令>                        在对方机器上执行命令
  peer.py copy <机器> <本地文件> <对方目录>          复制文件过去，并核对两边内容一致
  peer.py find-project <机器> <git 远程地址>        在对方机器上找同一个仓库的目录
  peer.py launch <机器> <对方目录> <会话名> <提示词>   在对方的 tmux 里启动 Claude

<机器> 可以是电脑名或主机名的一部分（不区分大小写），如 m16。
"""
import glob
import json
import os
import shlex
import subprocess
import sys
import time

REG_DIR = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/ClaudeHandoff/hosts")
STALE_SECONDS = 15 * 60
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=accept-new"]
PROJECT_ROOTS = "~/project ~/projects ~/code ~/workspace ~/src ~/Developer ~"
REMOTE_PATH = 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"'


class PeerError(Exception):
    pass


def local_host():
    return subprocess.run(["scutil", "--get", "LocalHostName"],
                          capture_output=True, text=True).stdout.strip()


def download_placeholders():
    """iCloud 把文件移出本地时只留下 .<名字>.icloud 占位文件，先触发下载。"""
    pending = glob.glob(os.path.join(REG_DIR, ".*.icloud"))
    for path in pending:
        subprocess.run(["brctl", "download", path], capture_output=True)
    if pending:
        time.sleep(3)


def load_hosts():
    download_placeholders()
    hosts = []
    for path in sorted(glob.glob(os.path.join(REG_DIR, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                hosts.append(json.load(f))
        except (OSError, ValueError):
            continue
    return hosts


def age_text(seconds):
    if seconds < 120:
        return f"{int(seconds)} 秒前"
    if seconds < 7200:
        return f"{int(seconds // 60)} 分钟前"
    return f"{int(seconds // 3600)} 小时前"


def cmd_list(_args):
    me = local_host()
    hosts = load_hosts()
    if not hosts:
        print(f"没有登记记录（{REG_DIR}）。在每台 Mac 上运行 install.sh 登记。")
        return
    for h in hosts:
        tag = "（本机）" if h.get("host") == me else ""
        age = age_text(time.time() - h.get("updated", 0))
        ips = ", ".join(h.get("ips", [])) or "无地址"
        print(f"{h.get('name')}{tag}  主机名 {h.get('host')}  用户 {h.get('user')}  地址 {ips}  更新于 {age}")


def pick_host(keyword):
    me = local_host()
    key = keyword.lower()
    matches = [h for h in load_hosts() if h.get("host") != me
               and (key in h.get("name", "").lower() or key in h.get("host", "").lower())]
    if not matches:
        raise PeerError(f"没有登记过名字包含“{keyword}”的其他机器。运行 peer.py list 查看登记情况。")
    if len(matches) > 1:
        names = "、".join(h.get("name", "") for h in matches)
        raise PeerError(f"“{keyword}”对应多台机器：{names}，请说得具体些。")
    return matches[0]


def ssh_base(h, ip):
    # 主机密钥按主机名记，地址变了也能认出是不是同一台机器
    return ["ssh", *SSH_OPTS, "-o", f"HostKeyAlias={h['host']}", f"{h['user']}@{ip}"]


def resolve(keyword):
    """返回 (登记记录, 可连通的地址)。连上后核对电脑名，避免地址被别的机器占用时连错。"""
    h = pick_host(keyword)
    age = time.time() - h.get("updated", 0)
    tried = []
    for ip in h.get("ips", []):
        r = subprocess.run(ssh_base(h, ip) + ["scutil --get LocalHostName"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == h["host"]:
            return h, ip
        err = r.stderr.strip().splitlines()
        tried.append(f"{ip}：{err[-1] if err else '主机名不符'}")
    hint = f"登记更新于 {age_text(age)}" + ("，已经很久没更新，对方可能在睡眠、关机或换了网络" if age > STALE_SECONDS else "")
    raise PeerError(f"连不上 {h.get('name')}（{hint}）。尝试过：{'；'.join(tried) or '没有登记地址'}")


def run_remote(keyword, command, capture=False):
    h, ip = resolve(keyword)
    remote = f"{REMOTE_PATH}; {command}"
    return subprocess.run(ssh_base(h, ip) + [remote], capture_output=capture, text=True)


def cmd_resolve(args):
    h, ip = resolve(args[0])
    print(f"{h['user']}@{ip}  （{h['name']}，主机名 {h['host']}）")


def cmd_ssh(args):
    sys.exit(run_remote(args[0], " ".join(args[1:])).returncode)


def sha256(path):
    r = subprocess.run(["shasum", "-a", "256", path], capture_output=True, text=True, check=True)
    return r.stdout.split()[0]


def cmd_copy(args):
    keyword, local, remote_dir = args
    h, ip = resolve(keyword)
    remote = remote_dir.rstrip("/") + "/" + os.path.basename(local)
    r = subprocess.run(["scp", "-q", *SSH_OPTS, "-o", f"HostKeyAlias={h['host']}",
                        local, f"{h['user']}@{ip}:{remote}"])
    if r.returncode != 0:
        sys.exit(r.returncode)
    check = subprocess.run(ssh_base(h, ip) + [f"shasum -a 256 {shlex.quote(remote)}"],
                           capture_output=True, text=True)
    if check.stdout.split()[:1] != [sha256(local)]:
        raise PeerError(f"复制后 {h['name']}:{remote} 的内容和本地不一致，不要删除本地文件")
    print(f"已复制到 {h['name']}:{remote}，两边内容一致")


def cmd_find_project(args):
    keyword, url = args
    script = (
        f"for root in {PROJECT_ROOTS}; do "
        f"for d in $root/*/; do [ -d \"$d/.git\" ] || continue; "
        f"[ \"$(git -C \"$d\" remote get-url origin 2>/dev/null)\" = {shlex.quote(url)} ] && echo \"${{d%/}}\"; "
        "done; done 2>/dev/null | awk '!seen[$0]++'")
    r = run_remote(keyword, script, capture=True)
    dirs = [line for line in r.stdout.splitlines() if line.strip()]
    if not dirs:
        print(f"对方机器上没有找到远程地址为 {url} 的仓库")
        sys.exit(1)
    print("\n".join(dirs))


def launch_script(directory, session, prompt):
    claude = "claude " + shlex.quote(prompt)
    return (
        "tmux ls >/dev/null 2>&1 || { echo NO_TMUX_SERVER; exit 3; }; "
        f"tmux has-session -t {shlex.quote(session)} 2>/dev/null && {{ echo SESSION_EXISTS; exit 4; }}; "
        f"[ -f ~/.claude-oauth-env ] && . ~/.claude-oauth-env; "
        f"tmux new-session -d -s {shlex.quote(session)} -c {shlex.quote(directory)} {shlex.quote(claude)} && "
        f"sleep 15 && tmux capture-pane -p -t {shlex.quote(session)} | tail -25")


def cmd_launch(args):
    keyword, directory, session, prompt = args
    r = run_remote(keyword, launch_script(directory, session, prompt), capture=True)
    out = r.stdout
    if "NO_TMUX_SERVER" in out:
        raise PeerError("对方没有正在运行的 tmux。请在对方机器本地的终端里执行一次 `tmux new -d -s main`，"
                        "让 Claude 能读到登录信息，然后再接力。")
    if "SESSION_EXISTS" in out:
        raise PeerError(f"对方已经有名为 {session} 的 tmux 会话，换个会话名。")
    print(out)
    if "Not logged in" in out or "/login" in out:
        print("\n[提示] 对方的 Claude 没有登录，见 SKILL.md 的“首次设置”。")
    elif "trust" in out.lower():
        print("\n[提示] 对方的 Claude 在询问是否信任这个目录，需要在对方机器上确认。")
    sys.exit(r.returncode)


COMMANDS = {
    "list": (cmd_list, 0), "resolve": (cmd_resolve, 1), "ssh": (cmd_ssh, -2),
    "copy": (cmd_copy, 3), "find-project": (cmd_find_project, 2), "launch": (cmd_launch, 4),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    func, nargs = COMMANDS[sys.argv[1]]
    args = sys.argv[2:]
    if (nargs >= 0 and len(args) != nargs) or (nargs < 0 and len(args) < -nargs):
        print(__doc__)
        sys.exit(2)
    try:
        func(args)
    except PeerError as e:
        print(f"[claude-handoff] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
