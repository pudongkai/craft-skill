# Stop hook 识别后台 Bash 命令需求文档

项目: stop-guard-bg
作者: 产品（团队角色）　　日期: 2026-11-24
状态: 草稿

## 1. 背景与目标

phased-dev-loop 的 Stop hook（`phased-dev-loop/scripts/stop_guard.py`）在还有未完成阶段时拦住会话停下。它已经会在“本会话还有没交回结果的后台子 agent”时放行，好让主对话结束本轮、等子 agent 的完成通知把会话叫醒。

但用 Bash 工具 `run_in_background` 起的后台命令（例如 `board.py wait` 等事件）不在这套判断里：命令还没结束，hook 照样拦截，主对话只能不停被拦、反复空转，直到连续拦截 3 次后被强制放行。dev-flow 的 `flow_guard.py` 和 team-pm 的 `worker_guard.py` 复用同一套判断，有同样的问题。

目标：本会话有未结束的后台 Bash 命令时，三个 hook 都放行停止，和有后台子 agent 时的行为一致；命令结束（或被停掉）后，hook 恢复正常拦截。

## 2. 用户与场景

| 用户 | 场景 | 现在怎么做 / 痛点 |
| --- | --- | --- |
| 跑 phased-dev-loop 的主对话 | 后台起了一条耗时命令（长测试、构建），想结束本轮等完成通知 | 每次停下都被拦，只能空转或前台干等 |
| 跑 dev-flow 的主对话 | 同上 | 同上 |
| team-pm 派出的角色会话 / PM 会话 | 后台跑 `board.py wait` 等事件，结束本轮等通知 | 被 worker_guard 拦住，反复被拦直到 3 次后强制放行，还会多写一条“会话停下”事件惊动 PM |

## 3. 功能需求

### 3.1 用户故事

- US-1：作为跑 phased-dev-loop 的主对话，我想要在有未结束的后台 Bash 命令时能停下，以便等命令完成通知把我叫醒，而不是被反复拦截。
- US-2：作为跑 dev-flow 的主对话，我想要同样的放行行为，以便流程里也能后台跑命令再等通知。
- US-3：作为 team-pm 派出的角色会话，我想要在后台等待命令时能停下，以便不空转、也不触发“会话停下”事件。
- US-4：作为流程的使用者，我想要后台命令结束或被停掉后 hook 照常拦截，以便流程不会因为一条早就结束的后台命令一直被放行。

### 3.2 业务规则

- BR-1：“未结束的后台 Bash 命令”指：会话记录里有 Bash 工具返回的后台任务 id（`toolUseResult.backgroundTaskId`），且会话记录里（不论在启动记录之前还是之后）没有该 id 的结束记录。命令跑得快时，完成通知可能比启动记录先写进会话记录，这种情况也算已结束。主动 `run_in_background` 起的命令、前台超时被转到后台的命令、用户手动转到后台的命令都算。
- BR-2：结束记录包括：
  - 带该任务 id 的完成通知（`<task-notification>` 中 `<task-id>` 为该 id，`<status>` 为 completed / failed / killed / stopped 之一），出现在 `queue-operation` 或 `attachment` 记录里都算；
  - 用 TaskStop 停掉该任务的成功结果（`toolUseResult.task_id` 为该 id）。TaskStop 停掉的命令不会再收到完成通知，所以必须单独认。
- BR-3：任务 id 按字符串原样比对，不限定字符集（Bash 任务 id 含非十六进制字母，如 `b6r5u7d58`）。
- BR-4：后台子 agent 和后台 Bash 命令合并计数：任一类还有未结束的，hook 就放行。放行时和现在后台子 agent 放行一样，不输出任何内容。
- BR-5：没有未结束的后台任务时，三个 hook 的拦截、连续拦截后放行等现有行为不变。
- BR-6：三个 hook（stop_guard.py、flow_guard.py、worker_guard.py）共用同一处判断，不各写一份，行为一致。
- BR-7：会话记录读不到、某行解析失败时，按现有做法处理（读不到时算 0 个未结束任务，坏行跳过），hook 不报错退出。

## 4. 验收标准

下文“会话记录”指 hook 输入里 `transcript_path` 指向的 jsonl 文件；“拦截”指 hook 标准输出是 `{"decision":"block",...}`；“放行”指 hook 退出码 0 且标准输出为空。“需拦截的状态”指：phased-dev-loop 有未完成阶段的方案、dev-flow 处于非终止环节、worker_guard 对应任务单状态为“进行中”。

