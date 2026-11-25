"""团队看板的文件读写：项目卡、任务单、事件、配置。

看板根目录默认 ~/.claude/team，可用环境变量 TEAM_BOARD 改。
项目卡和任务单都是 Markdown：第一个 `## ` 标题之前的 `键: 值` 行是字段，之后是正文各节。
所有写操作在看板级文件锁内完成，多个会话同时改看板不会互相覆盖。
"""
import contextlib
import fcntl
import json
import os
import re
import time

FIELD_RE = re.compile(r"^([^\s#:：][^:：]{0,15}?)\s*[:：]\s?(.*)$")
SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
ITEM_FILE_RE = re.compile(r"^(\d{4})-.*\.md$")

ROLES = {"PM": "pm", "产品": "product", "开发": "dev", "QA": "qa", "人": "human"}
TYPES = {
    "需求": "prd", "方案": "design", "测试计划": "test-plan", "开发": "dev", "测试": "test",
    "修复": "fix", "验收": "accept", "答疑": "question", "确认": "confirm", "其他": "task",
}
CODE_TYPES = {"开发", "修复"}
ITEM_STATUSES = ["待领取", "进行中", "待人确认", "阻塞", "完成", "取消"]
PROJECT_STAGES = ["立项", "需求", "方案", "开发", "测试", "验收", "交付", "完成", "暂停"]
INACTIVE_STAGES = {"完成", "暂停"}
# 项目规模决定走哪一档流程，见 team-pm/SKILL.md 的“项目规模”
PROJECT_SCALES = ["S", "M", "L"]
DEFAULT_SCALE = "M"

DEFAULT_CONFIG = {
    "max_sessions": 4,
    "max_sessions_per_project": 2,
    "permission_mode": "auto",
    "notify": "none",
    "effort": {"产品": "medium", "QA": "medium"},
    "autocompact": "auto",
    "runner": "subagent",
}


class BoardError(Exception):
    pass


def board_root():
    return os.path.expanduser(os.environ.get("TEAM_BOARD") or "~/.claude/team")


def ensure_board():
    root = board_root()
    os.makedirs(os.path.join(root, "projects"), exist_ok=True)
    cfg = os.path.join(root, "config.json")
    if not os.path.exists(cfg):
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    return root


def load_config():
    path = os.path.join(ensure_board(), "config.json")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return {**DEFAULT_CONFIG, **cfg}


def board_lock():
    return file_lock(".lock")


@contextlib.contextmanager
def file_lock(name):
    """看板目录下的独占文件锁。同一进程里不同的锁可以嵌套，同一把锁不能。"""
    with open(os.path.join(ensure_board(), name), "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def today():
    return time.strftime("%Y-%m-%d")


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---------- 文档：字段 + 正文各节 ----------

class Doc:
    """项目卡或任务单。改动只替换对应的行，其余内容原样保留。"""

    def __init__(self, path, text):
        self.path = path
        self.lines = text.splitlines()

    @classmethod
    def load(cls, path):
        return cls(path, read_file(path))

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines).rstrip("\n") + "\n")
        os.replace(tmp, self.path)

    def _header_end(self):
        for i, line in enumerate(self.lines):
            if SECTION_RE.match(line):
                return i
        return len(self.lines)

    def _field_index(self, key):
        for i in range(self._header_end()):
            m = FIELD_RE.match(self.lines[i])
            if m and m.group(1).strip() == key:
                return i
        return None

    def fields(self):
        out = {}
        for line in self.lines[:self._header_end()]:
            m = FIELD_RE.match(line)
            if m:
                out[m.group(1).strip()] = m.group(2).strip()
        return out

    def get(self, key, default=""):
        return self.fields().get(key, default)

    def set(self, key, value):
        i = self._field_index(key)
        line = f"{key}: {value}"
        if i is not None:
            self.lines[i] = line
            return
        last = max((j for j in range(self._header_end()) if FIELD_RE.match(self.lines[j])), default=None)
        self.lines.insert(last + 1 if last is not None else len(self.lines[:self._header_end()]), line)

    def _section_range(self, name):
        for i, line in enumerate(self.lines):
            m = SECTION_RE.match(line)
            if m and m.group(1) == name:
                end = next((j for j in range(i + 1, len(self.lines)) if SECTION_RE.match(self.lines[j])),
                           len(self.lines))
                return i, end
        return None

    def section(self, name):
        r = self._section_range(name)
        return "\n".join(self.lines[r[0] + 1:r[1]]).strip() if r else ""

    def set_section(self, name, body):
        r = self._section_range(name)
        # 正文里以 ## 开头的行会被当成新的一节，缩进一格避免把本节截断
        rows = [" " + ln if SECTION_RE.match(ln) else ln for ln in body.strip().splitlines()]
        block = [""] + rows + [""]
        if r:
            self.lines[r[0] + 1:r[1]] = block
        else:
            self.lines += ["", f"## {name}"] + block

    def append_to_section(self, name, line):
        body = self.section(name)
        self.set_section(name, (body + "\n" + line).strip() if body and body != "无" else line)


