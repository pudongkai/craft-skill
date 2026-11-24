import json
import os
import unittest

from background_cases import (BackgroundTaskCases, HookTestCase, agent_started, bash_started, notification,
                              queue_done, task_stopped)

PLAN = "docs/plan.md"


class BackgroundTaskTest(BackgroundTaskCases, HookTestCase):
    """临时项目里有一份 RUNNING、还有未完成阶段的方案，会话记录提到了它。"""

    GUARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "stop_guard.py")
    MENTION = f"按 {PLAN} 开发"

    def prepare(self, root):
        state = os.path.join(root, ".claude", "dev-loop")
        os.makedirs(state)
        os.makedirs(os.path.join(root, "docs"))
        with open(os.path.join(state, "ACTIVE"), "w", encoding="utf-8") as f:
            f.write(PLAN + "\n")
        with open(os.path.join(root, PLAN), "w", encoding="utf-8") as f:
            f.write("# 方案\n\n开发状态: RUNNING\n\n## 开发计划\n\n- [x] 阶段1：准备\n- [ ] 阶段2：实现\n")

    def test_baseline_blocks(self):
        out = json.loads(self.baseline)
        self.assertEqual(out["decision"], "block")
        self.assertIn("阶段2：实现", out["reason"])

    def test_task_id_without_success_message_still_allows(self):
        other = {"type": "user", "toolUseResult": {"task_id": "bx1", "message": "No task found"}}
        self.assertAllows([bash_started("bx1"), other])

    def test_task_stop_on_agent_blocks(self):
        self.assertBlocks([agent_started("a1f"), task_stopped("a1f")])

    def test_notification_does_not_cross_next_task(self):
        content = notification("bfirst", "running") + notification("bsecond")
        rows = [bash_started("bfirst"), bash_started("bsecond"), {"type": "queue-operation", "content": content}]
        self.assertAllows(rows)
        self.assertBlocks(rows + [queue_done("bfirst")])

    def test_malformed_fields_are_ignored(self):
        rows = [
            {"type": "user", "toolUseResult": {"backgroundTaskId": 123}},
            {"type": "user", "toolUseResult": {"status": "async_launched", "agentId": ["x"]}},
            {"type": "attachment", "attachment": "backgroundTaskId"},
            {"type": "queue-operation", "content": ["<task-id>bx1</task-id>"]},
        ]
        self.assertBlocks(rows)
        self.assertAllows(rows + [bash_started("bx1")])


if __name__ == "__main__":
    unittest.main()
