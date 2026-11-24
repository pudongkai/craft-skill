# Stop hook 识别后台 Bash 命令技术方案

开发状态: TODO
方案评审: 待评审
需求来源: docs/prd/stop-guard-bg.md
作者: 开发（团队角色）　　日期: 2026-11-24

## 1. 背景与目标

三个 Stop hook（`phased-dev-loop/scripts/stop_guard.py`、`dev-flow/scripts/flow_guard.py`、`team-pm/scripts/worker_guard.py`）在本会话还有没交回结果的后台子 agent 时放行，但不认 Bash 工具的后台命令（`run_in_background` 或前台超时转后台）。主对话后台跑 `board.py wait`、长测试时结束本轮会被反复拦截，直到连续拦截 3 次才放行，worker_guard 还会多写一条“会话停下”事件。

做完后要达到：
- 会话记录里有未结束的后台 Bash 命令时，三个 hook 都放行且不输出内容；
- 命令收到完成通知（completed / failed / killed / stopped）或被 TaskStop 停掉后，三个 hook 恢复原有拦截，输出与改动前一致；
- 识别逻辑只在 `stop_guard.py` 里写一份，另外两个 hook 通过已有的共用函数拿到结果。

## 2. 范围

**本次要做：**
- `stop_guard.py` 的会话记录扫描（`scan_transcript`）同时统计后台 Bash 命令：认启动记录、完成通知、TaskStop 结果；
- 完成通知里的任务 id 不再限定十六进制字符；
- 三个 hook 和相关 SKILL.md、failure-modes.md 里“后台子 agent”的说明改成“后台任务（子 agent、Bash 命令）”；
- 新增 stop_guard、flow_guard 的 hook 测试，worker_guard 测试补后台 Bash 用例。

**本次不做：**
- 后台命令永远不结束时的超时处理（平台超时杀掉会发 killed 通知，按结束处理）；
- 拦截、连续拦截后放行的提示文字和计数规则；
- Monitor、CronCreate、ScheduleWakeup 等其他后台机制；
- hook 在 settings.json 里的注册方式；
- `watch-task/scripts/hook.py` 里另一份后台子 agent 统计（它不是这三个 hook 之一，需求没要求）。

## 3. 现状分析

- `phased-dev-loop/scripts/stop_guard.py`
  - `scan_transcript(transcript, needles)`：逐行读会话记录，返回 (未交回结果的后台子 agent 数, 最后提到的 needle)。为了快，行里既没有 `gentId` 也没有 `<task-id>` 时跳过 JSON 解析；读不到文件时返回 `(0, _UNREADABLE)`，坏行跳过。
  - `_launched_agent(row)`：`toolUseResult.status == "async_launched"` 取 `agentId`，否则取 `resumedAgentId`。
  - `_finished_agents(row)`：只看 `type` 为 `queue-operation`（`content`）或 `attachment`（`attachment.prompt`）的行，把空白压成单个空格后用 `TASK_DONE_RE` 取出已结束的 id。`TASK_DONE_RE` 的 id 部分是 `[0-9a-f]+`，Bash 任务 id（如 `b6r5u7d58`）匹配不上。
  - `session_target(transcript, candidates)`：包一层 `scan_transcript`，stop_guard 和 flow_guard 用它，第一项非 0 就放行。
- `dev-flow/scripts/flow_guard.py`：`from stop_guard import session_target`，`pending` 非 0 时放行。
- `team-pm/scripts/worker_guard.py`：开发、修复单先以子进程跑 stop_guard.py；之后 `decide_stop` 里 `scan_transcript(path, [])` 的第一项非 0 时放行。
- 三个 hook 已经通过同一个函数拿“未结束数”，只要扩展 `scan_transcript`，三个 hook 同时生效，AC-13 不需要改调用方。