# ---------- 项目 ----------

def project_dir(name):
    return os.path.join(board_root(), "projects", name)


def project_path(name):
    return os.path.join(project_dir(name), "project.md")


def load_project(name):
    path = project_path(name)
    if not os.path.exists(path):
        raise BoardError(f"项目不存在：{name}")
    return Doc.load(path)


def project_names():
    base = os.path.join(ensure_board(), "projects")
    return sorted(n for n in os.listdir(base) if os.path.exists(os.path.join(base, n, "project.md")))


def create_project(name, repo, confirm, priority, goal):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise BoardError("项目名只能用小写字母、数字和短横线，如 order-refund")
    repo = os.path.abspath(os.path.expanduser(repo))
    if not os.path.isdir(os.path.join(repo, ".git")) and not os.path.isfile(os.path.join(repo, ".git")):
        raise BoardError(f"不是 git 仓库：{repo}")
    with board_lock():
        if os.path.exists(project_path(name)):
            raise BoardError(f"项目已存在：{name}")
        os.makedirs(os.path.join(project_dir(name), "items"))
        text = "\n".join([
            f"项目: {name}",
            f"仓库: {repo}",
            f"工作目录: {os.path.join(repo, '.claude', 'worktrees', name)}",
            f"分支: feat/{name}",
            "目标分支: release",
            "阶段: 立项",
            f"人工确认: {confirm}",
            f"规模: {DEFAULT_SCALE}",
            f"优先级: {priority}",
            f"目标: {goal}",
            "", "## 文档", "", "需求:", "方案:", "测试计划:",
            "", "## 待人确认", "", "无",
            "", "## 记录", "", f"- {today()} 立项",
        ])
        doc = Doc(project_path(name), text)
        doc.save()
    return doc


def set_project_field(name, key, value):
    if key == "阶段" and value not in PROJECT_STAGES:
        raise BoardError(f"阶段只能是：{'、'.join(PROJECT_STAGES)}")
    if key == "规模" and value not in PROJECT_SCALES:
        raise BoardError(f"规模只能是：{'、'.join(PROJECT_SCALES)}")
    with board_lock():
        doc = load_project(name)
        old = doc.get(key)
        doc.set(key, value)
        if key in ("阶段", "规模") and old != value:
            doc.append_to_section("记录", f"- {today()} {key} {old} → {value}")
        doc.save()
    return doc


def set_project_doc(name, kind, path):
    """在项目卡“文档”一节登记需求、方案、测试计划的路径。"""
    with board_lock():
        doc = load_project(name)
        body = doc.section("文档").splitlines()
        rows = [ln for ln in body if not ln.startswith(f"{kind}:")]
        rows.append(f"{kind}: {path}")
        doc.set_section("文档", "\n".join(rows))
        doc.save()


def resolve_human_questions(name, which):
    """勾掉项目卡“待人确认”里第 which 条未处理的问题（从 1 数），which 为 all 时全部勾掉。返回勾掉的条数。"""
    with board_lock():
        doc = load_project(name)
        rows = doc.section("待人确认").splitlines()
        open_idx = [i for i, ln in enumerate(rows) if ln.startswith("- [ ]")]
        if which == "all":
            picked = open_idx
        else:
            n = int(which) if str(which).isdigit() else 0
            if not 1 <= n <= len(open_idx):
                raise BoardError(f"待人确认里没有第 {which} 条未处理的问题（共 {len(open_idx)} 条）")
            picked = [open_idx[n - 1]]
        for i in picked:
            rows[i] = "- [x]" + rows[i][5:]
        doc.set_section("待人确认", "\n".join(rows) or "无")
        doc.save()
    return len(picked)


def add_pm_decision(name, text):
    """PM 代为决定的非阻塞问题记进项目卡“PM 决定”一节，用户事后可以查看和推翻。"""
    with board_lock():
        doc = load_project(name)
        doc.append_to_section("PM 决定", f"- {today()} {text}")
        doc.save()


def add_human_question(name, text):
    with board_lock():
        doc = load_project(name)
        doc.append_to_section("待人确认", f"- [ ] {text}")
        doc.save()


# ---------- 任务单 ----------

def items_dir(project):
    return os.path.join(project_dir(project), "items")


def item_files(project):
    d = items_dir(project)
    if not os.path.isdir(d):
        raise BoardError(f"项目不存在：{project}")
    return sorted(n for n in os.listdir(d) if ITEM_FILE_RE.match(n))


