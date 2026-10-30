#!/usr/bin/env python3
"""盯本机 Claude Code 会话：只要它停下不干活（做完、要你决策、出错、等授权、退出），就报出来。

用法：
  watch.py sessions                 列出本机正在运行的 Claude Code 会话（名称、状态、目录）
  watch.py add <名称|会话id|pid>     开始盯一个会话
  watch.py remove <名称|会话id|all>  不再盯
  watch.py list                     正在盯的会话
  watch.py check                    检查所有在盯的会话；没有新情况输出 OK，否则每个会话一段
  watch.py reply <名称|会话id> <文字>  在会话所在的终端标签里替主管输入一句话并回车（仅 macOS Terminal）

数据：~/.claude/sessions/<pid>.json 里有会话名称和 status（busy=在干活，其它=停下），
      ~/.claude/projects/<目录>/<会话id>.jsonl 是会话记录。
状态存在 ../state/watches.json。
"""
import fcntl, glob, json, os, re, subprocess, sys, time
from datetime import datetime

from hook import EVENTS, NEED_YOU, WAKE_FAILED, pending_agents

HOME = os.path.expanduser("~")
SESS_DIR = os.path.join(HOME, ".claude", "sessions")
PROJ_DIR = os.path.join(HOME, ".claude", "projects")
STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "watches.json")
STALL_MIN = 10        # 干活中某个操作超过这么久没结果：卡住或在等授权
SETTLE_SEC = 60       # 停下后至少这么久才算真停（避免回合之间的瞬时空闲）
WORKING = ("busy",)   # sessions/<pid>.json 的 status：只有 busy 算在干活；shell = 停着但开着后台 shell（如起了个服务），也算停下
REPLY_OK = ("idle", "shell")   # 只在停在输入框时替主管回话；其它状态下回车可能落在授权选择框上，等于替主管点了同意
ASK_WORDS = ("确认", "要不要", "是否", "选哪", "你决定", "请你", "等你", "回复", "confirm", "should I", "which")
QUESTION_END = re.compile(r"[？?][\s*_`)）]*$")


HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
HOOK_EVENTS = ("Stop", "Notification", "SessionEnd")
HOOK_TAG = "watch-task/scripts/hook.py"   # 认出哪些 hook 是我们加的


def _settings(cwd):
    return os.path.join(cwd, ".claude", "settings.local.json")


