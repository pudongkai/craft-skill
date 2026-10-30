#!/usr/bin/env python3
"""Claude Code hook（Stop / Notification / SessionEnd）：被盯的会话状态一变，就记进 ../state/events.jsonl，
并用 dws 以主管身份给数字员工发一条钉钉消息把它叫醒（醒来跑 watch.py check 再汇报）。

由 watch.py add 临时注册到被盯会话所在项目的 .claude/settings.local.json，remove 时删掉。
同一项目里没被盯的会话也会触发，这里按 session_id 过滤。必须快、不输出、永远 exit 0，不能影响会话本身：
发消息交给后台子进程（hook.py --send），hook 立即返回。
"""
import fcntl, json, os, re, subprocess, sys, time

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
EVENTS = os.path.join(STATE_DIR, "events.jsonl")
WAKE_LOG = os.path.join(STATE_DIR, "wake.log")
WAKE_FAILED = os.path.join(STATE_DIR, "wake-failed.jsonl")
DWS = os.path.expanduser("~/.local/bin/dws")
SEND_TRIES = 4
RETRY_SEC = 15
# dws 读令牌要拿全机唯一的 ~/.dws/.data.lock，和 Roster 的 dws 接收端抢锁失败时报这些，等一会儿重试即可
LOCK_BUSY = re.compile(r"timeout acquiring token lock|bus child did not signal readiness", re.I)
NOT_SENT = re.compile(r'"executed"\s*:\s*false|"dry_run"\s*:\s*true')
# 需要主管处理的 Notification；idle_prompt（空闲等输入）由 Stop 报，其余（agent_completed、auth_success 等）不报
NEED_YOU = ("permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input")
TASK_DONE = re.compile(r"<task-id>([0-9a-f]+)</task-id>.*?<status>(?:completed|failed|killed|stopped)</status>")


def append_jsonl(path, rec):
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _launched(row):
    """这一行是派出（或恢复）后台子 agent 的工具结果时返回 agent id。正文里打印出来的 agentId 不算。"""
    r = row.get("toolUseResult")
    if not isinstance(r, dict):
        return None
    if r.get("status") == "async_launched":
        return r.get("agentId")
    return r.get("resumedAgentId")


def _finished(row):
    """Claude Code 自己排进队列的完成通知里结束的 agent id。"""
    if row.get("type") == "queue-operation":
        text = row.get("content")
    elif row.get("type") == "attachment":
        text = (row.get("attachment") or {}).get("prompt")
    else:
        return []
    return TASK_DONE.findall(" ".join(text.split())) if isinstance(text, str) else []


def pending_agents(transcript):
    """会话记录里已派出、还没交回结果的后台子 agent 数。Stop 时有这种子 agent 就不算真停，交回结果后会话会自己接着干。"""
    pending = set()
    try:
        for line in open(transcript, encoding="utf-8"):
            if "gentId" not in line and "<task-id>" not in line:   # agentId / resumedAgentId
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            aid = _launched(row)
            if aid:
                pending.add(aid)
            pending.difference_update(_finished(row))
    except (OSError, TypeError):
        return 0
    return len(pending)


def needs_you(ev):
    ntype = ev.get("notification_type")
    if ntype:
        return ntype in NEED_YOU
    return "waiting for your input" not in (ev.get("message") or "")


def wake(name, why):
    """没配 state/config.json 就只记事件；配了就起后台子进程发消息。"""
    if not os.path.exists(os.path.join(STATE_DIR, "config.json")):
        return
    text = f"[盯任务] {name} {why}。运行盯任务技能的 check，把情况汇报给我。"
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--send", name, text],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd="/tmp", start_new_session=True)


def send(name, text):
    """发叫醒消息；抢不到 dws 锁就等一会儿重试。最终没发出去记进 wake-failed.jsonl，check 时报给主管。"""
    cfg = json.load(open(os.path.join(STATE_DIR, "config.json")))
    cmd = [cfg.get("dws") or DWS, "chat", "+messages-send", "--profile", cfg["dws_profile"], "--as", "user",
           "--chat-id", cfg["chat_id"], "--text", text,
           "--idempotency-key", f"watch-{name}-{int(time.time())}", "--yes"]
    env = dict(os.environ, DWS_SHIM_DISABLE="1")
    err = ""
    for i in range(SEND_TRIES):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd="/tmp", env=env, timeout=60)
            out, ok = (r.stdout + r.stderr).strip(), r.returncode == 0
        except (OSError, subprocess.TimeoutExpired) as e:
            out, ok = str(e), False
        with open(WAKE_LOG, "a") as log:
            log.write(f"{time.strftime('%F %T')} 第 {i + 1} 次 {text}\n{out}\n")
        if ok and not NOT_SENT.search(out):
            return
        err = "dws 只做了预演，没有真正发送" if ok else out[-300:]
        if not LOCK_BUSY.search(out):
            break
        time.sleep(RETRY_SEC)
    append_jsonl(WAKE_FAILED, {"t": time.time(), "name": name, "text": text, "error": err})


def main():
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    sid = ev.get("session_id")
    try:
        watches = json.load(open(os.path.join(STATE_DIR, "watches.json")))
    except Exception:
        return
    name = next((k for k, v in watches.items() if v.get("sessionId") == sid), None)
    if not name:
        return
    kind = ev.get("hook_event_name")
    msg = ev.get("message") or ev.get("reason") or ""
    bg = pending_agents(ev.get("transcript_path")) if kind == "Stop" else 0
    append_jsonl(EVENTS, {"t": time.time(), "name": name, "sessionId": sid, "event": kind, "message": msg,
                          "notification_type": ev.get("notification_type"),
                          "last": (ev.get("last_assistant_message") or "")[-800:], "background_tasks": bg})

    if kind == "Stop" and not bg:
        wake(name, "停下了")
    elif kind == "Notification" and needs_you(ev):
        wake(name, "需要你处理：" + msg[:80])
    elif kind == "SessionEnd":
        wake(name, "会话退出了")


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--send"]:
            send(sys.argv[2], sys.argv[3])
        else:
            main()
    except Exception:
        pass
    sys.exit(0)
