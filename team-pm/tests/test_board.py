import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import board  # noqa: E402
import board_store as bs  # noqa: E402


def make_repo(path):
    os.makedirs(path)
    subprocess.run(["git", "init", "-q", path], check=True)
    return path


class BoardTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["TEAM_BOARD"] = os.path.join(self.tmp.name, "board")
        self.repo = make_repo(os.path.join(self.tmp.name, "repo"))

    def tearDown(self):
        os.environ.pop("TEAM_BOARD", None)
        self.tmp.cleanup()

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = board.main(list(args))
        return code, out.getvalue()

    def new_project(self, name="demo", priority="P1"):
        return bs.create_project(name, self.repo, "需求, 方案, 交付", priority, "演示")


class ProjectTest(BoardTestCase):
    def test_create_project_writes_card(self):
        doc = self.new_project()
        f = doc.fields()
        self.assertEqual(f["阶段"], "立项")
        self.assertEqual(f["分支"], "feat/demo")
        self.assertTrue(f["工作目录"].endswith(".claude/worktrees/demo"))
        self.assertTrue(os.path.exists(os.path.join(os.environ["TEAM_BOARD"], "config.json")))

    def test_rejects_bad_name_duplicate_and_non_repo(self):
        with self.assertRaises(bs.BoardError):
            bs.create_project("Bad Name", self.repo, "", "P1", "")
        self.new_project()
        with self.assertRaises(bs.BoardError):
            self.new_project()
        with self.assertRaises(bs.BoardError):
            bs.create_project("other", self.tmp.name, "", "P1", "")

    def test_set_stage_records_history_and_validates(self):
        self.new_project()
        bs.set_project_field("demo", "阶段", "需求")
        doc = bs.load_project("demo")
        self.assertEqual(doc.get("阶段"), "需求")
        self.assertIn("立项 → 需求", doc.section("记录"))
        with self.assertRaises(bs.BoardError):
            bs.set_project_field("demo", "阶段", "瞎写")

    def test_project_doc_and_question(self):
        self.new_project()
        bs.set_project_doc("demo", "需求", "docs/prd/demo.md")
        bs.set_project_doc("demo", "需求", "docs/prd/demo-v2.md")
        bs.add_human_question("demo", "要不要支持部分退款？建议支持")
        doc = bs.load_project("demo")
        self.assertIn("需求: docs/prd/demo-v2.md", doc.section("文档"))
        self.assertNotIn("demo.md", doc.section("文档"))
        self.assertEqual(doc.section("待人确认"), "- [ ] 要不要支持部分退款？建议支持")


