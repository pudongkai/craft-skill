"""三个 Stop hook 共用的后台任务测试：会话记录行的构造函数、跑 hook 的夹具和放行 / 拦截用例。

用法：测试类同时继承 BackgroundTaskCases 和 unittest.TestCase，并实现
assertAllows(rows, raw_lines=()) / assertBlocks(rows, transcript=None)。rows 是写在会话记录里的行（dict），
raw_lines 是写在 rows 前面的原始文本行，transcript 指定另一份会话记录路径。
assertBlocks 应确认输出与没有后台任务时的拦截输出一致。HookTestCase 提供了一份通用实现。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest


def bash_started(tid, **extra):
    return {"type": "user", "toolUseResult": {"stdout": "", "backgroundTaskId": tid, **extra}}


def agent_started(aid):
    return {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": aid}}


def notification(tid, status="completed"):
    return (f"<task-notification>\n<task-id>{tid}</task-id>\n<tool-use-id>toolu_1</tool-use-id>\n"
            f"<output-file>/tmp/{tid}.output</output-file>\n<status>{status}</status>\n<summary>done</summary>")


def queue_done(tid, status="completed"):
    return {"type": "queue-operation", "content": notification(tid, status)}


def attachment_done(tid, status="completed"):
    return {"type": "attachment", "attachment": {"prompt": notification(tid, status)}}


def task_stopped(tid):
    return {"type": "user", "toolUseResult": {"message": f"Successfully stopped task: {tid} (sleep 100)",
                                              "task_id": tid, "task_type": "local_bash", "command": "sleep 100"}}


class BackgroundTaskCases:
    def test_bash_started_without_end_allows(self):
        self.assertAllows([bash_started("b6r5u7d58")])

    def test_bash_timed_out_or_user_backgrounded_allows(self):
        self.assertAllows([bash_started("bto1", timedOutAfterMs=120000)])
        self.assertAllows([bash_started("bus1", backgroundedByUser=True)])

    def test_bash_queue_notification_blocks(self):
        self.assertBlocks([bash_started("bls6cfshm"), queue_done("bls6cfshm")])

    def test_bash_attachment_notification_blocks(self):
        self.assertBlocks([bash_started("bls6cfshm"), attachment_done("bls6cfshm")])

    def test_bash_other_end_statuses_block(self):
        for status in ["failed", "killed", "stopped"]:
            with self.subTest(status=status):
                self.assertBlocks([bash_started("bx1"), queue_done("bx1", status)])

    def test_bash_notification_before_start_blocks(self):
        self.assertBlocks([queue_done("bfast1"), bash_started("bfast1")])

    def test_bash_task_stop_blocks(self):
        self.assertBlocks([bash_started("b4e03i4ah"), task_stopped("b4e03i4ah")])

    def test_two_commands_one_ended_allows_then_both_block(self):
        rows = [bash_started("ba1"), bash_started("bb2"), queue_done("ba1")]
        self.assertAllows(rows)
        self.assertBlocks(rows + [queue_done("bb2")])

    def test_pending_bash_with_finished_agent_allows(self):
        self.assertAllows([agent_started("a1f"), queue_done("a1f"), bash_started("bx1")])

    def test_pending_agent_with_finished_bash_allows(self):
        self.assertAllows([agent_started("a1f"), bash_started("bx1"), queue_done("bx1")])

    def test_all_tasks_finished_blocks(self):
        self.assertBlocks([agent_started("a1f"), bash_started("bx1"), queue_done("bx1"), queue_done("a1f")])

    def test_missing_transcript_blocks(self):
        self.assertBlocks([], transcript=os.path.join(self.tmp.name, "missing.jsonl"))

    def test_bad_lines_are_skipped(self):
        self.assertAllows([bash_started("bx1")], raw_lines=['{"backgroundTaskId": broken', '["backgroundTaskId"]'])


class HookTestCase(unittest.TestCase):
    """以子进程跑 GUARD，会话记录首行是 MENTION；子类在 prepare(root) 里准备项目文件。"""

    GUARD = None
    MENTION = ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.root)
        self.prepare(self.root)
        self.transcript = os.path.join(self.tmp.name, "session.jsonl")
        self.session = 0
        self.baseline = self.run_hook([])

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, root):
        raise NotImplementedError

    def run_hook(self, rows, transcript=None, raw_lines=()):
        """每次用新的 session_id，避免连续拦截计数影响结果。"""
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"content": self.MENTION}}, ensure_ascii=False) + "\n")
            for line in raw_lines:
                f.write(line + "\n")
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.session += 1
        payload = {"session_id": f"s{self.session}", "cwd": self.root,
                   "transcript_path": transcript or self.transcript}
        proc = subprocess.run([sys.executable, self.GUARD], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return proc.stdout.strip()

    def assertAllows(self, rows, raw_lines=()):
        self.assertEqual(self.run_hook(rows, raw_lines=raw_lines), "")

    def assertBlocks(self, rows, transcript=None):
        self.assertEqual(self.run_hook(rows, transcript=transcript), self.baseline)
