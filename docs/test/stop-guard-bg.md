# Stop hook 识别后台 Bash 命令测试计划

项目: stop-guard-bg
作者: QA（团队角色）　　日期: 2026-11-24
需求文档: docs/prd/stop-guard-bg.md

## 1. 测试范围

三个 Stop hook，都以“标准输入传 hook JSON（`session_id`、`transcript_path`、`cwd`）、读标准输出和退出码”的方式调用：

- US-1：`phased-dev-loop/scripts/stop_guard.py`（无参数）。
- US-2：`dev-flow/scripts/flow_guard.py`（无参数）。
- US-3：`team-pm/scripts/worker_guard.py --project <项目> --item <编号>`，包括非代码类任务单（自己判断）和开发 / 修复单（先转给 stop_guard.py）两条路径；同时检查放行时不写“会话停下”事件。
- US-4：后台 Bash 命令结束（完成通知、TaskStop）后三个 hook 恢复拦截。
- BR-6 / AC-13：flow_guard.py、worker_guard.py 复用 stop_guard.py 的同一个判断函数（代码检查）。

## 2. 不测什么

- 后台命令卡死、平台超时上限：需求“本次不做”；超时被杀的 killed 通知已由 TC-5 覆盖。
- Monitor、CronCreate、ScheduleWakeup 等其他后台机制：需求“本次不做”。只加一条 TC-19 确认它们不会被误当成后台 Bash 命令。
- 拦截 / 强制放行的提示文字内容、连续拦截 3 次的计数规则本身：不改，只在回归里确认与改动前一致。
- hook 在 settings.json 里的注册方式：不改。
- 真实 Claude Code 会话里的端到端行为（主对话结束本轮、被完成通知叫醒）：依赖平台，没法在测试里自动驱动。用 TC-23 手工抽查一次真实会话记录的格式。

## 3. 环境与数据

- 环境：项目 worktree 本地，`python3`（3.9+，只用标准库），`git`（worker_guard 测试建项目时需要一个 git 仓库）。
- 测试数据：每条用例在 `tempfile.TemporaryDirectory()` 里现造，用完自动删除：
  - 会话记录：jsonl 文件，按用例写入下列行（字段取自需求文档 BR-1、BR-2 和 AC 表）：
    - Bash 后台启动：`{"type":"user","toolUseResult":{"backgroundTaskId":"b6r5u7d58", ...}}`；前台超时转后台另带 `"timedOutAfterMs":120000`。
    - 完成通知（queue-operation）：`{"type":"queue-operation","content":"<task-notification>\n<task-id>b6r5u7d58</task-id>\n<status>completed</status>\n</task-notification>"}`（标签间有换行）。
    - 完成通知（attachment）：`{"type":"attachment","attachment":{"prompt":"<同上>"}}`。
    - TaskStop 成功：`{"type":"user","toolUseResult":{"message":"Successfully stopped task: b6r5u7d58 (sleep 600)","task_id":"b6r5u7d58","task_type":"local_bash"}}`。
    - 后台子 agent 启动：`{"type":"user","toolUseResult":{"status":"async_launched","agentId":"a1b2c3"}}`。
  - phased-dev-loop 需拦截状态：`<tmp>/.claude/dev-loop/ACTIVE` 写 `docs/plan.md`；`docs/plan.md` 含 `开发状态: RUNNING` 和 `## 开发计划` 下一条 `- [ ] 阶段1：实现`；会话记录第一行的文字里提到 `docs/plan.md`（hook 按会话记录里最后提到的方案判断）。
  - dev-flow 需拦截状态：`<tmp>/.claude/dev-flow/demo.state` 写 `环节: 代码审查`；会话记录里提到 `.claude/dev-flow/demo.state`。
  - worker_guard 需拦截状态：`TEAM_BOARD=<tmp>/board`，用 `board_store` 建项目 `demo` 和一张状态为“进行中”的任务单（沿用 `team-pm/tests/test_worker_guard.py` 的建法）；非代码路径用“测试”类型，代码路径用“开发”类型并让 `cwd` 指向上面的 phased-dev-loop 需拦截状态目录。
- 全量测试命令：`python3 -m unittest discover -s team-pm/tests`（当前基线 47 条全部通过）；加上新增测试后同一命令一起跑。
- 自动化测试位置：QA 在测试单里写，`team-pm/tests/test_guard_bg_bash.py`，unittest，用 subprocess 调三个 hook 脚本（和已有的 `test_worker_guard.py` 同一做法）。开发在开发单里自己加的测试不替代这些用例。

## 4. 用例

下文“三个 hook”指 stop_guard.py、flow_guard.py、worker_guard.py（非代码单）三种调用；worker_guard 开发单路径单独列在 TC-17。“放行”= 退出码 0、标准输出为空、标准错误为空；“拦截”= 退出码 0、标准输出是 JSON 且 `decision` 为 `block`。每条用例三个 hook 各跑一遍，任一不符即不通过。

