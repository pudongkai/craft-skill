#!/usr/bin/env python3
"""盯本机 Claude Code 会话：只要它停下不干活（做完、要你决策、出错、等授权、退出），就报出来。

用法：
  watch.py sessions                 列出本机正在运行的 Claude Code 会话（名称、状态、目录）
  watch.py add <名称|会话id|pid>     开始盯一个会话
  watch.py remove <名称|会话id|all>  不再盯
  watch.py list                     正在盯的会话
  watch.py check                    检查所有在盯的会话；没有新情况输出 OK，否则每个会话一段
  watch.py reply-check <名称|会话id>  替主管回话前检查：找到会话，确认 crossSessionInbound 是 accept
  watch.py install                  把 hook 注册进 ~/.claude/settings.json（首次使用时由主管运行一次）
  watch.py uninstall                删掉注册的 hook

数据：~/.claude/sessions/<pid>.json 里有会话名称和 status（busy=在干活，其它=停下），
      ~/.claude/projects/<目录>/<会话id>.jsonl 是会话记录。
状态存在 ../state/watches.json。
"""
import fcntl, glob, json, os, re, sys, time
from datetime import datetime

from hook import EVENTS, NEED_YOU, WAKE_FAILED, pending_agents

HOME = os.path.expanduser("~")
SESS_DIR = os.path.join(HOME, ".claude", "sessions")
PROJ_DIR = os.path.join(HOME, ".claude", "projects")
STATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "watches.json")
STALL_MIN = 10        # 干活中某个操作超过这么久没结果：卡住或在等授权
SETTLE_SEC = 60       # 停下后至少这么久才算真停（避免回合之间的瞬时空闲）
WORKING = ("busy",)   # sessions/<pid>.json 的 status：只有 busy 算在干活；shell = 停着但开着后台 shell（如起了个服务），也算停下
ASK_WORDS = ("确认", "要不要", "是否", "选哪", "你决定", "请你", "等你", "回复", "confirm", "should I", "which")
QUESTION_END = re.compile(r"[？?][\s*_`)）]*$")


HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
HOOK_EVENTS = ("Stop", "Notification", "SessionEnd")
HOOK_TAG = "watch-task/scripts/hook.py"   # 认出哪些 hook 是我们加的
# hook 注册在用户级设置里，所有会话都会触发，hook.py 按 watches.json 里的会话 id 过滤。
# 这样 add 只写本技能的 state/，不用去改被盯项目的文件。
USER_SETTINGS = os.path.join(HOME, ".claude", "settings.json")


def write_json(path, data, indent=2):
    """先写临时文件再改名：Claude Code 也会写 settings.json，避免它读到写了一半的文件。"""
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


def _our_commands(d):
    return [h.get("command", "") for ev in HOOK_EVENTS for g in d.get("hooks", {}).get(ev, [])
            for h in g.get("hooks", []) if HOOK_TAG in h.get("command", "")]


def hook_state():
    """返回 'ok'（已注册且指向本技能目录）、'other'（指向别的副本）、'missing' 或 'unknown'（读不了设置文件）。"""
    try:
        d = json.load(open(USER_SETTINGS)) if os.path.exists(USER_SETTINGS) else {}
    except (OSError, ValueError):
        return "unknown"
    cmds = _our_commands(d)
    if not cmds:
        return "missing"
    return "ok" if len(cmds) == len(HOOK_EVENTS) and all(HOOK in c for c in cmds) else "other"


def cmd_install():
    """把 hook.py 注册进用户级 settings.json，只需做一次；已有我们的旧条目就换成新的。
    命令先检查脚本在不在：本技能目录被删了也不会让每次停下都报错。"""
    d = json.load(open(USER_SETTINGS)) if os.path.exists(USER_SETTINGS) else {}
    hooks = d.setdefault("hooks", {})
    _drop_ours(hooks)
    cmd = f'[ -f "{HOOK}" ] && python3 "{HOOK}"; exit 0'
    for ev in HOOK_EVENTS:
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    write_json(USER_SETTINGS, d)
    print(f"hook 已注册到 {USER_SETTINGS}，指向 {HOOK}")


