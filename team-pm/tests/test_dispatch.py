import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import board_dispatch as bd  # noqa: E402
import board_store as bs  # noqa: E402

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_CLAUDE_DIR"]
with open(os.path.join(d, "calls.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
if sys.argv[1:3] == ["agents", "--json"]:
    sys.stderr.write("update available\\n")
    print(open(os.path.join(d, "agents.json")).read())
elif "--bg" in sys.argv:
    if os.path.exists(os.path.join(d, "untrusted")):
        print("Workspace not trusted. Run `claude` in x once"); sys.exit(1)
    print("backgrounded · " + open(os.path.join(d, "next_id")).read().strip() + " · name")
"""


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        os.environ["TEAM_BOARD"] = os.path.join(t, "board")
        self.fake = os.path.join(t, "fake")
        os.makedirs(self.fake)
        with open(os.path.join(self.fake, "claude"), "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(os.path.join(self.fake, "claude"), 0o755)
        os.environ["FAKE_CLAUDE_DIR"] = self.fake
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = self.fake + os.pathsep + self.old_path
        self.set_agents([])
        self.set_next_id("abc123")
        self.repo = os.path.join(t, "repo")
        os.makedirs(self.repo)
        for cmd in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], cwd=self.repo, check=True)
        bs.create_project("demo", self.repo, "需求, 方案, 交付", "P1", "")
        bs.create_item("demo", "产品", "需求", "写需求", "")

    def tearDown(self):
        os.environ["PATH"] = self.old_path
        for k in ("TEAM_BOARD", "FAKE_CLAUDE_DIR"):
            os.environ.pop(k, None)
        self.tmp.cleanup()

    def set_agents(self, rows):
        with open(os.path.join(self.fake, "agents.json"), "w") as f:
            json.dump(rows, f)

    def set_next_id(self, sid):
        with open(os.path.join(self.fake, "next_id"), "w") as f:
            f.write(sid)

    def calls(self, kind="--bg"):
        with open(os.path.join(self.fake, "calls.jsonl")) as f:
            rows = [json.loads(line) for line in f]
        return [r for r in rows if kind in r["argv"]]

    def test_dispatch_creates_worktree_and_launches(self):
        sid, resumed = bd.dispatch("demo", "1")
        self.assertEqual((sid, resumed), ("abc123", False))
        p = bs.load_project("demo")
        self.assertTrue(os.path.isdir(p.get("工作目录")))
        self.assertTrue(p.get("基线"))
        item = bs.load_item("demo", "1")
        self.assertEqual((item.get("状态"), item.get("会话")), ("进行中", "abc123"))
        call = self.calls()[0]
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(p.get("工作目录")))
        argv = call["argv"]
        self.assertEqual(argv[argv.index("-n") + 1], "team-demo-0001")
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIn("--project demo --item 0001", settings["hooks"]["Stop"][0]["hooks"][0]["command"])
        self.assertIn("--event notification", settings["hooks"]["Notification"][0]["hooks"][0]["command"])
        self.assertIn(settings["theme"], ("light", "dark"))
        self.assertIn("item claim demo 0001 --resume", argv[-1])
        self.assertIn("team-product", argv[-1])
        exclude = os.path.join(self.repo, ".git", "info", "exclude")
        self.assertIn(".claude/worktrees/", bs.read_file(exclude))

    def test_dispatch_reuses_existing_branch(self):
        subprocess.run(["git", "branch", "feat/demo"], cwd=self.repo, check=True)
        bd.dispatch("demo", "1")
        self.assertTrue(os.path.isdir(bs.load_project("demo").get("工作目录")))

    def test_running_item_needs_retry_and_retry_limit(self):
        bd.dispatch("demo", "1")
        with self.assertRaises(bs.BoardError):
            bd.dispatch("demo", "1")
        bd.dispatch("demo", "1", retry=True)
        bd.dispatch("demo", "1", retry=True)
        self.assertEqual(bs.load_item("demo", "1").get("重试"), "2")
        with self.assertRaisesRegex(bs.BoardError, "已转为阻塞"):
            bd.dispatch("demo", "1", retry=True)
        self.assertEqual(bs.load_item("demo", "1").get("状态"), "阻塞")
        self.assertEqual(bs.read_events()[-1]["事件"], "阻塞")

    def test_redispatch_uses_new_session_with_continue_prompt(self):
        bd.dispatch("demo", "1")
        self.assertNotIn("这张单以前已经有会话", self.calls()[-1]["argv"][-1])
        bs.update_item("demo", "1", {"状态": "待人确认"})
        bs.reply_item("demo", "1", "人选了方案 A")
        self.set_agents([{"id": "abc123", "sessionId": "full-abc", "state": "done"}])
        _, continued = bd.dispatch("demo", "1")
        self.assertTrue(continued)
        argv = self.calls()[-1]["argv"]
        self.assertNotIn("--resume", argv)
        self.assertEqual(argv[argv.index("--autocompact") + 1], "auto")
        self.assertIn("这张单以前已经有会话", argv[-1])
        self.assertIn("item claim demo 0001", argv[-1])

    def test_alive_old_session_stopped_before_redispatch(self):
        bd.dispatch("demo", "1")
        self.set_agents([{"id": "abc123", "sessionId": "full-abc", "pid": os.getpid(), "state": "done"}])
        bd.dispatch("demo", "1", retry=True)
        self.assertEqual(self.calls("stop")[-1]["argv"], ["stop", "abc123"])
        self.assertNotIn("--resume", self.calls()[-1]["argv"])

    def test_missing_claude_is_board_error(self):
        os.environ["PATH"] = "/nonexistent"
        with self.assertRaisesRegex(bs.BoardError, "找不到命令"):
            bd.list_sessions()

    def test_exit_reported_again_after_redispatch(self):
        bd.dispatch("demo", "1")
        bs.update_item("demo", "1", {"派出时间": "2020-01-01 00:00:00"})
        self.set_agents([{"id": "abc123", "sessionId": "full-abc", "state": "done"}])
        bd.reap()
        bd.reap()
        self.assertEqual(len(bs.read_events()), 1)
        bd.dispatch("demo", "1", retry=True)
        bs.update_item("demo", "1", {"派出时间": "2020-01-01 00:05:00"})
        bd.reap()
        self.assertEqual([e["事件"] for e in bs.read_events()], ["会话退出", "会话退出"])
        bs.update_item("demo", "1", {"状态": "完成"})
        bd.reap()
        self.assertEqual(json.load(open(bd.reap_state_path())), [])

    def test_hook_reported_stall_not_reported_again(self):
        bd.dispatch("demo", "1")
        bd.mark_reported("demo", bs.load_item("demo", "1"))
        bs.update_item("demo", "1", {"派出时间": "2020-01-01 00:00:00"})
        bd.mark_reported("demo", bs.load_item("demo", "1"))
        self.set_agents([{"id": "abc123", "pid": os.getpid(), "state": "done"}])
        bd.reap()  # 会话还开着时记录也要保留
        self.set_agents([{"id": "abc123", "state": "done"}])
        bd.reap()
        self.assertEqual(bs.read_events(), [])

    def test_reply_resets_retry(self):
        bd.dispatch("demo", "1")
        bd.dispatch("demo", "1", retry=True)
        bs.update_item("demo", "1", {"状态": "阻塞"})
        bs.reply_item("demo", "1", "人已处理")
        self.assertEqual(bs.load_item("demo", "1").get("重试"), "0")

    def test_human_item_and_untrusted_repo(self):
        bs.create_item("demo", "人", "确认", "", "")
        with self.assertRaises(bs.BoardError):
            bd.dispatch("demo", "2")
        open(os.path.join(self.fake, "untrusted"), "w").close()
        with self.assertRaisesRegex(bs.BoardError, "信任"):
            bd.dispatch("demo", "1")
        self.assertEqual(bs.load_item("demo", "1").get("状态"), "待领取")

    def test_reap_reports_exit_once_and_stops_finished(self):
        bd.dispatch("demo", "1")
        bs.create_item("demo", "QA", "测试计划", "", "")
        self.set_next_id("def456")
        bd.dispatch("demo", "2")
        bs.update_item("demo", "2", {"状态": "完成"})
        self.set_agents([{"id": "abc123", "state": "done"},
                         {"id": "def456", "pid": os.getpid(), "state": "done"}])
        self.assertEqual(len(bd.reap()), 1)  # 刚派出的单不检查退出
        self.assertEqual(bs.read_events(), [])
        bs.update_item("demo", "1", {"派出时间": "2020-01-01 00:00:00"})
        notes = bd.reap()
        self.assertEqual(len(notes), 2)
        self.assertFalse(bd.session_alive({"state": "done"}))
        self.assertTrue(bd.session_alive({"state": "working"}))
        self.assertEqual([e["事件"] for e in bs.read_events()], ["会话退出"])
        self.assertEqual(self.calls("stop")[-1]["argv"], ["stop", "def456"])
        bd.reap()
        self.assertEqual(len(bs.read_events()), 1)

    def test_wait_returns_new_events_and_advances_cursor(self):
        bs.append_event("demo", "0001", "完成", "x")
        events = bd.wait(timeout=5, poll=0.1)
        self.assertEqual([e["序号"] for e in events], [1])
        self.assertEqual(bd.wait(timeout=0.3, poll=0.1), [])
        bs.append_event("demo", "0001", "阻塞", "y")
        self.assertEqual([e["事件"] for e in bd.wait(timeout=5, poll=0.1)], ["阻塞"])


if __name__ == "__main__":
    unittest.main()