会话记录里的实际格式（查过本机会话记录确认）：
- 启动：`toolUseResult` 为 `{"stdout":…, "backgroundTaskId":"b6r5u7d58"}`；前台超时转后台多一个 `"timedOutAfterMs":120000`；用户手动转后台多一个 `"backgroundedByUser":true`。三种都带 `backgroundTaskId`。
- 顺序：命令跑得快时，完成通知（`queue-operation`）会比带 `backgroundTaskId` 的工具结果先写进会话记录（例：`~/.claude/projects/-Users-dongkaipu-projects-clauve/1f012bd8-….jsonl` 第 62 行通知、第 68 行启动记录）。不能假定启动记录一定在结束记录前面。
- 完成通知：`queue-operation` 行的 `content` 为 `<task-notification>\n<task-id>bls6cfshm</task-id>\n<tool-use-id>…</tool-use-id>\n<output-file>…</output-file>\n<status>completed</status>\n<summary>…`，与子 agent 的通知格式相同。
- TaskStop 成功：`toolUseResult` 为 `{"message":"Successfully stopped task: …","task_id":"b4e03i4ah","task_type":"local_bash","command":…}`。本机近期会话记录里，顶层带 `task_id` 的 `toolUseResult` 只有 TaskStop 的成功结果。

### 可复用的已有代码

| 功能 | 已有实现（文件:类/方法） | 用在哪些阶段 |
| --- | --- | --- |
| 逐行扫描会话记录、跳过坏行、读不到时按 0 处理 | `phased-dev-loop/scripts/stop_guard.py:scan_transcript` | 阶段1 |
| 从完成通知取已结束 id | `phased-dev-loop/scripts/stop_guard.py:_finished_agents` / `TASK_DONE_RE` | 阶段1 |
| 三个 hook 取“未结束数” | `session_target`（stop_guard、flow_guard）、`scan_transcript`（worker_guard） | 阶段1、阶段2 |
| worker_guard 测试夹具（临时看板、写会话记录、跑 hook） | `team-pm/tests/test_worker_guard.py:WorkerGuardTestCase` | 阶段2 |

## 4. 方案设计

### 4.1 整体思路

在 `scan_transcript` 里增加 Bash 后台命令的统计，和原有子 agent 统计相加后返回：
- 子 agent：保持现在的逐行处理（派出、恢复时加入，结束时移除），因为同一个 agent 会被 `resumedAgentId` 恢复，必须按先后顺序算；
- Bash 命令：任务 id 不会重复使用，但完成通知可能先于启动记录写入，所以不按顺序处理，而是整份记录扫完后算“启动过的 id − 结束过的 id”的个数。调用方的接口和返回值含义（第一项非 0 即放行）不变，三个 hook 不用改逻辑。

### 4.2 数据模型

无。

### 4.3 接口设计

`scan_transcript`、`session_target` 签名和返回结构不变，第一项的含义从“未交回结果的后台子 agent 数”改为“未结束的后台任务数（子 agent + Bash 命令）”，同步改文档字符串。

### 4.4 关键流程

`stop_guard.py` 内改动：

1. 启动记录：`_launched_agent` 不变；新增 `_launched_bash(row)`：`toolUseResult` 是 dict 且 `backgroundTaskId` 是非空字符串时返回它。主动后台、超时转后台、用户手动转后台三种记录都带这个字段，不需要分别判断。
2. 完成通知：`TASK_DONE_RE` 改成
   `<task-id>([^<\s]+)</task-id>(?:(?!<task-id>).)*?<status>(?:completed|failed|killed|stopped)</status>`
   - id 部分不限字符集（BR-3），只排除 `<` 和空白；
   - 中间不允许跨过下一个 `<task-id>`，避免一行里有多条通知时把前一条的 id 配到后一条的状态上。
   `_finished_agents` 改名为 `_finished_tasks`，其余逻辑不变（仍只看 `queue-operation` 和 `attachment` 两类行）。