| 编号 | 前置条件 | 操作 | 期望结果 | 对应 |
| --- | --- | --- | --- | --- |
| AC-1 | 需拦截的状态；会话记录里有一行 tool_result，`toolUseResult` 含 `"backgroundTaskId":"b6r5u7d58"`，之后没有该 id 的结束记录 | 分别以 Stop 事件调用 stop_guard.py、flow_guard.py、worker_guard.py | 三个 hook 都放行（输出为空） | US-1、US-2、US-3、BR-1、BR-4、BR-6 |
| AC-2 | 同 AC-1，但启动记录是前台超时转后台（`toolUseResult` 含 `backgroundTaskId` 和 `timedOutAfterMs`） | 同 AC-1 | 三个 hook 都放行 | BR-1 |
| AC-2a | 需拦截的状态；会话记录里先有 `b6r5u7d58` 的完成通知（同 AC-3 的 queue-operation 行），后面才有带 `"backgroundTaskId":"b6r5u7d58"` 的启动记录 | 同 AC-1 | 三个 hook 都拦截 | BR-1、BR-2 |
| AC-3 | AC-1 的会话记录后面再加一行 `type:"queue-operation"`，`content` 含 `<task-notification>` 及 `<task-id>b6r5u7d58</task-id>`、`<status>completed</status>`（标签间有换行） | 同 AC-1 | 三个 hook 都拦截，输出和没有后台任务时相同 | US-4、BR-2、BR-3、BR-5 |
| AC-4 | 同 AC-3，但通知在 `type:"attachment"` 行的 `attachment.prompt` 里 | 同 AC-1 | 三个 hook 都拦截 | BR-2 |
| AC-5 | 同 AC-3，`<status>` 分别为 failed、killed、stopped | 同 AC-1 | 每种状态下三个 hook 都拦截 | BR-2 |
| AC-6 | AC-1 的会话记录后面再加一行 TaskStop 的结果，`toolUseResult` 为 `{"message":"Successfully stopped task: b6r5u7d58 (...)","task_id":"b6r5u7d58","task_type":"local_bash"}`，之后没有完成通知 | 同 AC-1 | 三个 hook 都拦截 | US-4、BR-2 |
| AC-7 | 会话记录里起了两条后台命令 `b1xx`、`b2yy`，只有 `b1xx` 有完成通知 | 同 AC-1 | 三个 hook 都放行；再加 `b2yy` 的完成通知后，三个 hook 都拦截 | BR-1、BR-2 |
| AC-8 | 会话记录里有一条未结束的后台 Bash 命令，和一个已完成的后台子 agent | 同 AC-1 | 三个 hook 都放行 | BR-4 |
| AC-9 | 会话记录里有一个未完成的后台子 agent（`status:"async_launched"`），和一条已完成的后台 Bash 命令 | 同 AC-1 | 三个 hook 都放行（现有子 agent 放行行为不变） | BR-4、BR-5 |
| AC-10 | 会话记录里后台子 agent 和后台 Bash 命令都已有完成通知，或者没有任何后台任务 | 同 AC-1 | 三个 hook 都拦截，拦截文字与改动前一致 | BR-5 |
| AC-11 | 任务单不是“进行中”（如已完成）、或 phased-dev-loop / dev-flow 本来就不需要拦截；会话记录里没有后台任务 | 同 AC-1 | 输出与改动前一致 | BR-5 |
| AC-12 | `transcript_path` 指向不存在的文件；或会话记录里混有一行非 JSON 文本，其余同 AC-1 | 同 AC-1 | 文件不存在时 hook 不报错、按无后台任务处理（需拦截的状态下拦截）；有坏行时坏行被跳过，结果同 AC-1（放行） | BR-7 |
| AC-13 | 代码层面 | 查看 flow_guard.py、worker_guard.py | 两者通过调用 stop_guard.py 的同一函数得到“未结束后台任务数”，没有各自复制一份识别逻辑 | BR-6 |
| AC-14 | 仓库里的自动化测试 | 运行 team-pm 测试和新增的 hook 测试 | 全部通过；新增测试覆盖 AC-1～AC-10 中的后台 Bash 情况（至少 AC-1、AC-3、AC-6、AC-7、AC-8） | 全部 |

## 5. 本次不做

- 不处理后台命令卡死、永远不结束的情况（例如加超时上限让 hook 恢复拦截）。命令有平台自带的后台时间上限，超时被杀会发 killed 通知，按 BR-2 处理。
- 不改拦截、连续拦截后放行的提示文字，也不在放行时新增输出。
- 不改“连续拦截 3 次后放行”的计数规则。
- 不识别 Monitor、CronCreate、ScheduleWakeup 等其他后台机制，只认 Bash 后台命令和现有的后台子 agent。
- 不改 hook 在 settings.json 里的注册方式。

## 6. 已做的假设

- TaskStop 成功结果算作结束：TaskStop 停掉的 Bash 命令之后不会再有完成通知，不认的话 hook 会永远放行。只认 `toolUseResult` 里带 `task_id` 的成功结果；对 agent id 用 TaskStop 时同样按结束处理（多认一种结束方式不会造成永久放行）。
- 放行不输出任何内容：和现有后台子 agent 放行一致，避免改变现有行为；需要提示时由完成通知本身叫醒会话。
- 完成通知的格式以现有会话记录里的 `<task-notification>` 为准（统一包括 Bash 命令和子 agent），不考虑其他标签名。
- 识别以会话记录为唯一来源，不去查进程或 `tasks/*.output` 文件：hook 拿到的只有会话记录，查进程跨平台且容易误判。

## 7. 待确认问题

无
