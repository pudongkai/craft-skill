"""QA 测试：三个 Stop hook 识别后台 Bash 命令（测试计划 docs/test/stop-guard-bg.md）。

每个场景在独立临时目录里造出 hook 需要的状态，用 subprocess 调用 hook 脚本，
只看退出码、标准输出和标准错误。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(TESTS))
STOP_GUARD = os.path.join(SKILLS, "phased-dev-loop", "scripts", "stop_guard.py")
FLOW_GUARD = os.path.join(SKILLS, "dev-flow", "scripts", "flow_guard.py")
WORKER_GUARD = os.path.join(SKILLS, "team-pm", "scripts", "worker_guard.py")
sys.path.insert(0, os.path.join(SKILLS, "team-pm", "scripts"))
import board_store as bs  # noqa: E402

TID = "b6r5u7d58"
PLAN_RUNNING = "# 方案\n\n开发状态: RUNNING\n\n## 开发计划\n\n- [ ] 阶段1：实现\n"
PLAN_DONE = "# 方案\n\n开发状态: DONE\n\n## 开发计划\n\n- [x] 阶段1：实现\n"


def bash_started(tid=TID, **extra):
    return {"type": "user", "toolUseResult": {"stdout": "", "stderr": "", "backgroundTaskId": tid, **extra}}


def agent_started(aid="a1b2c3"):
    return {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": aid}}


def notification(tid, status="completed"):
    return (f"<task-notification>\n<task-id>{tid}</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n"
            f"<status>{status}</status>\n<summary>done</summary>\n</task-notification>")


def queue_done(tid=TID, status="completed"):
    return {"type": "queue-operation", "operation": "enqueue", "content": notification(tid, status)}


def attachment_done(tid=TID, status="completed"):
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": notification(tid, status)}}


def task_stopped(tid=TID):
    return {"type": "user", "toolUseResult": {"message": f"Successfully stopped task: {tid} (sleep 600)",
                                              "task_id": tid, "task_type": "local_bash"}}


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Scenario:
    """一个 hook 的独立运行环境。kind: stop / flow / worker / worker-code。"""

    def __init__(self, kind, blocking=True):
        self.kind = kind
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "proj")
        self.transcript = os.path.join(self.tmp.name, "session.jsonl")
        self.board = os.path.join(self.tmp.name, "board")
        self.env = {**os.environ, "TEAM_BOARD": self.board}
        self.mention = None
        if kind in ("stop", "worker-code"):
            self._setup_plan(blocking or kind == "worker-code")
        elif kind == "flow":
            self._setup_flow(blocking)
        else:
            os.makedirs(self.root)
        if kind.startswith("worker"):
            self._setup_board("开发" if kind == "worker-code" else "测试", blocking)

    def _setup_plan(self, running):
        write_file(os.path.join(self.root, ".claude", "dev-loop", "ACTIVE"), "docs/plan.md\n")
        write_file(os.path.join(self.root, "docs", "plan.md"), PLAN_RUNNING if running else PLAN_DONE)
        self.mention = "docs/plan.md"

    def _setup_flow(self, blocking):
        stage = "代码审查" if blocking else "完成"
        write_file(os.path.join(self.root, ".claude", "dev-flow", "demo.state"), f"环节: {stage}\n")
        self.mention = ".claude/dev-flow/demo.state"

    def _setup_board(self, item_type, in_progress):
        old = os.environ.get("TEAM_BOARD")
        os.environ["TEAM_BOARD"] = self.board
        try:
            repo = os.path.join(self.tmp.name, "repo")
            os.makedirs(repo)
            subprocess.run(["git", "init", "-q", repo], check=True)
            bs.create_project("demo", repo, "需求, 方案, 交付", "P1", "演示")
            bs.create_item("demo", "QA", item_type, "测试", "说明")
            bs.claim_item("demo", 1)
            if not in_progress:
                bs.update_item("demo", 1, fields={"状态": "完成"})
        finally:
            if old is None:
                os.environ.pop("TEAM_BOARD", None)
            else:
                os.environ["TEAM_BOARD"] = old

    def write(self, rows):
        """rows 里 dict 按 JSON 写一行，str 原样写一行；开头自动加一行提到方案 / 状态文件的消息。"""
        lines = []
        if self.mention:
            lines.append(json.dumps({"type": "user", "message": {"content": f"继续 {self.mention}"}},
                                    ensure_ascii=False))
        for row in rows:
            lines.append(row if isinstance(row, str) else json.dumps(row, ensure_ascii=False))
        write_file(self.transcript, "\n".join(lines) + "\n")

    def run(self, session="s1", transcript=None):
        payload = {"session_id": session, "transcript_path": transcript or self.transcript, "cwd": self.root}
        if self.kind == "stop":
            cmd = [sys.executable, STOP_GUARD]
        elif self.kind == "flow":
            cmd = [sys.executable, FLOW_GUARD]
        else:
            cmd = [sys.executable, WORKER_GUARD, "--project", "demo", "--item", "1"]
        proc = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True,
                              env=self.env, cwd=self.tmp.name)
        return proc

    def stop_events(self):
        old = os.environ.get("TEAM_BOARD")
        os.environ["TEAM_BOARD"] = self.board
        try:
            return [e for e in bs.read_events() if e["事件"] == "会话停下"]
        finally:
            if old is None:
                os.environ.pop("TEAM_BOARD", None)
            else:
                os.environ["TEAM_BOARD"] = old

    def close(self):
        self.tmp.cleanup()


HOOKS = ("stop", "flow", "worker")


class GuardBgBashTest(unittest.TestCase):
    def scenario(self, kind, blocking=True):
        s = Scenario(kind, blocking)
        self.addCleanup(s.close)
        return s

    def assertAllowed(self, proc, msg=""):
        self.assertEqual(proc.returncode, 0, msg)
        self.assertEqual(proc.stderr, "", msg)
        self.assertEqual(proc.stdout.strip(), "", msg)

    def assertBlocked(self, proc, msg=""):
        self.assertEqual(proc.returncode, 0, msg)
        self.assertEqual(proc.stderr, "", msg)
        out = json.loads(proc.stdout)
        self.assertEqual(out.get("decision"), "block", msg)
        return out

    def run_rows(self, kind, rows, blocking=True):
        s = self.scenario(kind, blocking)
        s.write(rows)
        return s.run()

    def check_all(self, rows, expect, blocking=True, same_as_control=False):
        for kind in HOOKS:
            with self.subTest(hook=kind):
                proc = self.run_rows(kind, rows, blocking)
                if expect == "allow":
                    self.assertAllowed(proc, kind)
                    continue
                out = self.assertBlocked(proc, kind)
                if same_as_control:
                    control = self.assertBlocked(self.run_rows(kind, []), kind)
                    self.assertEqual((out["decision"], out["reason"]),
                                     (control["decision"], control["reason"]), kind)

    # TC-1
    def test_tc01_pending_bash_allows(self):
        self.check_all([bash_started()], "allow")

    # TC-2
    def test_tc02_timed_out_to_background_allows(self):
        self.check_all([bash_started(timedOutAfterMs=120000)], "allow")

    # TC-3
    def test_tc03_queue_notification_blocks_like_control(self):
        self.check_all([bash_started(), queue_done()], "block", same_as_control=True)

    # TC-4
    def test_tc04_attachment_notification_blocks(self):
        self.check_all([bash_started(), attachment_done()], "block")

    # TC-5
    def test_tc05_other_end_statuses_block(self):
        for status in ("failed", "killed", "stopped"):
            with self.subTest(status=status):
                self.check_all([bash_started(), queue_done(status=status)], "block")

    # TC-6
    def test_tc06_task_stop_blocks(self):
        self.check_all([bash_started(), task_stopped()], "block")

    # TC-7
    def test_tc07_two_commands_one_ended(self):
        for kind in HOOKS:
            with self.subTest(hook=kind):
                s = self.scenario(kind)
                rows = [bash_started("b1xx"), bash_started("b2yy"), queue_done("b1xx")]
                s.write(rows)
                self.assertAllowed(s.run(), kind)
                s.write(rows + [queue_done("b2yy")])
                self.assertBlocked(s.run(session="s2"), kind)

    # TC-8
    def test_tc08_pending_bash_with_finished_agent_allows(self):
        self.check_all([agent_started(), queue_done("a1b2c3"), bash_started()], "allow")

    # TC-9
    def test_tc09_pending_agent_with_finished_bash_allows(self):
        self.check_all([bash_started(), queue_done(), agent_started()], "allow")

    # TC-10
    def test_tc10_all_finished_blocks_like_control(self):
        rows = [agent_started(), bash_started(), queue_done("a1b2c3"), attachment_done()]
        self.check_all(rows, "block", same_as_control=True)

    # TC-11
    def test_tc11_not_blocking_state_without_tasks_allows(self):
        self.check_all([], "allow", blocking=False)

    # TC-12
    def test_tc12_not_blocking_state_with_pending_bash_allows(self):
        self.check_all([bash_started()], "allow", blocking=False)

    # TC-13
    def test_tc13_missing_transcript_blocks(self):
        for kind in HOOKS:
            with self.subTest(hook=kind):
                s = self.scenario(kind)
                self.assertBlocked(s.run(transcript=os.path.join(s.tmp.name, "nope.jsonl")), kind)

    # TC-14
    def test_tc14_bad_lines_around_start_allow(self):
        self.check_all(["not json {", bash_started(), "not json {"], "allow")

    # TC-15
    def test_tc15_bad_line_between_start_and_end_blocks(self):
        self.check_all([bash_started(), "not json {", queue_done()], "block")

    # TC-16
    def test_tc16_non_hex_ids(self):
        for tid in ("bZ9-x_7", "B6R5U7D58"):
            with self.subTest(tid=tid):
                self.check_all([bash_started(tid)], "allow")
                self.check_all([bash_started(tid), queue_done(tid)], "block")

    # TC-17
    def test_tc17_worker_code_item_forwards_to_stop_guard(self):
        self.assertAllowed(self.run_rows("worker-code", [bash_started()]))
        for rows in ([bash_started(), queue_done()], [bash_started(), task_stopped()]):
            out = self.assertBlocked(self.run_rows("worker-code", rows))
            self.assertTrue(out["reason"].startswith("[phased-dev-loop]"), out["reason"])

    # TC-18
    def test_tc18_worker_release_writes_no_stop_event(self):
        s = self.scenario("worker")
        s.write([bash_started()])
        before = len(s.stop_events())
        for i in range(5):
            self.assertAllowed(s.run(), f"第 {i + 1} 次")
        self.assertEqual(len(s.stop_events()), before)

    # TC-19
    def test_tc19_foreground_bash_and_text_mention_not_tasks(self):
        rows = [
            {"type": "user", "toolUseResult": {"stdout": "ok", "stderr": "", "interrupted": False}},
            {"type": "user", "message": {"content": f"看看 <task-id>{TID}</task-id> 和 \"task_id\""}},
        ]
        self.check_all(rows, "block")

    # TC-20
    def test_tc20_other_id_or_running_status_still_pending(self):
        self.check_all([bash_started(), queue_done("b0000000")], "allow")
        self.check_all([bash_started(), queue_done(status="running")], "allow")

    # TC-21
    def test_tc21_failed_task_stop_not_end(self):
        failures = [
            {"type": "user", "toolUseResult": f"Error: No task found with ID: {TID}"},
            {"type": "user", "toolUseResult": {"message": f"Failed to stop task: {TID}"}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
                                                      "content": f"No task found with ID: {TID}"}]}},
        ]
        for row in failures:
            with self.subTest(row=row):
                self.check_all([bash_started(), row], "allow")

    # TC-22
    def test_tc22_pending_release_not_counted_as_stall(self):
        for kind in HOOKS:
            with self.subTest(hook=kind):
                s = self.scenario(kind)
                s.write([bash_started()])
                for i in range(4):
                    self.assertAllowed(s.run(), f"{kind} 第 {i + 1} 次")
                s.write([bash_started(), queue_done()])
                out = self.assertBlocked(s.run(), kind)
                self.assertNotIn("systemMessage", out)

    # TC-26
    def test_tc26_notification_before_start_blocks(self):
        self.check_all([queue_done(), bash_started()], "block", same_as_control=True)
        self.check_all([attachment_done(), bash_started()], "block")

    # 回归：多份方案 / 状态文件时按会话记录里最后提到的那份判断
    def test_regression_last_mentioned_plan(self):
        s = self.scenario("stop")
        write_file(os.path.join(s.root, ".claude", "dev-loop", "ACTIVE"), "docs/plan.md\ndocs/done.md\n")
        write_file(os.path.join(s.root, "docs", "done.md"), PLAN_DONE)
        s.mention = None
        say = lambda p: {"type": "user", "message": {"content": f"继续 {p}"}}  # noqa: E731
        s.write([say("docs/plan.md"), say("docs/done.md")])
        self.assertAllowed(s.run())
        s.write([say("docs/done.md"), say("docs/plan.md")])
        self.assertBlocked(s.run(session="s2"))
        s.write([say("docs/done.md"), say("docs/plan.md"), bash_started()])
        self.assertAllowed(s.run(session="s3"))

    def test_regression_last_mentioned_flow_state(self):
        s = self.scenario("flow")
        write_file(os.path.join(s.root, ".claude", "dev-flow", "other.state"), "环节: 完成\n")
        s.mention = None
        say = lambda p: {"type": "user", "message": {"content": f"继续 {p}"}}  # noqa: E731
        s.write([say(".claude/dev-flow/demo.state"), say(".claude/dev-flow/other.state")])
        self.assertAllowed(s.run())
        s.write([say(".claude/dev-flow/other.state"), say(".claude/dev-flow/demo.state")])
        self.assertBlocked(s.run(session="s2"))


if __name__ == "__main__":
    unittest.main()
