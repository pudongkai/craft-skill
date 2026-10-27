#!/usr/bin/env python3
"""Stop hook：技术方案里还有未完成阶段时，阻止 Claude 结束本轮回复。

.claude/dev-loop/ACTIVE 记录当前开发的技术方案文档路径。
只在开发状态为 RUNNING 时拦截；BLOCKED / DONE 或没有 ACTIVE 时放行。
连续多次拦截而文档没有任何变化时放行，避免 Claude 卡住后无限循环。
"""
import hashlib
import json
import os
import re
import sys

MAX_STALLED_BLOCKS = 3
STATE_DIR = os.path.join(".claude", "dev-loop")
# 阶段写成顶层列表项（- [ ] 阶段1：…），缩进的子项不算阶段
PHASE_RE = re.compile(r"^(?:[-*]|\d+\.)\s+\[([ ~xX!])\]\s+(.+)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
PLAN_SECTION_RE = re.compile(r"开发计划|实施计划|开发阶段|阶段划分|实施步骤")
STATUS_RE = re.compile(r"^\**开发状态\**\s*[:：]\s*\**\s*(\w+)", re.MULTILINE)


def find_project_root(start):
    path = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(path, STATE_DIR, "ACTIVE")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def load_plan(root):
    with open(os.path.join(root, STATE_DIR, "ACTIVE"), encoding="utf-8") as f:
        rel = f.read().strip()
    if not rel:
        return None, None
    plan_path = os.path.join(root, rel)
    if not os.path.isfile(plan_path):
        return plan_path, None
    with open(plan_path, encoding="utf-8") as f:
        return plan_path, f.read()


def plan_section(lines):
    """返回开发计划章节的行；文档里没有这类章节时返回全文。"""
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and PLAN_SECTION_RE.search(m.group(2)):
            level = len(m.group(1))
            for j in range(i + 1, len(lines)):
                h = HEADING_RE.match(lines[j])
                if h and len(h.group(1)) <= level:
                    return lines[i + 1:j]
            return lines[i + 1:]
    return lines


def parse_phases(text):
    phases = {" ": [], "~": [], "x": [], "!": []}
    for line in plan_section(text.splitlines()):
        m = PHASE_RE.match(line)
        if m:
            phases[m.group(1).lower()].append(m.group(2).strip())
    return phases


def stalled_count(root, text):
    """返回文档内容连续未变化的拦截次数（含本次）。"""
    guard_path = os.path.join(root, STATE_DIR, ".stop-guard.json")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        with open(guard_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = {}
    count = prev.get("count", 0) + 1 if prev.get("hash") == digest else 1
    with open(guard_path, "w", encoding="utf-8") as f:
        json.dump({"hash": digest, "count": count}, f)
    return count


def build_reason(rel, phases):
    if phases["~"] or phases[" "]:
        current = (phases["~"] or phases[" "])[0]
        remaining = len(phases["~"]) + len(phases[" "])
        return (
            f"[phased-dev-loop] 还有 {remaining} 个阶段未完成，不要停下，也不要询问用户是否继续。\n"
            f"下一步：{current}\n"
            f"1. 如果刚完成的阶段还没在技术方案 {rel} 里标成 [x] 并写验证记录，先补上。\n"
            f"2. 重新读 {rel} 的开发计划，按 phased-dev-loop 的单轮流程继续下一个阶段。\n"
            "3. 只有遇到必须由用户决定的问题时，才把开发状态改成 BLOCKED 并写清阻塞原因后停下。"
        )
    if phases["!"]:
        return (
            f"[phased-dev-loop] 阶段 {phases['!'][0]} 标记为卡住，但开发状态仍是 RUNNING。\n"
            f"要么继续解决它；要么在 {rel} 把开发状态改成 BLOCKED，在该阶段的子项里写清需要用户决定什么，然后再停下。"
        )
    return (
        f"[phased-dev-loop] 所有阶段都已勾选，但开发状态仍是 RUNNING。\n"
        f"执行收尾：跑一遍全量测试/构建，对照技术方案检查是否有遗漏，"
        f"通过后把 {rel} 的开发状态改成 DONE，再向用户汇报。"
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    root = find_project_root(payload.get("cwd") or os.getcwd())
    if root is None:
        return
    plan_path, text = load_plan(root)
    if text is None:
        return
    status_match = STATUS_RE.search(text)
    if not status_match or status_match.group(1).upper() != "RUNNING":
        return

    if stalled_count(root, text) > MAX_STALLED_BLOCKS:
        print(json.dumps({
            "systemMessage": (
                f"[phased-dev-loop] 连续 {MAX_STALLED_BLOCKS} 次拦截后技术方案文档没有任何变化，"
                f"已放行停止。请检查 {os.path.relpath(plan_path, root)}。"
            )
        }, ensure_ascii=False))
        return

    reason = build_reason(os.path.relpath(plan_path, root), parse_phases(text))
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    main()
