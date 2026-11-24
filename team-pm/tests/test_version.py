"""board.py version 子命令的测试，用例编号对应 docs/test/board-version.md。"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
BOARD = os.path.join(SCRIPTS, "board.py")
sys.path.insert(0, SCRIPTS)
import board  # noqa: E402


def snapshot(root):
    """看板目录下每个文件的相对路径 → (内容, 修改时间)。"""
    result = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                result[os.path.relpath(p, root)] = (fh.read(), os.stat(p).st_mtime_ns)
    return result


class VersionCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = os.path.realpath(self.tmp.name)
        self.home = os.path.join(self.t, "home")
        os.makedirs(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def run_board(self, *args, board_env=None, cwd=None, script=BOARD):
        env = {k: v for k, v in os.environ.items() if k != "TEAM_BOARD"}
        env["HOME"] = self.home
        if board_env is not None:
            env["TEAM_BOARD"] = board_env
        return subprocess.run([sys.executable, script, *args], env=env, cwd=cwd,
                              capture_output=True, text=True)

    def test_tc1_default_root(self):
        r = self.run_board("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(),
                         ["版本: 0.1.0", f"看板根目录: {self.home}/.claude/team"])
        self.assertNotIn("~", r.stdout)

    def test_tc3_tc17_team_board_absolute_full_output(self):
        root = os.path.join(self.t, "xx-board")
        r = self.run_board("version", board_env=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"版本: 0.1.0\n看板根目录: {root}\n")
        self.assertEqual(r.stderr, "")

    def test_tc4_tilde_expanded(self):
        r = self.run_board("version", board_env="~/my-board")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[1], f"看板根目录: {self.home}/my-board")
        self.assertNotIn("~", r.stdout)

    def test_tc5_missing_dir_not_created(self):
        root = os.path.join(self.t, "not-exist")
        r = self.run_board("version", board_env=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(root))

    def test_tc6_existing_board_unchanged(self):
        root = os.path.join(self.t, "board")
        repo = os.path.join(self.t, "repo")
        subprocess.run(["git", "init", "-q", repo], check=True)
        for args in (["project", "new", "demo", "--repo", repo, "--goal", "测试"],
                     ["item", "new", "demo", "--role", "开发", "--type", "开发", "--title", "a"],
                     ["item", "new", "demo", "--role", "QA", "--type", "测试", "--title", "b"],
                     ["item", "claim", "demo", "0001"],
                     ["item", "block", "demo", "0001", "--reason", "等答复"]):
            r = self.run_board(*args, board_env=root)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(root, "events.jsonl")))
        before = snapshot(root)
        r = self.run_board("version", board_env=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(snapshot(root), before)

    def test_tc7_empty_board_stays_empty(self):
        root = os.path.join(self.t, "empty")
        os.makedirs(root)
        r = self.run_board("version", board_env=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(root), [])

    def test_tc8_output_follows_constant(self):
        copy = os.path.join(self.t, "scripts")
        shutil.copytree(SCRIPTS, copy, ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(copy, "board.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('VERSION = "0.1.0"', src)
        with open(path, "w", encoding="utf-8") as f:
            f.write(src.replace('VERSION = "0.1.0"', 'VERSION = "0.2.0"'))
        r = self.run_board("version", board_env=os.path.join(self.t, "b"), script=path)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "版本: 0.2.0")

    def test_tc9_version_defined_once(self):
        hits = []
        for name in sorted(os.listdir(SCRIPTS)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as f:
                for no, line in enumerate(f, 1):
                    if re.search(r"0\.1\.0", line):
                        hits.append((name, no, line.strip()))
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0][0], "board.py")
        self.assertTrue(hits[0][2].startswith("VERSION ="), hits)

    def test_tc10_constant_format(self):
        self.assertRegex(board.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(board.VERSION, "0.1.0")

    def test_tc11_help_lists_version(self):
        r = self.run_board("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        idx = [i for i, l in enumerate(lines) if re.match(r"^\s+version\b", l)]
        self.assertTrue(idx, r.stdout)
        text = " ".join(lines[idx[0]:idx[0] + 2])
        self.assertRegex(text, r"[一-鿿]")

    def test_tc12_extra_positional_rejected(self):
        r = self.run_board("version", "extra", board_env=os.path.join(self.t, "b"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage:", r.stderr)
        self.assertIn("unrecognized arguments: extra", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_tc13_unknown_option_rejected(self):
        r = self.run_board("version", "--foo", board_env=os.path.join(self.t, "b"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage:", r.stderr)
        self.assertIn("--foo", r.stderr)

    def test_tc14_empty_team_board_uses_default(self):
        r = self.run_board("version", board_env="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[1], f"看板根目录: {self.home}/.claude/team")

    def test_tc15_relative_team_board(self):
        cwd = os.path.join(self.t, "cwd")
        os.makedirs(cwd)
        r = self.run_board("version", board_env="rel-board", cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[1], f"看板根目录: {cwd}/rel-board")

    def test_tc16_missing_parents_not_created(self):
        r = self.run_board("version", board_env=os.path.join(self.t, "a", "b", "c"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.t, "a")))


if __name__ == "__main__":
    unittest.main()
