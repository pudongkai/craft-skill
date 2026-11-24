#!/usr/bin/env python3
"""角色会话的 Stop / Notification hook。

由 dispatch 通过 `claude --bg --settings` 只给单个角色会话注册，命令里直接带上项目名和任务单号。
Stop：任务单还在进行中时阻止会话结束，提示用 item done / block / ask-human 收尾。以下情况放行：
- 任务单不存在，或已不是进行中（已收尾的会话不再交给 phased-dev-loop 判断）。
- 开发、修复单的技术方案还在 RUNNING：调用 phased-dev-loop 的 stop_guard.py，它有输出就由它负责；
  它因连续拦截而放行时，补写“会话停下”事件叫醒 PM。
- 本会话还有未结束的后台任务（子 agent 或 Bash 后台命令）：结束后会自动接着干。
- 连续多次拦截而任务单没有任何变化：放行并写“会话停下”事件叫醒 PM，避免无限循环。
Notification：会话要授权或要回答提问时写“会话停下”事件，PM 让人 claude attach 进去处理。
hook 自身出错时一律放行，不能因为脚本问题卡住会话。依赖 phased-dev-loop 装在同一个 skills 目录下。
"""
import argparse
import json
import os
import subprocess
import sys

SKILLS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STOP_GUARD = os.path.join(SKILLS_ROOT, "phased-dev-loop", "scripts", "stop_guard.py")
# 读会话记录和按会话计数的逻辑与 phased-dev-loop 共用，两个 skill 装在同一个 skills 目录下
sys.path.insert(0, os.path.dirname(STOP_GUARD))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_store as bs  # noqa: E402
from stop_guard import read_text, scan_transcript, stalled_count  # noqa: E402

MAX_STALLED_BLOCKS = 3
BOARD_CMD = "python3 ~/.claude/skills/team-pm/scripts/board.py"
NEEDS_HUMAN_TYPES = {"permission_prompt", "agent_needs_input"}
TYPE_KEYS = ("notification_type", "notificationType", "type", "kind")
SUMMARY_LEN = 120


def run_stop_guard(raw):
    """把同一份 hook 输入交给 stop_guard.py，返回它的输出（没有或出错时为空串）。"""
    try:
        proc = subprocess.run([sys.executable, STOP_GUARD], input=raw, capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip()


def load_item(project, item):
    """返回 (任务单, 全文)；任务单不存在或读不到时返回 (None, None)。"""
    try:
        doc = bs.load_item(project, item)
    except (bs.BoardError, OSError, ValueError):
        return None, None
    return doc, read_text(doc.path)


def block_reason(project, no):
    return (
        f"[team] 任务单 {project} {no} 还是进行中，不要就这样停下。\n"
        f"做完了：运行 `{BOARD_CMD} item done {project} {no} --result \"<三五行结论>\"`。\n"
        f"做不下去：等别的角色答复用 `item block {project} {no} --reason ...`，"
        f"需要人决定用 `item ask-human {project} {no} --question ...`。\n"
        "不要在会话里问人——后台会话没人看。"
    )


def decide_stop(payload, project, doc, text):
    """任务单进行中时返回要输出的 hook 结果（dict），放行且无需提示时返回 None。"""
    pending, _ = scan_transcript(payload.get("transcript_path"), [])
    if pending:
        return None
    no = bs.item_number(doc)
    state_dir = os.path.join("projects", project)
    if stalled_count(bs.board_root(), state_dir, payload.get("session_id", ""), text) > MAX_STALLED_BLOCKS:
        bs.append_event(project, no, "会话停下", f"连续 {MAX_STALLED_BLOCKS} 次拦截任务单都没变化")
        return {"systemMessage": (
            f"[team] 连续 {MAX_STALLED_BLOCKS} 次拦截后任务单 {project} {no} 没有任何变化，"
            "已放行停止，并通知 PM 处理。"
        )}
    return {"decision": "block", "reason": block_reason(project, no)}


def forward_stop_guard(raw, project, doc):
    """开发、修复单先交给 stop_guard；它放行停止时补写事件。返回要转发的输出，没有时为空串。"""
    if doc.get("类型") not in bs.CODE_TYPES:
        return ""
    out = run_stop_guard(raw)
    if not out:
        return ""
    try:
        released = '"block"' not in out and "systemMessage" in json.loads(out)
    except ValueError:
        released = False
    if released:
        bs.append_event(project, bs.item_number(doc), "会话停下", "phased-dev-loop 连续拦截后技术方案没有变化，已放行")
    return out


def handle_stop(raw, project, item):
    doc, text = load_item(project, item)
    if doc is None or text is None or doc.get("状态") != "进行中":
        return ""
    forwarded = forward_stop_guard(raw, project, doc)
    if forwarded:
        return forwarded
    result = decide_stop(parse_payload(raw), project, doc, text)
    return json.dumps(result, ensure_ascii=False) if result else ""


def notification_type(payload):
    for key in TYPE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    # 没有类型字段的旧版本只能从提示文字判断是否在等授权
    return "permission_prompt" if "permission" in str(payload.get("message", "")).lower() else ""


def needs_human(kind):
    return kind in NEEDS_HUMAN_TYPES or kind.startswith("elicitation")


def handle_notification(payload, project, item):
    """要授权或要回答提问时写“会话停下”事件，返回是否写了。"""
    kind = notification_type(payload)
    if not needs_human(kind):
        return False
    summary = " ".join(str(payload.get("message", "")).split())[:SUMMARY_LEN] or "无"
    doc, _ = load_item(project, item)
    no = bs.item_number(doc) if doc else item
    session = (doc.get("会话") if doc else "") or payload.get("session_id") or "<会话 id>"
    note = f"会话在等人处理（{kind}）：{summary}。请让人运行 claude attach {session} 接进会话处理"
    bs.append_event(project, no, "会话停下", note)
    return True


def parse_payload(raw):
    try:
        payload = json.loads(raw or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def parse_args(argv):
    p = argparse.ArgumentParser(description="角色会话的 Stop / Notification hook")
    p.add_argument("--event", choices=["stop", "notification"], default="stop")
    p.add_argument("--project", required=True)
    p.add_argument("--item", required=True)
    return p.parse_args(argv)


def main(argv=None):
    try:
        args = parse_args(argv)
    except SystemExit:
        # argparse 出错默认退出码 2，Stop hook 会把它当成拦截
        return
    raw = sys.stdin.read()
    try:
        if args.event == "notification":
            handle_notification(parse_payload(raw), args.project, args.item)
            return
        out = handle_stop(raw, args.project, args.item)
    except Exception as e:  # noqa: BLE001 —— hook 出错只提示，不卡住会话
        print(f"[team] worker_guard 出错，已放行：{e}", file=sys.stderr)
        return
    if out:
        print(out)


if __name__ == "__main__":
    main()