def cmd_uninstall():
    if not os.path.exists(USER_SETTINGS):
        print("没有注册过")
        return
    d = json.load(open(USER_SETTINGS))
    _drop_ours(d.get("hooks", {}))
    if not d.get("hooks"):
        d.pop("hooks", None)
    write_json(USER_SETTINGS, d)
    print(f"已从 {USER_SETTINGS} 删掉盯任务的 hook")


HOOK_HINT = {
    "missing": "还没注册 hook，它停下时叫不醒我",
    "other": "注册的 hook 指向别的技能副本，它停下时叫不醒我",
    "unknown": "读不到 ~/.claude/settings.json，确认不了 hook 有没有注册",
}
INSTALL_TIP = f'请主管在终端运行一次：python3 "{os.path.abspath(__file__)}" install'


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
    except PermissionError:   # 沙箱里没权限发信号，不代表进程没了
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
    w[name] = {"sessionId": s["sessionId"], "cwd": s.get("cwd"), "since": time.strftime("%F %T"),
               "line": len(rows),
               # 加的时候已经停着的，就当已报过，不立刻重复报
               "stop_key": None if s.get("status") in WORKING else last_words(rows)[0]}
    save(w)
    state = hook_state()
    if state == "ok":
        print(f"开始盯：{name}（{s.get('status')}）{s.get('cwd')}")
    else:
        print(f"开始盯：{name}（{s.get('status')}）{s.get('cwd')}；但{HOOK_HINT[state]}。{INSTALL_TIP}")


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
    return (f"【通知】有 {len(fails)} 次通知没发出去，最近一次（{last.get('name')}）：{short(last.get('error'), 200)}。"
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
            w.pop(name)
    save(w)
    fail = wake_failures()
    if fail:
        out.append(fail)
    print("\n\n".join(out) if out else "OK")


def inbound_setting():
    """用户级设置里的 crossSessionInbound；读不到设置文件时返回 None。"""
    try:
        d = json.load(open(USER_SETTINGS)) if os.path.exists(USER_SETTINGS) else {}
    except (OSError, ValueError):
        return None
    return d.get("crossSessionInbound", "")


INBOUND_TIP = ("请主管在任意一个 Claude Code 会话里打开 /config，把「Messages from your other sessions」改成接受；"
               '或在 ~/.claude/settings.json 里加一行 "crossSessionInbound": "accept"')


def cmd_reply_check(key, w, live):
    """替主管回话前的检查：找到要发给的会话，确认消息不会被拦下等确认。真正发送用 Claude Code 的会话间发消息。"""
    hits = [x for x in live if x.get("sessionId") == (w.get(key) or {}).get("sessionId")] or find(key, live)
    if len(hits) != 1:
        print("找不到唯一的会话"); sys.exit(2)
    s = hits[0]
    print(f"会话：{s.get('name') or '(未命名)'}\t{s.get('status')}\t{s['sessionId']}\t{s.get('cwd')}")
    v = inbound_setting()
    if v == "accept":
        print("crossSessionInbound = accept，发过去的消息会直接交给它")
        return
    if v is None:
        print("读不到 ~/.claude/settings.json，确认不了 crossSessionInbound 的设置；发过去的消息可能被拦下等主管确认。" + INBOUND_TIP)
    else:
        print(f"crossSessionInbound 是「{v or '未设置'}」，不是 accept：对方开着跳过权限确认的模式时，"
              "消息会被拦下等主管在终端里点确认，5 分钟没人点就丢掉。" + INBOUND_TIP)
    sys.exit(3)


def cmd_remove(key, w):
    keys = list(w) if key == "all" else [k for k, v in w.items() if key in (k, v["sessionId"]) or v["sessionId"].startswith(key)]
    for k in keys:
        w.pop(k)
    save(w)
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
        state = hook_state()
        if state != "ok":
            print(f"注意：{HOOK_HINT[state]}。{INSTALL_TIP}")
        if not w:
            print("没有在盯的会话")
        for k, v in w.items():
            s = next((x for x in live if x.get("sessionId") == v["sessionId"]), None)
            print(f"{k}\t{s.get('status') if s else '已退出'}\t从 {v['since']} 开始\t{v.get('cwd')}")
    elif cmd == "check":
        cmd_check(w, live)
    elif cmd == "install":
        cmd_install()
    elif cmd == "uninstall":
        cmd_uninstall()
    elif cmd == "reply-check":
        cmd_reply_check(a[1], w, live)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