3. TaskStop 结果：新增 `_stopped_task(row)`，`toolUseResult` 是 dict、`task_id` 是非空字符串、且 `message` 以 `Successfully stopped task` 开头时返回 `task_id`。加 `message` 条件是为了不把以后其他工具顶层同名的 `task_id` 字段误认成结束；失败的 TaskStop 不是这种结构，也不会被认成结束。对子 agent id 用 TaskStop 时同样移除（需求第 6 节的假设）。
4. 预筛：跳过 JSON 解析的条件从“行里没有 `gentId` 且没有 `<task-id>`”扩成“行里没有 `gentId`、`backgroundTaskId`、`"task_id"`、`<task-id>` 中任何一个”。用一个元组常量 `TASK_LINE_MARKERS` 表达。
5. 计数：扫描时维护三个集合——`agents`（未结束的子 agent，逐行加入和移除，同现在）、`bash_started`（见过的 Bash 任务 id）、`ended`（见过结束记录的所有 id，来自完成通知和 TaskStop）。每行先处理启动：子 agent id 加入 `agents`，Bash 任务 id 加入 `bash_started`；再处理结束：id 加入 `ended`，并从 `agents` 移除。扫完返回 `len(agents) + len(bash_started - ended)`。

异常处理沿用现状：读不到文件返回 `(0, _UNREADABLE)`，坏行跳过（BR-7）。

文字说明同步更新（不改行为）：
- `stop_guard.py`、`flow_guard.py`、`worker_guard.py` 开头的说明和 `scan_transcript` / `session_target` 文档字符串；
- `phased-dev-loop/SKILL.md` 第 88 行（等结果）、第 147 行（放行条件表格）、第 155 行，`dev-flow/SKILL.md` 第 152 行（放行条件表格），“后台子 agent”改为“后台任务（子 agent 或 Bash 后台命令）”，第 88、155 行讲的是派子 agent 等结果，只在适用处补上 Bash 后台命令；
- `phased-dev-loop/references/failure-modes.md` 的 F3 补上 Bash 后台命令。

### 4.5 公共逻辑

后台任务识别 → `phased-dev-loop/scripts/stop_guard.py:scan_transcript` 及其辅助函数 → 阶段1。阶段2 只加测试和文字，依赖阶段1。

### 4.6 备选方案

- 在 flow_guard、worker_guard 里各加一段 Bash 识别：违反 BR-6，不采用。
- 查进程或 `tasks/*.output` 文件判断命令是否结束：hook 只拿得到会话记录，查进程跨平台且容易误判（需求第 6 节），不采用。
- Bash 任务和子 agent 合成一个集合逐行加入、移除：完成通知先于启动记录写入时，Bash 任务会一直算作未结束，hook 永久放行，不采用；子 agent 有恢复的情况，也不能简单用“启动 − 结束”，所以两类分开算。

## 5. 兼容性与上线

- 只改脚本内部，hook 注册方式、输出格式不变。skills 目录是到仓库的软链接，合入主干后生效，回滚即还原 `stop_guard.py`。
- 对已有调用方：没有后台 Bash 命令的会话，集合内容和现在完全一样，行为不变（BR-5）。`TASK_DONE_RE` 放宽后，子 agent 的十六进制 id 仍能匹配。

## 6. 风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 完成通知没写进会话记录（例如会话在命令结束前退出、通知丢失） | 该会话后续 Stop 一直放行，阶段循环失去推动 | 需求明确不处理；平台对后台命令有时间上限，超时会发 killed 通知；记入方案，后续需要时再加超时 |
| 完成通知先于启动记录写入会话记录 | 逐行处理时该命令永远算未结束，hook 一直放行 | Bash 命令按“启动过 − 结束过”整份记录计数，与顺序无关；测试覆盖 |
| 其他工具的 `toolUseResult` 顶层也带 `task_id` | 误把未结束的任务认成结束，hook 提前恢复拦截 | 额外要求 `message` 以 `Successfully stopped task` 开头；最坏情况等同于现在的行为（被拦截），不会造成永久放行 |
| 预筛多了 `"task_id"`、`backgroundTaskId` 关键字 | 多解析一些行，略慢 | 只对含关键字的行解析，量很小 |

## 7. 测试方案

全部用 `python3 -m unittest` 跑，构造临时目录和会话记录，以子进程方式调用 hook，检查退出码和标准输出。

