import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import board_dispatch as bd  # noqa: E402
import board_pm as bp  # noqa: E402
import board_store as bs  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


class PmTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TEAM_BOARD"] = os.path.join(self.tmp.name, "board")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")

    def tearDown(self):
        os.environ.pop("TEAM_BOARD", None)
        self.tmp.cleanup()


class RegistryTest(PmTestCase):
    def test_find_only_alive_pm(self):
        bp.register_pm(self.repo, "/dev/ttys999")
        with mock.patch.object(bp, "terminal_alive", return_value=True):
            self.assertEqual(bp.find_pm(self.repo + "/"), "/dev/ttys999")
        with mock.patch.object(bp, "terminal_alive", return_value=False):
            self.assertIsNone(bp.find_pm(self.repo))
        self.assertIsNone(bp.find_pm(os.path.join(self.tmp.name, "other")))

    def test_dead_terminals(self):
        self.assertFalse(bp.terminal_alive("/dev/ttys999"))
        self.assertFalse(bp.terminal_alive("no-such-tmux-session-xyz"))

    def test_project_new_registers_current_pm(self):
        import board
        with mock.patch.object(bp, "current_claude_terminal", return_value="/dev/ttys123"):
            board.main(["project", "new", "demo", "--repo", self.repo])
        self.assertEqual(bp.load_pm()[os.path.realpath(self.repo)]["终端"], "/dev/ttys123")


class ExportImportTest(PmTestCase):
    def test_roundtrip_localizes_paths_and_resets_running(self):
        bs.create_project("demo", self.repo, "无", "P1", "")
        bs.set_project_field("demo", "基线", "abc1234")
        bs.create_item("demo", "产品", "需求", "", "")
        bs.create_item("demo", "开发", "方案", "", "", deps="1")
        bs.update_item("demo", "1", {"状态": "完成"})
        archive = os.path.join(self.tmp.name, "demo.tgz")
        bs.claim_item("demo", "2", "s1")
        with self.assertRaisesRegex(bs.BoardError, "进行中"):
            bp.export_project("demo", archive)
        bs.update_item("demo", "2", {"状态": "待领取"})
        bp.export_project("demo", archive)

        os.environ["TEAM_BOARD"] = os.path.join(self.tmp.name, "board2")
        other = os.path.join(self.tmp.name, "other-repo")
        self.assertEqual(bp.import_project(archive, other), "demo")
        p = bs.load_project("demo")
        self.assertEqual(p.get("仓库"), other)
        self.assertEqual(p.get("工作目录"), os.path.join(other, ".claude", "worktrees", "demo"))
        self.assertEqual(p.get("基线"), "abc1234")
        self.assertEqual([d.get("状态") for d in bs.list_items("demo")], ["完成", "待领取"])
        with self.assertRaisesRegex(bs.BoardError, "已有项目"):
            bp.import_project(archive, other)


class WorktreeFromRemoteTest(PmTestCase):
    def test_tracks_remote_branch(self):
        origin = self.repo
        git(origin, "checkout", "-q", "-b", "feat/demo")
        git(origin, "commit", "-q", "--allow-empty", "-m", "work on demo")
        git(origin, "checkout", "-q", "-")
        clone = os.path.join(self.tmp.name, "clone")
        git(self.tmp.name, "clone", "-q", origin, clone)
        workdir = os.path.join(clone, ".claude", "worktrees", "demo")
        cmd = bd.worktree_command(clone, workdir, "feat/demo")
        self.assertIn("origin/feat/demo", cmd)
        subprocess.run(cmd, check=True, capture_output=True)
        log = subprocess.run(["git", "-C", workdir, "log", "--oneline", "-1"], capture_output=True, text=True)
        self.assertIn("work on demo", log.stdout)


if __name__ == "__main__":
    unittest.main()
