import json
import os
import sys
import unittest

SKILLS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(SKILLS, "phased-dev-loop", "tests"))
from background_cases import BackgroundTaskCases, HookTestCase, bash_started  # noqa: E402

STATE = os.path.join(".claude", "dev-flow", "x.state")


class BackgroundTaskTest(BackgroundTaskCases, HookTestCase):
    """临时项目里有一个环节为“编写方案”的状态文件，会话记录提到了它。"""

    GUARD = os.path.join(SKILLS, "dev-flow", "scripts", "flow_guard.py")
    MENTION = f"继续 {STATE}"

    def prepare(self, root):
        os.makedirs(os.path.dirname(os.path.join(root, STATE)))
        self.write_state("编写方案")

    def write_state(self, stage):
        with open(os.path.join(self.root, STATE), "w", encoding="utf-8") as f:
            f.write(f"环节: {stage}\n")

    def test_baseline_blocks(self):
        out = json.loads(self.baseline)
        self.assertEqual(out["decision"], "block")
        self.assertTrue(out["reason"].startswith("[dev-flow]"))

    def test_waiting_stage_allows_with_or_without_tasks(self):
        self.write_state("等待确认")
        self.assertAllows([])
        self.assertAllows([bash_started("bx1")])


if __name__ == "__main__":
    unittest.main()