- `phased-dev-loop/tests/test_stop_guard.py`（新增）：临时项目写 `.claude/dev-loop/ACTIVE` 和 RUNNING 的方案（有未完成阶段），会话记录里提到方案路径。
  - 无后台任务 → 拦截（基准输出，供对比）；
  - AC-1 启动后无结束 → 放行；AC-2 超时转后台、用户手动转后台 → 放行；
  - 完成通知写在启动记录之前 → 拦截；顶层带 `task_id` 但 `message` 不是成功停止 → 仍放行；
  - AC-3 queue-operation 完成通知（标签间换行）→ 拦截且输出与基准一致；AC-4 attachment 通知 → 拦截；AC-5 failed / killed / stopped → 拦截；
  - AC-6 TaskStop 结果 → 拦截；AC-7 两条命令只结束一条 → 放行，再结束另一条 → 拦截；
  - AC-8 未结束 Bash + 已完成子 agent → 放行；AC-9 未完成子 agent + 已结束 Bash → 放行；AC-10 都已结束 → 拦截；
  - AC-12 会话记录不存在（单个 ACTIVE 方案）→ 拦截；有坏行 + AC-1 → 放行；
  - 一行里两条通知，第一条没有状态 → 第一条的 id 不被移除（正则不跨通知）。
- `dev-flow/tests/test_flow_guard.py`（新增）：临时项目写 `.claude/dev-flow/x.state`（环节“编写方案”），覆盖 AC-1～AC-10、AC-11（环节“等待确认”时无论有无后台任务都放行）、AC-12。AC-5 的三种状态可在一个用例里循环。
- `team-pm/tests/test_worker_guard.py`（补充）：任务单进行中，覆盖 AC-1～AC-10、AC-12（worker_guard 直接调 `scan_transcript`，读不到记录时不经过 `session_target`，需单独测），并确认放行时没有写“会话停下”事件；AC-11 已有用例（`test_stoppable_statuses_allow`）。
- AC-13 靠代码审查确认：flow_guard、worker_guard 不出现 `backgroundTaskId` 等识别逻辑。
- 回归：`python3 -m unittest discover -s team-pm/tests`、`-s phased-dev-loop/tests`、`-s dev-flow/tests` 全部通过。

## 8. 开发计划

- [ ] 阶段1：stop_guard 识别后台 Bash 命令
  - 涉及模块：phased-dev-loop/scripts、phased-dev-loop/tests
  - 依赖：无
  - 验证方式：独立
  - 内容：按 4.4 第 1～5 步改 `stop_guard.py`（`_launched_bash`、`_finished_tasks`、`_stopped_task`、`TASK_DONE_RE`、`TASK_LINE_MARKERS`、`scan_transcript` 计数），更新文件开头说明和文档字符串；新增 `phased-dev-loop/tests/test_stop_guard.py`，覆盖第 7 节列出的 stop_guard 用例
  - 完成标准：`python3 -m unittest discover -s phased-dev-loop/tests` 通过；`python3 -m unittest discover -s team-pm/tests` 仍全部通过

- [ ] 阶段2：flow_guard、worker_guard 测试与文档同步
  - 涉及模块：dev-flow/tests、dev-flow/scripts、dev-flow/SKILL.md、team-pm/tests、team-pm/scripts/worker_guard.py、phased-dev-loop/SKILL.md、phased-dev-loop/references
  - 依赖：阶段1
  - 内容：新增 `dev-flow/tests/test_flow_guard.py`，在 `team-pm/tests/test_worker_guard.py` 补后台 Bash 用例（见第 7 节）；更新 flow_guard.py、worker_guard.py 开头说明，phased-dev-loop/SKILL.md、dev-flow/SKILL.md 放行条件表格，failure-modes.md 的 F3
  - 完成标准：`python3 -m unittest discover -s dev-flow/tests` 和 `python3 -m unittest discover -s team-pm/tests` 通过；`grep -n "backgroundTaskId\|task_id" dev-flow/scripts/flow_guard.py team-pm/scripts/worker_guard.py` 无输出

## 9. 待确认问题

无