def format_no(number):
    """把 1、0001 这类单号统一成四位；不是数字时报看板错误。"""
    if not str(number).strip().isdigit():
        raise BoardError(f"单号要写数字，如 0003：{number}")
    return f"{int(number):04d}"


def item_path(project, number):
    no = format_no(number)
    for n in item_files(project):
        if n.startswith(no + "-"):
            return os.path.join(items_dir(project), n)
    raise BoardError(f"任务单不存在：{project} {no}")


def load_item(project, number):
    return Doc.load(item_path(project, number))


def item_number(doc):
    return os.path.basename(doc.path)[:4]


def item_title(doc):
    first = doc.lines[0] if doc.lines else ""
    return first.lstrip("# ").strip()


def item_name(doc):
    """标题去掉编号和角色，如“写需求”。"""
    title = item_title(doc)
    return re.sub(r"^\d{4}\s+[^：:]+[：:]", "", title).strip()


def list_items(project):
    return [Doc.load(os.path.join(items_dir(project), n)) for n in item_files(project)]


def parse_deps(value):
    nums = re.findall(r"\d+", value or "")
    return [f"{int(n):04d}" for n in nums]


def _check_choice(value, choices, label):
    if value not in choices:
        raise BoardError(f"{label}只能是：{'、'.join(choices)}")


def create_item(project, role, type_, title, desc, deps="", input_="", source=""):
    _check_choice(role, list(ROLES), "角色")
    _check_choice(type_, list(TYPES), "类型")
    load_project(project)
    deps_list = parse_deps(deps)
    with board_lock():
        existing = item_files(project)
        for d in deps_list:
            if not any(n.startswith(d + "-") for n in existing):
                raise BoardError(f"依赖的任务单不存在：{d}")
        no = f"{max((int(n[:4]) for n in existing), default=0) + 1:04d}"
        path = os.path.join(items_dir(project), f"{no}-{ROLES[role]}-{TYPES[type_]}.md")
        text = "\n".join([
            f"# {no} {role}：{title or type_}",
            "", f"角色: {role}", f"类型: {type_}", "状态: 待领取",
            f"依赖: {'、'.join(deps_list) or '无'}", "会话:", "重试: 0",
            f"输入: {input_}", "产出:", f"来源: {source}",
            "", "## 说明", "", desc or "无", "", "## 结果", "", "无",
        ])
        doc = Doc(path, text)
        doc.save()
    return doc


def update_item(project, number, fields=None, result=None, event=None, note=""):
    """在锁内改任务单字段和“结果”一节，可同时追加一条事件。"""
    with board_lock():
        doc = load_item(project, number)
        for k, v in (fields or {}).items():
            if k == "状态":
                _check_choice(v, ITEM_STATUSES, "状态")
            doc.set(k, v)
        if result is not None:
            doc.set_section("结果", result)
        doc.save()
        if event:
            _append_event(project, item_number(doc), event, note)
    return doc


def reply_item(project, number, text):
    """把人的决定或别的角色的答复记进任务单“答复”一节；等答复的单改回待领取，可以重派。"""
    with board_lock():
        doc = load_item(project, number)
        doc.append_to_section("答复", f"- {today()} {text}")
        if doc.get("状态") in ("阻塞", "待人确认"):
            doc.set("状态", "待领取")
            doc.set("重试", "0")  # 人或别的角色介入后重新计重试次数
        doc.save()
    return doc


def claim_item(project, number, session="", resume=False):
    with board_lock():
        doc = load_item(project, number)
        status = doc.get("状态")
        if status == "进行中" and not resume:
            raise BoardError(f"{item_number(doc)} 已被领取（会话 {doc.get('会话') or '未知'}）；重派后接着做时加 --resume")
        if status not in ("待领取", "进行中"):
            raise BoardError(f"{item_number(doc)} 状态是 {status}，不能领取")
        doc.set("状态", "进行中")
        if session:
            doc.set("会话", session)
        doc.save()
    return doc


def deps_done(project, doc, by_no=None):
    if by_no is None:
        by_no = {item_number(d): d for d in list_items(project)}
    return all(by_no[d].get("状态") == "完成" for d in parse_deps(doc.get("依赖")) if d in by_no)


# ---------- 事件 ----------

def events_path():
    return os.path.join(board_root(), "events.jsonl")


def _append_event(project, item, event, note):
    """调用方需持有看板锁。"""
    path = events_path()
    seq = 1
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            seq = sum(1 for _ in f) + 1
    row = {"序号": seq, "时间": now(), "项目": project, "任务单": item, "事件": event, "说明": note}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def append_event(project, item, event, note=""):
    with board_lock():
        return _append_event(project, item, event, note)


def read_events(since=0):
    path = events_path()
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("序号", 0) > since:
                out.append(row)
    return out
