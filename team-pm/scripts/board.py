#!/usr/bin/env python3
"""团队看板命令行。格式和用法见 team-pm/references/protocol.md。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board_dispatch  # noqa: E402
import board_store as bs  # noqa: E402

VERSION = "0.1.0"
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SCHEDULED_ROLES = {"产品", "开发", "QA"}
# 不需要看板目录存在的子命令，main() 不为它们创建看板
NO_BOARD_COMMANDS = {"version"}


# ---------- 调度：哪些单可以派 ----------

def running_counts():
    """返回 (全局进行中的单数, {项目: 进行中的单数}, {项目: 进行中的代码类单数})。"""
    total, per_project, code = 0, {}, {}
    for name in bs.project_names():
        for doc in bs.list_items(name):
            if doc.get("状态") != "进行中" or doc.get("角色") not in SCHEDULED_ROLES:
                continue
            total += 1
            per_project[name] = per_project.get(name, 0) + 1
            if doc.get("类型") in bs.CODE_TYPES:
                code[name] = code.get(name, 0) + 1
    return total, per_project, code


def candidate_items():
    """依赖都已完成、项目未暂停的待领取单，按项目优先级和编号排序。"""
    out = []
    for name in bs.project_names():
        project = bs.load_project(name)
        if project.get("阶段") in bs.INACTIVE_STAGES:
            continue
        docs = bs.list_items(name)
        by_no = {bs.item_number(d): d for d in docs}
        rank = PRIORITY_ORDER.get(project.get("优先级"), 9)
        for doc in docs:
            if (doc.get("状态") == "待领取" and doc.get("角色") in SCHEDULED_ROLES
                    and bs.deps_done(name, doc, by_no)):
                out.append((rank, name, doc))
    out.sort(key=lambda t: (t[0], t[1], bs.item_number(t[2])))
    return [(name, doc) for _, name, doc in out]


def ready_items():
    """在并发上限内可以马上派出的单。"""
    cfg = bs.load_config()
    total, per_project, code = running_counts()
    picked = []
    for name, doc in candidate_items():
        if total >= cfg["max_sessions"]:
            break
        if per_project.get(name, 0) >= cfg["max_sessions_per_project"]:
            continue
        is_code = doc.get("类型") in bs.CODE_TYPES
        if is_code and code.get(name, 0) >= 1:
            continue
        picked.append((name, doc))
        total += 1
        per_project[name] = per_project.get(name, 0) + 1
        if is_code:
            code[name] = code.get(name, 0) + 1
    return picked


# ---------- 输出 ----------

def item_line(doc):
    no = bs.item_number(doc)
    extra = f"  依赖 {doc.get('依赖')}" if doc.get("依赖") not in ("", "无") else ""
    session = f"  会话 {doc.get('会话')}" if doc.get("会话") else ""
    source = f"  来源 {doc.get('来源')}" if doc.get("来源") else ""
    return f"  {no} [{doc.get('状态')}] {doc.get('角色')}/{doc.get('类型')} {bs.item_name(doc)}{extra}{source}{session}"


def print_status(names):
    for name in names:
        p = bs.load_project(name)
        docs = bs.list_items(name)
        pending = [ln for ln in p.section("待人确认").splitlines() if ln.startswith("- [ ]")]
        print(f"■ {name}  阶段 {p.get('阶段')}  优先级 {p.get('优先级')}  待人确认 {len(pending)}")
        for doc in docs:
            if doc.get("状态") not in ("完成", "取消"):
                print(item_line(doc))
        done = sum(1 for d in docs if d.get("状态") == "完成")
        print(f"  已完成 {done} 张，共 {len(docs)} 张")


def print_event(e):
    note = f"：{e['说明']}" if e.get("说明") else ""
    print(f"#{e['序号']} {e['时间']} {e['项目']} {e['任务单']} {e['事件']}{note}")


# ---------- 子命令 ----------

def cmd_project(a):
    if a.action == "new":
        doc = bs.create_project(a.name, a.repo, a.confirm, a.priority, a.goal)
        print(f"已建项目 {a.name}：{doc.path}")
        if not board_dispatch.repo_trusted(doc.get("仓库")):
            print(f"注意：{doc.get('仓库')} 还没被 Claude Code 信任，派单会失败。请人在这个目录运行一次 claude 并接受信任提示")
    elif a.action == "set":
        bs.set_project_field(a.name, a.key, a.value)
        print(f"{a.name} {a.key}: {a.value}")
    elif a.action == "doc":
        bs.set_project_doc(a.name, a.kind, a.path)
        print(f"{a.name} 文档 {a.kind}: {a.path}")
    elif a.action == "ask":
        bs.add_human_question(a.name, a.text)
        print(f"已记入 {a.name} 待人确认")
    elif a.action == "resolve":
        print(f"已勾掉 {bs.resolve_human_questions(a.name, a.which)} 条")
    elif a.action == "show":
        print("\n".join(bs.load_project(a.name).lines))
    elif a.action == "list":
        for name in bs.project_names():
            p = bs.load_project(name)
            print(f"{name}  阶段 {p.get('阶段')}  优先级 {p.get('优先级')}  {p.get('目标')}")


def cmd_item(a):
    if a.action == "new":
        doc = bs.create_item(a.project, a.role, a.type, a.title, a.desc, a.deps, a.input, a.source)
        print(f"已建任务单 {bs.item_number(doc)}：{doc.path}")
    elif a.action == "show":
        print("\n".join(bs.load_item(a.project, a.number).lines))
    elif a.action == "path":
        print(bs.item_path(a.project, a.number))
    elif a.action == "list":
        for doc in bs.list_items(a.project):
            if matches(doc, a):
                print(item_line(doc))
    elif a.action == "claim":
        doc = bs.claim_item(a.project, a.number, a.session or os.environ.get("TEAM_SESSION", ""), a.resume)
        print(f"已领取 {a.project} {bs.item_number(doc)}，开始干活；做完运行 item done")
    else:
        cmd_item_finish(a)


def matches(doc, a):
    """item list 的筛选：状态、类型相等，来源里含指定单号。"""
    if a.status and doc.get("状态") != a.status:
        return False
    if a.type and doc.get("类型") != a.type:
        return False
    return not a.source or f"{int(a.source):04d}" in bs.parse_deps(doc.get("来源"))


def cmd_item_finish(a):
    if a.action == "done":
        fields = {"状态": "完成"}
        if a.output:
            fields["产出"] = a.output
        bs.update_item(a.project, a.number, fields, a.result, "完成", a.result.splitlines()[0] if a.result else "")
    elif a.action == "block":
        bs.update_item(a.project, a.number, {"状态": "阻塞"}, a.reason, "阻塞", a.reason)
    elif a.action == "ask-human":
        bs.update_item(a.project, a.number, {"状态": "待人确认"}, a.question, "待人确认", a.question)
    elif a.action == "set":
        bs.update_item(a.project, a.number, {a.key: a.value})
    elif a.action == "reply":
        bs.reply_item(a.project, a.number, a.text)
    print(f"{a.project} {int(a.number):04d} 已更新")


def cmd_ready(a):
    items = ready_items()
    for name, doc in items:
        print(f"{name} {bs.item_number(doc)} {doc.get('角色')}/{doc.get('类型')} {bs.item_name(doc)}")
    if not items:
        print("没有可以派出的任务单")


def cmd_status(a):
    print_status([a.project] if a.project else bs.project_names())


def cmd_events(a):
    for e in bs.read_events(a.since):
        print_event(e)


def build_parser():
    ap = argparse.ArgumentParser(prog="board.py", description="团队看板")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add_project_parsers(sub.add_parser("project", help="项目卡").add_subparsers(dest="action", required=True))
    add_item_parsers(sub.add_parser("item", help="任务单").add_subparsers(dest="action", required=True))
    sub.add_parser("ready", help="列出可以马上派出的任务单")
    sub.add_parser("status", help="所有项目的进展").add_argument("project", nargs="?")
    sub.add_parser("events", help="打印事件").add_argument("--since", type=int, default=0)
    sub.add_parser("notify", help="config 里 notify 为 dws 时，用 watch-task 的钉钉单聊发一条通知").add_argument("text")
    sub.add_parser("version", help="打印脚本版本号和看板根目录")
    return ap, sub


def add_project_parsers(p):
    x = p.add_parser("new")
    x.add_argument("name")
    x.add_argument("--repo", required=True)
    x.add_argument("--confirm", default="需求, 方案, 交付")
    x.add_argument("--priority", default="P1")
    x.add_argument("--goal", default="")
    x = p.add_parser("set")
    x.add_argument("name")
    x.add_argument("key")
    x.add_argument("value")
    x = p.add_parser("doc")
    x.add_argument("name")
    x.add_argument("kind", choices=["需求", "方案", "测试计划", "测试报告", "验收"])
    x.add_argument("path")
    x = p.add_parser("ask")
    x.add_argument("name")
    x.add_argument("text")
    x = p.add_parser("resolve", help="勾掉待人确认里第几条未处理的问题")
    x.add_argument("name")
    x.add_argument("which", help="序号（从 1 数，只数未处理的）或 all")
    p.add_parser("show").add_argument("name")
    p.add_parser("list")


def add_item_parsers(i):
    x = i.add_parser("new")
    x.add_argument("project")
    x.add_argument("--role", required=True, choices=list(bs.ROLES))
    x.add_argument("--type", required=True, choices=list(bs.TYPES))
    x.add_argument("--title", default="")
    x.add_argument("--desc", default="")
    x.add_argument("--deps", default="")
    x.add_argument("--input", default="")
    x.add_argument("--from", dest="source", default="", help="由哪张单引出，如答疑单写提问方的单号；多张用逗号分开")
    for name in ("show", "path"):
        x = i.add_parser(name)
        x.add_argument("project")
        x.add_argument("number")
    x = i.add_parser("list")
    x.add_argument("project")
    x.add_argument("--status", choices=bs.ITEM_STATUSES)
    x.add_argument("--type", choices=list(bs.TYPES))
    x.add_argument("--from", dest="source", default="", help="只列来源里含这个单号的")
    x = i.add_parser("claim")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--session", default="")
    x.add_argument("--resume", action="store_true")
    x = i.add_parser("done")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--result", required=True, help="三五行结论，写进任务单“结果”一节")
    x.add_argument("--output", default="", help="产出文档路径或提交号")
    x = i.add_parser("block")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--reason", required=True)
    x = i.add_parser("ask-human")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--question", required=True)
    x = i.add_parser("reply", help="记下答复；阻塞、待人确认的单改回待领取")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("--text", required=True)
    x = i.add_parser("set")
    x.add_argument("project")
    x.add_argument("number")
    x.add_argument("key")
    x.add_argument("value")


def cmd_notify(a):
    cfg = bs.load_config()
    if cfg.get("notify") != "dws":
        print("notify 没开启（config.json 里 notify 不是 dws），未发送")
        return
    script = os.path.expanduser(cfg.get("dws_hook") or "~/.claude/skills/watch-task/scripts/hook.py")
    if not os.path.isfile(script):
        raise bs.BoardError(f"找不到 watch-task 的发送脚本：{script}")
    code, out = board_dispatch.run([sys.executable, script, "--send", "团队", f"[团队] {a.text}"], timeout=60)
    if code != 0:
        raise bs.BoardError(f"发送失败：{out.strip()[:200]}")
    print("已发送")


def cmd_version(a):
    print(f"版本: v{VERSION}")
    print(f"看板根目录: {os.path.abspath(bs.board_root())}")


COMMANDS = {"project": cmd_project, "item": cmd_item, "ready": cmd_ready,
            "status": cmd_status, "events": cmd_events, "notify": cmd_notify,
            "version": cmd_version}


def main(argv=None):
    ap, sub = build_parser()
    board_dispatch.register(sub, COMMANDS)
    a = ap.parse_args(argv)
    try:
        if a.cmd not in NO_BOARD_COMMANDS:
            bs.ensure_board()
        COMMANDS[a.cmd](a)
    except bs.BoardError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
