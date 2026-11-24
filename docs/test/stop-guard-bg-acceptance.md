# Stop hook 识别后台 Bash 命令验收结论

项目: stop-guard-bg
作者: 产品（团队角色）　　日期: 2026-11-24
验收单: 0007
需求文档: docs/prd/stop-guard-bg.md
测试报告: docs/test/stop-guard-bg-report.md

## 1. 验收范围

- 提交号: 0d99028（开发提交 d79160e、e057999，QA 测试提交 0d99028）
- 分支: feat/stop-guard-bg

## 2. 自己做的检查

- 全量测试：`python3 -m unittest discover -s <目录>/tests`，team-pm、phased-dev-loop、dev-flow 三处都是 OK。
- 手工跑 stop_guard.py：在临时目录造一份开发状态 RUNNING 的方案和会话记录，用 Stop 事件输入调用 hook：
  - 无后台任务 → 拦截（对照）
  - 只有 `backgroundTaskId: b6r5u7d58` 的启动记录 → 放行，输出为空（AC-1）
  - 启动记录后跟 queue-operation 完成通知（标签间有换行） → 拦截（AC-3）
  - 完成通知写在启动记录前面 → 拦截（AC-2a）
  - 启动记录后跟 TaskStop 成功结果 → 拦截（AC-6）
  - 启动记录前有一行非 JSON → 放行；`transcript_path` 指向不存在的文件 → 退出码 0、无报错、拦截（AC-12）
- 看代码：`phased-dev-loop/scripts/stop_guard.py` 的 `scan_transcript` 统一统计子 agent 和 Bash 后台命令；Bash 按“启动过的 id − 结束过的 id”在整份记录上计数，所以完成通知先于启动记录也算结束；结束记录包括 queue-operation / attachment 里的 `<task-notification>`（四种结束状态）和 TaskStop 成功结果。flow_guard.py 经 `session_target`、worker_guard.py 直接调用 `scan_transcript`，两者没有自己的识别逻辑。

## 3. 逐条结论

| 编号 | 结论 | 依据 |
| --- | --- | --- |
| AC-1 | 通过 | 手工运行放行；测试报告 TC-1（三个 hook）；开发测试 test_bash_started_without_end_allows |
| AC-2 | 通过 | 测试报告 TC-2；`_launched_bash` 只看 `backgroundTaskId`，不区分启动方式 |
| AC-2a | 通过 | 手工运行拦截；TC-26（queue-operation、attachment 两组）；test_bash_notification_before_start_blocks |
| AC-3 | 通过 | 手工运行拦截；TC-3（拦截内容与无后台任务时逐字相同） |
| AC-4 | 通过 | TC-4；`_finished_tasks` 读 `attachment.prompt` |
| AC-5 | 通过 | TC-5；`TASK_DONE_RE` 认 completed / failed / killed / stopped |
| AC-6 | 通过 | 手工运行拦截；TC-6；TC-21 确认失败的 TaskStop 不算结束 |
| AC-7 | 通过 | TC-7；test_two_commands_one_ended_allows_then_both_block |
| AC-8 | 通过 | TC-8 |
| AC-9 | 通过 | TC-9；已有用例 test_pending_background_agent_allows 仍通过 |
| AC-10 | 通过 | TC-10（拦截内容与对照逐字相同）；手工对照运行 |
| AC-11 | 通过 | TC-11、TC-12 |
| AC-12 | 通过 | 手工运行两种情况；TC-13、TC-14、TC-15 |
| AC-13 | 通过 | 看代码，见第 2 节；TC-24 |
| AC-14 | 通过 | 三处全量测试自己运行均 OK；开发新增 BackgroundTaskCases 被三个 hook 的测试继承，覆盖 AC-1、AC-3、AC-6、AC-7、AC-8 等 |

另外，测试报告 TC-23 用真实会话记录核对了启动记录、TaskStop 结果、完成通知的字段，与需求 BR-1、BR-2 的写法一致。

## 4. 修复单

无

## 5. 总体结论

验收通过：14 + 1（AC-2a）共 15 条验收标准全部通过，无修复单。
