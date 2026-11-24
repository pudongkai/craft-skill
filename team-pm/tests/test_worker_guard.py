import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import board_store as bs  # noqa: E402
import worker_guard as wg  # noqa: E402

GUARD = os.path.join(SCRIPTS, "worker_guard.py")


class WorkerGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = os.path.join(self.tmp.name, "board")
        os.environ["TEAM_BOARD"] = self.board
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        bs.create_project("demo", repo, "需求, 方案, 交付", "P1", "演示")
        bs.create_item("demo", "开发", "开发", "实现退款", "按方案开发")
        self.cwd = os.path.join(self.tmp.name, "work")
        os.makedirs(self.cwd)
        self.transcript = os.path.join(self.tmp.name, "session.jsonl")
        self.write_transcript([{"type": "user", "message": {"content": "开始"}}])

    def tearDown(self):
        os.environ.pop("TEAM_BOARD", None)
        self.tmp.cleanup()

    def write_transcript(self, rows):
        with open(self.transcript, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def set_status(self, status):
        bs.update_item("demo", 1, fields={"状态": status})

    def payload(self, **extra):
        return {"session_id": "s1", "transcript_path": self.transcript, "cwd": self.cwd, **extra}

    def run_hook(self, payload, *args, project="demo", item="1"):
        cmd = [sys.executable, GUARD, *args, "--project", project, "--item", item]
        proc = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True,
                              env={**os.environ, "TEAM_BOARD": self.board})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        return proc.stdout.strip()

    def events(self):
        return [e for e in bs.read_events() if e["事件"] == "会话停下"]


class StopTest(WorkerGuardTestCase):
    def test_missing_item_or_project_allows(self):
        self.assertEqual(self.run_hook(self.payload(), item="9"), "")
        self.assertEqual(self.run_hook(self.payload(), project="nope"), "")

    def test_stoppable_statuses_allow(self):
        bs.claim_item("demo", 1)
        for status in ["完成", "阻塞", "待人确认", "取消", "待领取"]:
            self.set_status(status)
            self.assertEqual(self.run_hook(self.payload()), "", status)

    def test_in_progress_blocks(self):
        bs.claim_item("demo", 1)
        out = json.loads(self.run_hook(self.payload()))
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("[team]"))
        self.assertIn("demo 0001", out["reason"])
        self.assertIn("item done demo 0001", out["reason"])
        self.assertIn("item ask-human", out["reason"])

    def test_pending_background_agent_allows(self):
        bs.claim_item("demo", 1)
        launched = {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": "abc"}}
        self.write_transcript([launched])
        self.assertEqual(self.run_hook(self.payload()), "")

    def test_finished_background_agent_blocks_again(self):
        bs.claim_item("demo", 1)
        launched = {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": "abc"}}
        done = {"type": "queue-operation",
                "content": "<task-notification><task-id>abc</task-id><status>completed</status>"}
        self.write_transcript([launched, done])
        self.assertEqual(json.loads(self.run_hook(self.payload()))["decision"], "block")

    def test_stalled_blocks_release_and_wake_pm(self):
        bs.claim_item("demo", 1)
        for _ in range(wg.MAX_STALLED_BLOCKS):
            self.assertEqual(json.loads(self.run_hook(self.payload()))["decision"], "block")
        self.assertEqual(self.events(), [])
        out = json.loads(self.run_hook(self.payload()))
        self.assertNotIn("decision", out)
        self.assertIn("[team]", out["systemMessage"])
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["项目"], events[0]["任务单"]), ("demo", "0001"))
        self.assertIn("连续 3 次", events[0]["说明"])

    def test_item_change_resets_stall_count(self):
        bs.claim_item("demo", 1)
        for _ in range(wg.MAX_STALLED_BLOCKS):
            self.run_hook(self.payload())
        bs.update_item("demo", 1, result="做到一半")
        self.assertEqual(json.loads(self.run_hook(self.payload()))["decision"], "block")

    def write_running_plan(self):
        state = os.path.join(self.cwd, ".claude", "dev-loop")
        os.makedirs(state)
        with open(os.path.join(state, "ACTIVE"), "w", encoding="utf-8") as f:
            f.write("plan.md\n")
        with open(os.path.join(self.cwd, "plan.md"), "w", encoding="utf-8") as f:
            f.write("# 方案\n\n开发状态: RUNNING\n\n## 开发计划\n\n- [ ] 阶段1：实现\n")
        self.write_transcript([{"type": "user", "message": {"content": "开发 plan.md"}}])

    def test_forwards_stop_guard_output(self):
        bs.claim_item("demo", 1)
        self.write_running_plan()
        out = json.loads(self.run_hook(self.payload()))
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("[phased-dev-loop]"))

    def test_finished_dev_item_not_held_by_running_plan(self):
        bs.claim_item("demo", 1)
        self.write_running_plan()
        self.set_status("阻塞")
        self.assertEqual(self.run_hook(self.payload()), "")

    def test_non_code_item_skips_stop_guard(self):
        bs.create_item("demo", "产品", "答疑", "", "")
        bs.claim_item("demo", 2)
        self.write_running_plan()
        out = json.loads(self.run_hook(self.payload(), item="2"))
        self.assertTrue(out["reason"].startswith("[team]"))
        bs.update_item("demo", 2, fields={"状态": "完成"})
        self.assertEqual(self.run_hook(self.payload(), item="2"), "")

    def test_stop_guard_release_wakes_pm(self):
        bs.claim_item("demo", 1)
        self.write_running_plan()
        for _ in range(3):
            self.run_hook(self.payload())
        out = json.loads(self.run_hook(self.payload()))
        self.assertIn("[phased-dev-loop]", out["systemMessage"])
        self.assertEqual(len(self.events()), 1)

    def test_bad_args_do_not_block(self):
        proc = subprocess.run([sys.executable, GUARD, "--project", "demo"], input="{}",
                              capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 2)

    def test_error_allows(self):
        bs.claim_item("demo", 1)
        stdin = io.StringIO(json.dumps(self.payload()))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(wg, "decide_stop", side_effect=RuntimeError("坏了")), \
                mock.patch.object(wg, "run_stop_guard", return_value=""), \
                mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            wg.main(["--project", "demo", "--item", "1"])
        self.assertEqual(out.getvalue(), "")
        self.assertIn("坏了", err.getvalue())


class NotificationTest(WorkerGuardTestCase):
    def notify(self, **fields):
        return self.run_hook(self.payload(**fields), "--event", "notification")

    def test_permission_prompt_writes_event(self):
        self.assertEqual(self.notify(notification_type="permission_prompt",
                                     message="Claude needs your permission to use Bash"), "")
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["任务单"], "0001")
        self.assertIn("permission to use Bash", events[0]["说明"])
        self.assertIn("claude attach", events[0]["说明"])

    def test_elicitation_and_needs_input_write_events(self):
        self.notify(notification_type="elicitation_dialog", message="请选择")
        self.notify(type="agent_needs_input", message="需要输入")
        self.assertEqual(len(self.events()), 2)

    def test_legacy_permission_message_without_type(self):
        self.notify(message="Claude needs your permission to use Edit")
        self.assertEqual(len(self.events()), 1)

    def test_unrelated_types_ignored(self):
        self.notify(notification_type="idle_prompt", message="Claude is waiting for your input")
        self.notify(notification_type="auth_success", message="ok")
        self.notify(message="Claude is waiting for your input")
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