def write_json(path, data, indent=2):
    """先写临时文件再改名：Claude Code 也会写 settings.local.json，避免它读到写了一半的文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def _drop_ours(hooks):
    """从 hooks 配置里删掉我们加的条目，删空的事件也去掉。"""
    for ev in list(hooks):
        groups = []
        for g in hooks[ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if HOOK_TAG not in h.get("command", "")]
            if g["hooks"]:
                groups.append(g)
        if groups:
            hooks[ev] = groups
        else:
            hooks.pop(ev)


def hook_install(cwd):
    """把 hook.py 临时注册进项目的 settings.local.json；已有我们的旧条目就换成新的。
    命令先检查脚本在不在：本技能目录被删了也不会让被盯项目的每次停下都报错。"""
    p = _settings(cwd)
    d = json.load(open(p)) if os.path.exists(p) else {}
    hooks = d.setdefault("hooks", {})
    _drop_ours(hooks)
    cmd = f'[ -f "{HOOK}" ] && python3 "{HOOK}"; exit 0'
    for ev in HOOK_EVENTS:
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    write_json(p, d)


def hook_uninstall(cwd):
    """删掉我们加的 hook；文件因此变空就整个删掉，别人的设置原样保留。"""
    p = _settings(cwd)
    if not os.path.exists(p):
        return
    d = json.load(open(p))
    _drop_ours(d.get("hooks", {}))
    if not d.get("hooks"):
        d.pop("hooks", None)
    if d:
        write_json(p, d)
    else:
        os.remove(p)


def take_jsonl(path):
    """读走 hook 记下的记录并清空；和 hook 的追加写用同一把文件锁，不丢记录。"""
    if not os.path.exists(path):
        return []
    with open(path, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lines = f.readlines()
        f.truncate(0)
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def load():
    if os.path.exists(STATE):
        return json.load(open(STATE))
    return {}


def save(w):
    write_json(STATE, w, indent=1)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def sessions():
    out = []
    for f in glob.glob(os.path.join(SESS_DIR, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("pid") and alive(d["pid"]) and d.get("sessionId"):
            out.append(d)
    return out


def find(key, pool):
    key = str(key)
    hits = [s for s in pool if key in (str(s.get("pid")), s.get("sessionId"), s.get("name"))]
    if not hits:
        hits = [s for s in pool if key in (s.get("name") or "") or (s.get("sessionId") or "").startswith(key)]
    return hits


def log_path(sid, cwd=None):
    if cwd:
        p = os.path.join(PROJ_DIR, cwd.replace("/", "-").replace(" ", "-").replace(".", "-"), sid + ".jsonl")
        if os.path.exists(p):
            return p
    g = glob.glob(os.path.join(PROJ_DIR, "*", sid + ".jsonl"))
    return g[0] if g else None


def ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""


def short(s, n=200):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "…"


def read_rows(path):
    rows = []
    for l in open(path):
        try:
            rows.append(json.loads(l))
        except Exception:
            pass
    return rows


def last_words(rows):
    for r in reversed(rows):
        if r.get("type") == "assistant":
            t = text_of((r.get("message") or {}).get("content"))
            if t.strip():
                return r.get("uuid"), t
        if r.get("type") == "user" and isinstance((r.get("message") or {}).get("content"), str):
            break
    return None, ""


def asks_you(words):
    """最后几行里有以问号结尾的句子或请主管拿主意的说法。"""
    tail = [l.strip() for l in words.strip().splitlines() if l.strip()][-3:]
    return any(QUESTION_END.search(l) or any(k in l for k in ASK_WORDS) for l in tail)


def stalled_ops(rows, w):
    """干活中超过 STALL_MIN 分钟没结果的操作（每个只报一次）。"""
    pending = {}
    for r in rows:
        c = (r.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                inp = b.get("input") or {}
                pending[b["id"]] = (ts(r.get("timestamp")), inp.get("description") or inp.get("command") or b.get("name"))
            elif b.get("type") == "tool_result":
                pending.pop(b.get("tool_use_id"), None)
    notes = []
    for tid, (t0, desc) in pending.items():
        if t0 and time.time() - t0 > STALL_MIN * 60 and tid not in w.get("stalls", []):
            notes.append(f"有个操作 {int((time.time() - t0) / 60)} 分钟没结果，可能卡住或在等你授权：{short(desc, 120)}")
            w.setdefault("stalls", []).append(tid)
    return notes


def stop_note(rows, s, w, had_error):
    """停下了的报告；同一次停下只报一次。"""
    uid, words = last_words(rows)
    key = uid or str(s.get("statusUpdatedAt"))
    if w.get("stop_key") == key:
        return None
    w["stop_key"] = key
    kind = "出错后停下了" if had_error else ("要你决策/回复" if asks_you(words) else "停下了（做完了或暂停）")
    mins = int((time.time() - (s.get("statusUpdatedAt") or 0) / 1000) / 60)
    return f"{kind}，已停 {mins} 分钟。它最后说：{short(words, 600) or '（没有文字）'}"


def check_one(name, w, live, stop_t=None):
    """返回 (报告文字或 None, 是否结束盯梢)。stop_t 是 hook 报的最近一次真停的时间。"""
    sid = w["sessionId"]
    s = next((x for x in live if x.get("sessionId") == sid), None)
    if not s:
        return f"会话已经退出（终端里的 Claude Code 关掉了），我停止盯它。", True
    path = log_path(sid, s.get("cwd"))
    rows = read_rows(path) if path else []
    new = rows[w.get("line", 0):]
    w["line"] = len(rows)
    notes = []

    for r in new:
        if r.get("isApiErrorMessage") or (r.get("type") == "system" and r.get("level") == "error"):
            notes.append("API/系统错误：" + short(text_of((r.get("message") or {}).get("content")) or r.get("content", "")))

    changed = (s.get("statusUpdatedAt") or 0) / 1000
    # hook 报停之后状态又变回 busy = 它已经接着干了；状态文件还没来得及更新的，以 hook 为准
    stopped = s.get("status") not in WORKING or (stop_t is not None and stop_t >= changed)
    if not stopped or (path and pending_agents(path)):   # 等后台子 agent 交回结果的，不算真停
        w["stop_key"] = None
        notes += stalled_ops(rows, w)
    elif stop_t is not None or time.time() - changed >= SETTLE_SEC:
        note = stop_note(rows, s, w, bool(notes))
        if note:
            notes.append(note)

    if not notes:
        return None, False
    return "\n".join(dict.fromkeys(notes)), False


def cmd_add(key, w, live):
    hits = find(key, live)
    if len(hits) != 1:
        print("找不到" if not hits else "匹配到多个：\n" + "\n".join(f"{h.get('name')} {h['sessionId']} {h.get('cwd')}" for h in hits))
        sys.exit(2)
    s = hits[0]
    name = s.get("name") or s["sessionId"][:8]
    path = log_path(s["sessionId"], s.get("cwd"))
    rows = read_rows(path) if path else []
    hook_install(s.get("cwd"))   # 先注册 hook，成功了再记成在盯，避免记了却没 hook
    w[name] = {"sessionId": s["sessionId"], "cwd": s.get("cwd"), "since": time.strftime("%F %T"),
               "line": len(rows),
               # 加的时候已经停着的，就当已报过，不立刻重复报
               "stop_key": None if s.get("status") in WORKING else last_words(rows)[0]}
    save(w)
    print(f"开始盯：{name}（{s.get('status')}）{s.get('cwd')}；hook 已注册")


def hook_hints(rep, evs):
    """把 hook 报的授权、提问等提示补进报告。"""
    for e in evs:
        if e.get("event") != "Notification" or not e.get("message"):
            continue
        ntype = e.get("notification_type")
        if ntype and ntype not in NEED_YOU:
            continue
        if not ntype and "waiting for your input" in e["message"]:   # 等输入已由「停下了」覆盖
            continue
        hint = "hook：" + short(e["message"], 120)
        rep = f"{rep}\n{hint}" if rep else hint
    return rep


def wake_failures():
    fails = take_jsonl(WAKE_FAILED)
    if not fails:
        return None
    last = fails[-1]
    return (f"【叫醒】有 {len(fails)} 次叫醒消息没发出去，最近一次（{last.get('name')}）：{short(last.get('error'), 200)}。"
            "没登录或鉴权失败的话，请主管重新 dws auth login")


def cmd_check(w, live):
    out = []
    evs = {}
    for e in take_jsonl(EVENTS):
        evs.setdefault(e.get("name"), []).append(e)
    for name in list(w):
        stops = [e["t"] for e in evs.get(name, []) if e.get("event") == "Stop" and not e.get("background_tasks")]
        rep, done = check_one(name, w[name], live, max(stops) if stops else None)
        rep = hook_hints(rep, evs.get(name, []))
        if rep:
            out.append(f"【{name}】{rep}")
        if done:
            cwd = w.pop(name).get("cwd")
            if cwd and not any(v.get("cwd") == cwd for v in w.values()):
                hook_uninstall(cwd)
    save(w)
    fail = wake_failures()
    if fail:
        out.append(fail)
    print("\n\n".join(out) if out else "OK")


def cmd_reply(key, words, w, live):
    hits = [x for x in live if x.get("sessionId") == (w.get(key) or {}).get("sessionId")] or find(key, live)
    if len(hits) != 1:
        print("找不到唯一的会话"); sys.exit(2)
    s = hits[0]
    if s.get("status") not in REPLY_OK:
        print(f"它现在是 {s.get('status')}，不在等输入（可能在干活或停在授权选择上），这时回车可能替你点了同意，没有发送"); sys.exit(2)
    tty = subprocess.run(["ps", "-o", "tty=", "-p", str(s["pid"])], capture_output=True, text=True).stdout.strip()
    if not tty or "?" in tty:
        print("这个会话不在终端里运行，没法替你输入"); sys.exit(2)
    text = " ".join(" ".join(words).split()).replace("\\", "\\\\").replace('"', '\\"')
    script = f'''tell application "Terminal"
repeat with w in windows
repeat with t in tabs of w
if tty of t is "/dev/{tty}" then
do script "{text}" in t
delay 1
do script "" in t
return "sent"
end if
end repeat
end repeat
return "not found"
end tell'''
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())


def cmd_remove(key, w):
    keys = list(w) if key == "all" else [k for k, v in w.items() if key in (k, v["sessionId"]) or v["sessionId"].startswith(key)]
    gone = {w.pop(k).get("cwd") for k in keys}
    save(w)
    for cwd in gone:
        if cwd and not any(v.get("cwd") == cwd for v in w.values()):
            hook_uninstall(cwd)
    print("已停止盯：" + ("、".join(keys) or "（没有匹配的）") + f"；还在盯 {len(w)} 个")


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "check"
    w = load()
    live = sessions()

    if cmd == "sessions":
        for s in sorted(live, key=lambda x: -(x.get("updatedAt") or 0)):
            print(f"{s.get('name') or '(未命名)'}\t{s.get('status')}\tpid={s['pid']}\t{s['sessionId']}\t{s.get('cwd')}")
    elif cmd == "add":
        cmd_add(a[1], w, live)
    elif cmd == "remove":
        cmd_remove(a[1], w)
    elif cmd == "list":
        if not w:
            print("没有在盯的会话")
        for k, v in w.items():
            s = next((x for x in live if x.get("sessionId") == v["sessionId"]), None)
            print(f"{k}\t{s.get('status') if s else '已退出'}\t从 {v['since']} 开始\t{v.get('cwd')}")
    elif cmd == "check":
        cmd_check(w, live)
    elif cmd == "reply":
        cmd_reply(a[1], a[2:], w, live)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