class ItemTest(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.new_project()

    def test_numbering_and_file_name(self):
        a = bs.create_item("demo", "产品", "需求", "写需求", "说明")
        b = bs.create_item("demo", "开发", "方案", "", "", deps="1")
        self.assertTrue(a.path.endswith("0001-product-prd.md"))
        self.assertTrue(b.path.endswith("0002-dev-design.md"))
        self.assertEqual(b.get("依赖"), "0001")
        self.assertEqual(bs.item_title(b), "0002 开发：方案")

    def test_rejects_unknown_dep_role_type(self):
        with self.assertRaises(bs.BoardError):
            bs.create_item("demo", "开发", "方案", "", "", deps="0009")
        with self.assertRaises(bs.BoardError):
            bs.create_item("demo", "设计师", "方案", "", "")
        with self.assertRaises(bs.BoardError):
            bs.create_item("demo", "开发", "上线", "", "")

    def test_claim_twice_needs_resume(self):
        bs.create_item("demo", "产品", "需求", "", "")
        bs.claim_item("demo", "1", "s1")
        with self.assertRaises(bs.BoardError):
            bs.claim_item("demo", "1", "s2")
        doc = bs.claim_item("demo", "1", "s2", resume=True)
        self.assertEqual(doc.get("会话"), "s2")

    def test_claim_finished_item_fails(self):
        bs.create_item("demo", "产品", "需求", "", "")
        bs.update_item("demo", "1", {"状态": "完成"})
        with self.assertRaises(bs.BoardError):
            bs.claim_item("demo", "1", resume=True)

    def test_done_writes_result_and_event(self):
        bs.create_item("demo", "产品", "需求", "", "")
        code, _ = self.run_cli("item", "done", "demo", "1", "--output", "docs/prd/demo.md",
                               "--result", "需求写完\n共 5 条验收标准")
        self.assertEqual(code, 0)
        doc = bs.load_item("demo", "1")
        self.assertEqual(doc.get("状态"), "完成")
        self.assertEqual(doc.get("产出"), "docs/prd/demo.md")
        self.assertEqual(doc.section("结果"), "需求写完\n共 5 条验收标准")
        events = bs.read_events()
        self.assertEqual([(e["序号"], e["事件"], e["说明"]) for e in events], [(1, "完成", "需求写完")])

    def test_block_and_ask_human(self):
        bs.create_item("demo", "开发", "方案", "", "")
        bs.create_item("demo", "开发", "开发", "", "")
        self.run_cli("item", "block", "demo", "1", "--reason", "等 0003 答复")
        self.run_cli("item", "ask-human", "demo", "2", "--question", "选 A 还是 B？建议 A")
        self.assertEqual(bs.load_item("demo", "1").get("状态"), "阻塞")
        self.assertEqual(bs.load_item("demo", "2").get("状态"), "待人确认")
        self.assertEqual([e["事件"] for e in bs.read_events(since=1)], ["待人确认"])

    def test_set_rejects_bad_status(self):
        bs.create_item("demo", "开发", "方案", "", "")
        code, _ = self.run_cli("item", "set", "demo", "1", "状态", "差不多")
        self.assertEqual(code, 1)

    def test_concurrent_creates_get_unique_numbers(self):
        threads = [threading.Thread(target=bs.create_item, args=("demo", "QA", "测试", "", ""))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(bs.item_files("demo")), 8)


class FilterAndResolveTest(BoardTestCase):
    def test_list_filters_by_type_and_source(self):
        self.new_project()
        bs.create_item("demo", "QA", "测试", "", "")
        bs.create_item("demo", "开发", "修复", "a", "", source="1")
        bs.create_item("demo", "开发", "修复", "b", "", source="0009")
        bs.create_item("demo", "QA", "测试", "回归", "", source="0002,0003")
        _, out = self.run_cli("item", "list", "demo", "--type", "修复", "--from", "1")
        self.assertEqual([ln.split()[0] for ln in out.splitlines()], ["0002"])
        _, out = self.run_cli("item", "list", "demo", "--from", "3")
        self.assertIn("来源 0002,0003", out)

    def test_resolve_questions(self):
        self.new_project()
        for q in ("问题一", "问题二", "问题三"):
            bs.add_human_question("demo", q)
        bs.resolve_human_questions("demo", "2")
        bs.resolve_human_questions("demo", "2")  # 第二条未处理的现在是“问题三”
        self.assertEqual(bs.load_project("demo").section("待人确认").splitlines(),
                         ["- [ ] 问题一", "- [x] 问题二", "- [x] 问题三"])
        with self.assertRaises(bs.BoardError):
            bs.resolve_human_questions("demo", "5")
        self.assertEqual(bs.resolve_human_questions("demo", "all"), 1)

    def test_notify_disabled_by_default(self):
        _, out = self.run_cli("notify", "hi")
        self.assertIn("没开启", out)


class ReadyTest(BoardTestCase):
    def ready(self):
        return [(n, bs.item_number(d)) for n, d in board.ready_items()]

    def test_only_items_with_done_deps(self):
        self.new_project()
        bs.create_item("demo", "产品", "需求", "", "")
        bs.create_item("demo", "开发", "方案", "", "", deps="1")
        self.assertEqual(self.ready(), [("demo", "0001")])
        bs.update_item("demo", "1", {"状态": "完成"})
        self.assertEqual(self.ready(), [("demo", "0002")])

    def test_priority_order_and_paused_project(self):
        self.new_project("low", "P2")
        self.new_project("high", "P0")
        self.new_project("paused", "P0")
        for name in ("low", "high", "paused"):
            bs.create_item(name, "产品", "需求", "", "")
        bs.set_project_field("paused", "阶段", "暂停")
        self.assertEqual(self.ready(), [("high", "0001"), ("low", "0001")])

    def test_limits_and_single_code_item(self):
        self.new_project()
        for _ in range(3):
            bs.create_item("demo", "开发", "修复", "", "", deps="")
        # 未写依赖的单视为无依赖；同一项目只派一张代码类单
        self.assertEqual(self.ready(), [("demo", "0001")])
        bs.claim_item("demo", "1")
        self.assertEqual(self.ready(), [])
        bs.create_item("demo", "QA", "测试计划", "", "")
        self.assertEqual(self.ready(), [("demo", "0004")])
        bs.create_item("demo", "产品", "验收", "", "")
        bs.claim_item("demo", "4")
        self.assertEqual(self.ready(), [])  # 单项目上限 2

    def test_global_limit_and_human_items_skipped(self):
        cfg_path = os.path.join(os.environ["TEAM_BOARD"], "config.json")
        bs.ensure_board()
        with open(cfg_path, "w") as f:
            json.dump({"max_sessions": 1}, f)
        self.new_project("a")
        self.new_project("b")
        bs.create_item("a", "人", "确认", "", "")
        bs.create_item("a", "产品", "需求", "", "")
        bs.create_item("b", "产品", "需求", "", "")
        self.assertEqual(self.ready(), [("a", "0002")])


class StatusTest(BoardTestCase):
    def test_status_lists_open_items(self):
        self.new_project()
        bs.create_item("demo", "产品", "需求", "写需求", "")
        bs.create_item("demo", "开发", "方案", "", "", deps="1")
        bs.update_item("demo", "1", {"状态": "完成"})
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("■ demo  阶段 立项", out)
        self.assertIn("0002 [待领取] 开发/方案", out)
        self.assertNotIn("0001 [", out)
        self.assertIn("已完成 1 张，共 2 张", out)


if __name__ == "__main__":
    unittest.main()
