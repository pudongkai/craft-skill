# Stop hook 识别后台 Bash 命令测试报告

项目: stop-guard-bg
作者: QA（团队角色）　　日期: 2026-11-24
测试计划: docs/test/stop-guard-bg.md
测试单: 0006

## 1. 概要结论

通过

- 用例：通过 26，不通过 0，阻塞 0
- 全量测试：`python3 -m unittest discover -s team-pm/tests` → 85 条全部通过（含 QA 新增 25 条）；`phased-dev-loop/tests` 18 条、`dev-flow/tests` 15 条全部通过

## 2. 执行环境

- 提交号: e057999（开发单 0005 的提交 d79160e、e057999；测试代码在其上提交）
- 分支: feat/stop-guard-bg
- 环境: macOS（Darwin 24.6.0），python3 标准库 unittest，git
- 新增的测试代码: `team-pm/tests/test_guard_bg_bash.py`（TC-1～TC-22、TC-26 和两条回归用例，共 25 个测试方法）
- 测试数据：每个场景在临时目录里现造方案 / dev-flow 状态文件 / 看板任务单和会话记录，用 subprocess 调用三个 hook
- 测试有效性检查：把同一测试文件放到改动前的代码（504c1e1）上运行，42 处失败（期望放行的用例全部失败），说明测试能发现缺少该功能的情况

## 3. 用例结果

下文“测试文件”指 `team-pm/tests/test_guard_bg_bash.py`，运行命令 `python3 -m unittest team-pm/tests/test_guard_bg_bash.py` → `Ran 25 tests ... OK`。每个用例对 stop_guard、flow_guard、worker_guard（非代码单）三个 hook 各跑一遍（subTest）。

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| TC-1 | 通过 | 测试文件::test_tc01_pending_bash_allows |
| TC-2 | 通过 | 测试文件::test_tc02_timed_out_to_background_allows |
| TC-3 | 通过 | 测试文件::test_tc03_queue_notification_blocks_like_control（decision、reason 与无后台任务的对照逐字相同） |
| TC-4 | 通过 | 测试文件::test_tc04_attachment_notification_blocks |
| TC-5 | 通过 | 测试文件::test_tc05_other_end_statuses_block（failed / killed / stopped） |
| TC-6 | 通过 | 测试文件::test_tc06_task_stop_blocks |
| TC-7 | 通过 | 测试文件::test_tc07_two_commands_one_ended |
| TC-8 | 通过 | 测试文件::test_tc08_pending_bash_with_finished_agent_allows |
| TC-9 | 通过 | 测试文件::test_tc09_pending_agent_with_finished_bash_allows |
| TC-10 | 通过 | 测试文件::test_tc10_all_finished_blocks_like_control |
| TC-11 | 通过 | 测试文件::test_tc11_not_blocking_state_without_tasks_allows |
| TC-12 | 通过 | 测试文件::test_tc12_not_blocking_state_with_pending_bash_allows |
| TC-13 | 通过 | 测试文件::test_tc13_missing_transcript_blocks（退出码 0、stderr 为空、拦截） |
| TC-14 | 通过 | 测试文件::test_tc14_bad_lines_around_start_allow |
| TC-15 | 通过 | 测试文件::test_tc15_bad_line_between_start_and_end_blocks |
| TC-16 | 通过 | 测试文件::test_tc16_non_hex_ids（`bZ9-x_7`、`B6R5U7D58`） |
| TC-17 | 通过 | 测试文件::test_tc17_worker_code_item_forwards_to_stop_guard（拦截内容以 `[phased-dev-loop]` 开头） |
| TC-18 | 通过 | 测试文件::test_tc18_worker_release_writes_no_stop_event（5 次放行，“会话停下”事件数不变） |
| TC-19 | 通过 | 测试文件::test_tc19_foreground_bash_and_text_mention_not_tasks |
| TC-20 | 通过 | 测试文件::test_tc20_other_id_or_running_status_still_pending |
| TC-21 | 通过 | 测试文件::test_tc21_failed_task_stop_not_end（字符串错误结果、不含 task_id 的 dict、is_error 的 tool_result 三种） |
| TC-22 | 通过 | 测试文件::test_tc22_pending_release_not_counted_as_stall（4 次放行后第 5 次为 block，无 systemMessage） |
| TC-23 | 通过 | 在本测试会话里后台起 `sleep 30`（bzb32h6vb）和 `sleep 600`（bdfk94rfp），TaskStop 停掉后者。会话记录 `~/.claude/projects/-Users-dongkaipu-workspace-craft-skill--claude-worktrees-stop-guard-bg/0c3306a2-565d-4b17-84b9-fa1db7599d37.jsonl`：第 130、131 行 user 的 `toolUseResult` 含 `backgroundTaskId`（键为 stdout、stderr、interrupted、isImage、noOutputExpected、backgroundTaskId）；第 147 行 `{"message":"Successfully stopped task: bdfk94rfp (...)","task_id":"bdfk94rfp","task_type":"local_bash","command":...}`；第 152、158 行 queue-operation、第 159 行 attachment 含 `<task-id>bzb32h6vb</task-id>`、`<status>completed</status>`。字段与造数一致。另用 `scan_transcript` 读这份真实记录：截到第 131 行时未结束任务数 2，整份记录为 0 |
| TC-24 | 通过 | `grep -n "import\|def " dev-flow/scripts/flow_guard.py team-pm/scripts/worker_guard.py`：flow_guard 第 19 行 `from stop_guard import read_text, session_target, stalled_count`，worker_guard 第 26 行 `from stop_guard import read_text, scan_transcript, stalled_count`；两文件 grep `backgroundTaskId\|task-notification\|task_id\|task-id` 无结果；`git diff 504c1e1 -- dev-flow/scripts team-pm/scripts` 只改了文档字符串各一行 |
| TC-25 | 通过 | 全量命令见第 1 节。开发新增测试 `phased-dev-loop/tests/background_cases.py` 的 BackgroundTaskCases 被 test_stop_guard.py、test_flow_guard.py、test_worker_guard.py 三处继承，覆盖 AC-1（test_bash_started_without_end_allows）、AC-2a（test_bash_notification_before_start_blocks）、AC-3/4/5（queue / attachment / 其他状态）、AC-6（test_bash_task_stop_blocks）、AC-7（test_two_commands_one_ended_allows_then_both_block）、AC-8（test_pending_bash_with_finished_agent_allows）、AC-9、AC-10、AC-12 |
| TC-26 | 通过 | 测试文件::test_tc26_notification_before_start_blocks（queue-operation、attachment 两组；queue 组 reason 与对照逐字相同） |

回归范围另加两条用例，均通过：test_regression_last_mentioned_plan（ACTIVE 两份方案，按最后提到的那份判断；最后提到 RUNNING 方案且有未结束后台命令时放行）、test_regression_last_mentioned_flow_state。已有的 test_pending_background_agent_allows、test_finished_background_agent_blocks_again、test_stalled_blocks_release_and_wake_pm、test_stop_guard_release_wakes_pm 在全量测试中通过。

## 4. 缺陷列表

无

## 5. 回归记录

无
