#!/usr/bin/env python3
"""Stop hook：开发流程还在环节交接处时，阻止 Claude 结束本轮回复。

.claude/dev-flow/STATE 记录当前环节、技术方案路径等，每行一个“键: 值”。
等待用户、阻塞、完成时放行；开发环节里方案未 DONE 时交给 phased-dev-loop 的 hook。
连续多次拦截而 STATE 和方案都没有变化时放行，避免无限循环。
"""
import hashlib
import json
import os
import re
import sys

MAX_STALLED_BLOCKS = 3
STATE_DIR = os.path.join(".claude", "dev-flow")
STATE_LINE_RE = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$")
PLAN_STATUS_RE = re.compile(r"^\**开发状态\**\s*[:：]\s*\**\s*(\w+)", re.MULTILINE)
STOPPABLE_STAGES = {"等待确认", "阻塞", "完成"}

NEXT_STEP = {
    "需求澄清": "继续澄清需求。没有影响整体方向的问题时，直接进入“编写方案”；有的话一次性列给用户，并在 STATE 写 `等待用户: 需求澄清问题`。",
    "编写方案": "按 tech-design 写完方案，把路径填进 STATE 的 `方案`，然后进入“方案自审”。",
    "方案自审": "派子 agent 审方案并处理问题，然后进入“等待确认”：向用户汇报方案，在 STATE 写 `环节: 等待确认` 和 `等待用户: 确认方案`。",
    "开发": "技术方案的开发状态已经是 DONE。在 STATE 写 `环节: 代码审查`，派子 agent 审查 `git diff <基线>`。",
    "代码审查": "处理审查结果（最多两轮），在 STATE 的 `审查` 记下结果，跑全量测试/构建，通过后进入“提交”。",
    "提交": "完成本地提交并整理 PR 描述，向用户汇报后在 STATE 写 `等待用户: 确认推送并创建 PR`，再停下。",
}


def find_project_root(start):
    path = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(path, STATE_DIR, "STATE")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def parse_state(text):
    state = {}
    for line in text.splitlines():
        m = STATE_LINE_RE.match(line)
        if m:
            state[m.group(1)] = m.group(2)
    return state


def plan_status(plan_text):
    if plan_text is None:
        return None
    m = PLAN_STATUS_RE.search(plan_text)
    return m.group(1).upper() if m else None


def stalled_count(root, content):
    """返回 STATE 和方案内容连续未变化的拦截次数（含本次）。"""
    guard_path = os.path.join(root, STATE_DIR, ".stop-guard.json")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        with open(guard_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    count = prev.get("count", 0) + 1 if prev.get("hash") == digest else 1
    with open(guard_path, "w", encoding="utf-8") as f:
        json.dump({"hash": digest, "count": count}, f)
    return count


def should_block(state, status):
    stage = state.get("环节", "")
    if not stage or stage in STOPPABLE_STAGES or state.get("等待用户"):
        return False
    if stage == "开发":
        return status == "DONE"
    return True


def build_reason(stage):
    step = NEXT_STEP.get(stage, f"STATE 里的环节“{stage}”无法识别，按 dev-flow 的环节列表改正后继续。")
    return (
        f"[dev-flow] 当前环节：{stage}。环节之间不要停下，也不要询问用户是否继续。\n"
        f"下一步：{step}\n"
        "只有需要用户回答或决定时，才在 .claude/dev-flow/STATE 写明 `等待用户: <在等什么>` 后停下。"
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    root = find_project_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return
    state_text = read_text(os.path.join(root, STATE_DIR, "STATE")) or ""
    state = parse_state(state_text)
    plan_rel = state.get("方案", "")
    plan_text = read_text(os.path.join(root, plan_rel)) if plan_rel else None
    if not should_block(state, plan_status(plan_text)):
        return

    if stalled_count(root, state_text + "\0" + (plan_text or "")) > MAX_STALLED_BLOCKS:
        print(json.dumps({
            "systemMessage": (
                f"[dev-flow] 连续 {MAX_STALLED_BLOCKS} 次拦截后流程状态和技术方案都没有变化，"
                "已放行停止。请检查 .claude/dev-flow/STATE。"
            )
        }, ensure_ascii=False))
        return

    reason = build_reason(state["环节"])
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