| 编号 | 对应 AC | 前置条件 | 步骤 | 期望结果 | 方式 |
| --- | --- | --- | --- | --- | --- |
| TC-1 | AC-1 | 需拦截状态；会话记录有 Bash 后台启动行 `b6r5u7d58`，之后无结束记录 | 分别调用三个 hook | 三个都放行 | 自动化（QA，team-pm/tests/test_guard_bg_bash.py） |
| TC-2 | AC-2 | 同 TC-1，启动行 `toolUseResult` 同时含 `backgroundTaskId` 和 `timedOutAfterMs` | 分别调用三个 hook | 三个都放行 | 自动化（QA，同上） |
| TC-3 | AC-3、AC-10 | TC-1 的记录后加 queue-operation 完成通知（`completed`，标签间有换行） | 分别调用三个 hook；另用同一需拦截状态、没有任何后台任务的会话记录各调一次作对照 | 三个都拦截，且 `reason` 与对照输出逐字相同 | 自动化（QA，同上） |
| TC-4 | AC-4 | TC-1 的记录后加 attachment 完成通知（`attachment.prompt`，`completed`） | 分别调用三个 hook | 三个都拦截 | 自动化（QA，同上） |
| TC-5 | AC-5 | 同 TC-3，`<status>` 依次为 `failed`、`killed`、`stopped` | 每种状态分别调用三个 hook | 3 种状态 × 3 个 hook 都拦截 | 自动化（QA，同上） |
| TC-6 | AC-6 | TC-1 的记录后加 TaskStop 成功结果（`task_id` 为 `b6r5u7d58`），之后无完成通知 | 分别调用三个 hook | 三个都拦截 | 自动化（QA，同上） |
| TC-7 | AC-7 | 会话记录起两条后台命令 `b1xx`、`b2yy`，只有 `b1xx` 的完成通知 | 分别调用三个 hook；再追加 `b2yy` 的完成通知后再调一次 | 第一次三个都放行；第二次三个都拦截 | 自动化（QA，同上） |
| TC-8 | AC-8 | 未结束的 Bash 后台命令 + 已收到完成通知的后台子 agent | 分别调用三个 hook | 三个都放行 | 自动化（QA，同上） |
| TC-9 | AC-9 | 未完成的后台子 agent（`async_launched`）+ 已收到完成通知的 Bash 后台命令 | 分别调用三个 hook | 三个都放行 | 自动化（QA，同上） |
| TC-10 | AC-10 | 后台子 agent 和 Bash 后台命令都已有完成通知 | 分别调用三个 hook | 三个都拦截，`reason` 与“没有后台任务”的对照输出逐字相同 | 自动化（QA，同上） |
| TC-11 | AC-11 | 无后台任务；不需拦截的状态：方案 `开发状态: DONE`、dev-flow `环节: 完成`、任务单状态“已完成” | 分别调用三个 hook | 三个都放行（输出为空，与改动前一致） | 自动化（QA，同上） |
| TC-12 | AC-11 | 不需拦截的状态（同 TC-11）+ 未结束的 Bash 后台命令 | 分别调用三个 hook | 三个都放行，无输出、无报错 | 自动化（QA，同上） |
| TC-13 | AC-12 | 需拦截状态；`transcript_path` 指向不存在的文件 | 分别调用三个 hook | 退出码 0、标准错误为空；三个都拦截。stop_guard / flow_guard 在 ACTIVE / 状态文件只有一份时按它判断（现有做法） | 自动化（QA，同上） |
| TC-14 | AC-12 | 同 TC-1，会话记录在启动行前后各插一行非 JSON 文本（如 `not json {`） | 分别调用三个 hook | 退出码 0、标准错误为空；三个都放行 | 自动化（QA，同上） |
| TC-15 | AC-12 | 同 TC-3（命令已结束），中间插一行非 JSON 文本 | 分别调用三个 hook | 三个都拦截 | 自动化（QA，同上） |
| TC-16 | AC-1、BR-3 | 同 TC-1 / TC-3，任务 id 换成非十六进制的字符串：`bZ9-x_7`、`b6r5u7d58` 的大写形式 `B6R5U7D58` 各一组 | 只有启动行时调用；加上同 id 的完成通知后再调用 | 只有启动行时三个都放行；加通知后三个都拦截 | 自动化（QA，同上） |
| TC-17 | AC-1、AC-3、AC-6 | worker_guard，任务单类型“开发”且进行中，`cwd` 下 phased-dev-loop 方案为 RUNNING 有未完成阶段 | 依次用 TC-1、TC-3、TC-6 的会话记录调用 worker_guard | TC-1 记录：放行；TC-3、TC-6 记录：拦截（输出为 stop_guard 的拦截内容） | 自动化（QA，同上） |
| TC-18 | AC-1（US-3） | worker_guard 非代码单进行中；同 TC-1 未结束 Bash 后台命令 | 连续调用 worker_guard 5 次，之后读看板事件 | 5 次都放行；看板里没有新增“会话停下”事件 | 自动化（QA，同上） |
| TC-19 | AC-10（BR-4 只认两类） | 需拦截状态；会话记录只有：一条不带 `backgroundTaskId` 的普通前台 Bash 结果（`toolUseResult` 含 `stdout`、`stderr`）；一行 type 为 user 的普通消息正文里写了 `<task-id>b6r5u7d58</task-id>` 字样 | 分别调用三个 hook | 三个都拦截（没有被误认成后台任务） | 自动化（QA，同上） |
| TC-20 | AC-2、AC-3 | 启动 `b6r5u7d58` 后收到的完成通知 id 是另一个 `b0000000`；或通知 `<status>` 是 `running` 这种非结束值 | 分别调用三个 hook | 两种情况三个都放行（没有该 id 的结束记录） | 自动化（QA，同上） |
| TC-21 | AC-6 | TaskStop 返回失败（`toolUseResult` 只有错误信息、不含 `task_id`，或是 `is_error` 的 tool_result） | 分别调用三个 hook | 三个都放行（失败的 TaskStop 不算结束） | 自动化（QA，同上） |
| TC-22 | AC-1、BR-5 | 需拦截状态，同 TC-1 未结束 Bash 后台命令 | 连续调用每个 hook 4 次（超过“连续拦截 3 次后放行”的次数），然后追加完成通知再调用 1 次 | 前 4 次都放行且无 `systemMessage`；第 5 次是 `decision: block`（后台放行不计入连续拦截次数） | 自动化（QA，同上） |
| TC-23 | AC-1、AC-3、AC-6 | 真实 Claude Code 会话 | 在任意会话里 `run_in_background` 起 `sleep 30`，再起一条 `sleep 600` 并用 TaskStop 停掉；等 30 秒后在 `~/.claude/projects/<目录>/<会话>.jsonl` 里 `grep -n 'backgroundTaskId\|task-notification\|task_id'` | 能看到两条 `backgroundTaskId`；`sleep 30` 有 queue-operation 或 attachment 里的 `<task-notification>`，`<status>completed</status>`；`sleep 600` 有带 `task_id` 的 TaskStop 结果。字段名与第 3 节造的数据一致（不一致时记缺陷，自动化用例的造数随之修正） | 手工 |
| TC-24 | AC-13 | 开发提交后 | `grep -n "import\|def " dev-flow/scripts/flow_guard.py team-pm/scripts/worker_guard.py`，并查看 `git diff <基线> -- dev-flow team-pm/scripts phased-dev-loop` | 两个文件只从 stop_guard 导入函数得到未结束后台任务数，文件里没有出现 `backgroundTaskId`、`task-notification`、`task_id` 的解析代码 | 手工 |
| TC-25 | AC-14 | 开发和 QA 测试代码都已提交 | `python3 -m unittest discover -s team-pm/tests`；另跑开发新增的 hook 测试（位置以开发单产出为准） | 全部通过；开发新增测试至少覆盖 AC-1、AC-3、AC-6、AC-7、AC-8 的后台 Bash 情况（按测试名和用例内容逐条对照） | 手工 |

## 5. 回归范围

- 后台子 agent 放行 / 收到通知后恢复拦截：TC-8～TC-10，以及已有的 `test_worker_guard.py` 中 `test_pending_background_agent_allows`、`test_finished_background_agent_blocks_again`。
- 连续拦截 3 次后放行和 worker_guard 写“会话停下”事件：已有的 `test_stalled_blocks_release_and_wake_pm`、`test_stop_guard_release_wakes_pm`，加上 TC-22。
- 多会话按会话记录里最后提到的方案 / 状态文件判断：stop_guard、flow_guard 改了会话记录扫描（`scan_transcript`），在 ACTIVE 里放两份方案、会话记录先后提到两份，确认仍按最后提到的那份判断（并入测试单时在 `test_guard_bg_bash.py` 里加一条）。
- 全量：`python3 -m unittest discover -s team-pm/tests` 全部通过。

## 6. 说明

- “拦截输出和没有后台任务时相同”（AC-3、AC-10）按 `decision`、`reason` 两个字段逐字相同判断；为避免连续拦截计数影响，对照组用新的 session_id 或新的临时目录。
- AC-12 “文件不存在时按无后台任务处理”：stop_guard / flow_guard 读不到会话记录时只在 ACTIVE / 状态文件只有一份时才知道该判断哪一份（需求说“按现有做法”），所以 TC-13 的数据只放一份方案 / 状态文件。
- TC-16 的“不限定字符集”按需求 BR-3 理解为任意非空字符串、原样比对；不测含 `<`、`>` 的 id（会破坏通知标签本身）。
- TC-21 失败 TaskStop 的具体格式需求没写明，按“`toolUseResult` 不含 `task_id`”理解；需求第 6 节说只认带 `task_id` 的成功结果，与此一致，不建答疑单。
- 没有建答疑单。
