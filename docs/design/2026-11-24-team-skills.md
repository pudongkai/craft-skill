# 多角色团队开发 skills 技术方案

开发状态: DONE
方案评审: 通过（2026-11-24）
需求来源: 对话需求：多个项目并行推进时只能开多个会话，难管理、离不开人；按团队开发模式用 PM、产品、开发、QA 等角色完成项目
作者: pudongkai　　日期: 2026-11-24

## 1. 背景与目标

现有的 `dev-flow` / `tech-design` / `phased-dev-loop` 能把**一个**需求从澄清做到本地提交，但它们都假设“一个会话 + 一个人盯着”：

- 多个项目并行时，人要自己开 N 个会话、记住每个会话做到哪、挨个回答问题；
- 需求、方案、开发、测试都由同一个会话完成，自己写自己验，缺少角色之间的互相检查；
- 会话停下（要确认、出错、等授权）没人知道，项目就停在那里。

目标：

1. 人只和**一个 PM 会话**打交道，就能同时推进多个项目；其他角色会话由 PM 派出、回收，人不用逐个打开。
2. 按团队分工：产品出需求和验收标准，开发出方案和代码，QA 按验收标准独立测试，PM 负责排期、派活、汇总问题。角色之间靠文档交接，互相检查。
3. 人只在固定的几个确认点参与（默认：需求确认、方案确认、交付），其余环节自动衔接；需要人决定的问题由 PM 攒成一批统一问。
4. 所有进度写在文件里：任何会话中断、被压缩、换机器，读看板和项目文档就能接着做。

## 2. 范围

**本次要做：**
- 4 个角色 skill：`team-pm`（项目 PM / 调度）、`team-product`（产品经理）、`team-dev`（开发）、`team-qa`（QA）。
- 一套公共协议：看板目录结构、任务单格式、状态流转、角色间交接和提问规则。
- 看板脚本 `board.py`：建项目、建任务单、领取 / 完成任务、派出角色会话、等待事件、检查会话存活。
- 角色会话的 Stop hook：任务单没收尾时不让会话停下；会话意外停下时记一条事件，叫醒 PM。

**本次不做：**
- 不做 Web 看板界面，状态就是 Markdown 文件，`board.py status` 打印汇总。
- 不自动推送、建 PR、合并到主分支、部署——这些是对外操作，留在“交付”确认点由人决定。
- 不做跨机器调度：看板和角色会话都在本机。换机器接着做沿用 `claude-handoff`。
- 不单独设代码审查、架构师、运维角色。代码审查沿用 dev-flow 的子 agent 审查，放在开发角色内部；以后需要时按同一协议加角色。
- 不改 `dev-flow` 本身，单项目、人在旁边盯着的场景继续用它。

## 3. 现状分析

| skill | 做什么 | 在新方案里的位置 |
| --- | --- | --- |
| `tech-design` | 按模板写技术方案，开发计划可被 phased-dev-loop 读取 | 开发角色写方案时直接用 |
| `phased-dev-loop` | 按方案分阶段开发，Stop hook 推动循环，支持阶段并行 | 开发角色写代码时直接用 |
| `dev-flow` | 串联澄清 → 方案 → 自审 → 确认 → 开发 → 审查 → 提交，状态文件 + Stop hook | 环节切分和审查提示词被拆给各角色复用；本身不变 |
| `watch-task` | 盯本机其他会话，停下时用 dws 单聊通知主管 | 可选：PM 有要人确认的事时，借它的 `hook.py --send` 发钉钉 |
| `progress-pulse` | 定时检查跟进中的事，发简报 | 增加一项检查来源：团队看板 |
| `claude-handoff` | 把任务接力给另一台 Mac | 不变 |

Claude Code 2.1.311 已经提供了本方案需要的运行能力：

- `claude --bg -n <名称> --permission-mode <模式> "<提示词>"`：在后台启动一个会话，立刻返回短 id；
- `claude agents --json`：列出所有交互式和后台会话及状态；`claude logs <id>`：看最近输出；`claude attach <id>`：人需要时接进去看；`claude stop <id>`：停掉，记录保留，可 `--resume`；
- 后台命令结束会自动叫醒发起它的会话（PM 用 `board.py wait` 等事件，不用轮询）。

### 可复用的已有代码

| 功能 | 已有实现（文件:类/方法） | 用在哪些阶段 |
| --- | --- | --- |
| 读会话记录、找本会话最后提到的目标、按会话计数防死循环 | `phased-dev-loop/scripts/stop_guard.py`：`read_text`、`session_target`、`stalled_count` | 阶段3 |
| 解析 `键: 值` 状态行 | `dev-flow/scripts/flow_guard.py`：`STATE_LINE_RE`、`parse_state` | 阶段1 |
| 方案审查、代码审查提示词 | `dev-flow/references/review-prompts.md` | 阶段6 |
| 方案模板与开发计划格式 | `tech-design/templates/tech-design.md`、`tech-design/references/plan-format.md` | 阶段6 |
| 钉钉单聊通知 | `watch-task/scripts/hook.py --send` | 阶段4（可选） |

## 4. 方案设计

### 4.1 整体思路

```
                 人（只和 PM 对话；只在确认点参与）
                          │
                ┌─────────▼─────────┐
                │  PM 会话 team-pm   │  前台，长期存在；不写代码、不读大文档
                └──┬──────┬──────┬──┘
     board.py dispatch    │      │  board.py wait（后台等事件，有事件就叫醒 PM）
        ┌──────────┘      │      └───────────┐
   ┌────▼────┐       ┌────▼────┐        ┌────▼────┐
   │产品会话  │       │开发会话  │        │QA 会话   │   后台 claude --bg，一张任务单一个会话，
   └────┬────┘       └────┬────┘        └────┬────┘   做完就结束
        │ 读写             │ 读写              │ 读写
   ┌────▼──────────────────▼─────────────────▼────┐
   │ 看板 ~/.claude/team/   项目卡、任务单、事件     │  状态：谁在做什么、做到哪
   │ 项目仓库 docs/…        需求文档、方案、测试文档 │  内容：交接的正文，跟代码一起提交
   └──────────────────────────────────────────────┘
```

三条原则：

1. **一张任务单一个短命会话。** 角色会话领一张任务单，做完、写结果、结束。不让角色会话常驻：上下文不会越堆越长，挂了也只丢一张单的进度，PM 重派即可。
2. **状态在看板，内容在仓库。** 看板只记状态和文档路径；需求文档、方案、测试计划、缺陷记录写在项目仓库里并提交，跟代码同版本，换人换机器也能拿到。
3. **角色之间只通过任务单说话。** 开发对需求有疑问，不直接找产品会话，而是建一张“答疑”单指给产品；PM 看到后派产品会话回答。所有交流都有记录，任何一方中断都不丢。

### 4.2 数据模型

看板根目录默认 `~/.claude/team/`，可用环境变量 `TEAM_BOARD` 改。

```
~/.claude/team/
  config.json                # 并发上限、默认权限模式、通知方式
  events.jsonl               # 事件流：任务完成、要人确认、会话意外停下……PM 靠它被叫醒
  projects/<项目名>/
    project.md               # 项目卡
    items/0001-product-prd.md  # 任务单，编号在项目内递增
    items/0002-dev-design.md
```

**项目卡 `project.md`**（`键: 值` 行 + 正文）：

```
项目: order-refund
仓库: /Users/me/workspace/shop
工作目录: /Users/me/workspace/shop/.claude/worktrees/order-refund
分支: feat/order-refund
阶段: 需求          # 立项 需求 方案 开发 测试 验收 交付 完成 暂停
人工确认: 需求, 方案, 交付
优先级: P1
目标: <一两句话>

## 文档
需求: docs/prd/order-refund.md
方案: docs/design/2026-11-24-order-refund.md
测试计划: docs/test/order-refund.md

## 待人确认
- [ ] <PM 攒下的问题，带建议做法>

## 记录
- 2026-11-24 立项
```

**任务单 `items/NNNN-<角色>-<简称>.md`**：

```
# 0002 开发：写技术方案
角色: 开发            # PM 产品 开发 QA 人
类型: 方案            # 需求 方案 测试计划 开发 测试 修复 验收 答疑 确认
状态: 待领取          # 待领取 进行中 待人确认 阻塞 完成 取消
依赖: 0001
会话:                 # 领取时写入后台会话 id
重试: 0
输入: docs/prd/order-refund.md
产出:                 # 完成时写产出文档路径或提交号

## 说明
<要做什么、完成标准>

## 结果
<完成时写：三五行结论。PM 只读这一节，不读产出全文>
```

**事件 `events.jsonl`** 每行一条：`{"时间", "项目", "任务单", "事件": "完成|待人确认|阻塞|会话停下|会话退出", "说明"}`。

**`config.json`**：`max_sessions`（全局同时运行的角色会话上限，默认 4）、`max_sessions_per_project`（默认 2）、`permission_mode`（默认 `auto`）、`notify`（`none` 或 `dws`，后者复用 watch-task 的配置）。

### 4.3 接口设计

`team-pm/scripts/board.py`（其他角色 skill 按固定路径 `~/.claude/skills/team-pm/scripts/board.py` 调用）：

| 命令 | 谁用 | 作用 |
| --- | --- | --- |
| `project new <名> --repo <路径> [--confirm 需求,方案,交付]` | PM | 建项目卡，建分支和 worktree |
| `project set <名> <键> <值>` | PM | 改项目阶段、优先级、暂停等 |
| `item new <项目> --role <角色> --type <类型> [--deps 0001] [--input …] --desc …` | 所有角色 | 建任务单 |
| `item claim <项目> <编号>` | 角色会话 | 领取：状态改为进行中，写入本会话 id；已被领取则失败 |
| `item done <项目> <编号> --output … --result …` | 角色会话 | 完成，追加事件 |
| `item block <项目> <编号> --reason …` / `item ask-human …` | 角色会话 | 阻塞 / 要人确认，追加事件 |
| `ready` | PM | 列出依赖已满足、可派出的任务单（按项目优先级排序，扣掉并发上限） |
| `dispatch <项目> <编号>` | PM | 用 `claude --bg -n team-<项目>-<编号>` 在项目工作目录启动角色会话，提示词见阶段4 |
| `reap` | PM | 对照 `claude agents --json`：进行中但会话已不在的单，记“会话退出”事件 |
| `wait [--since <事件序号>]` | PM（后台运行） | 阻塞到有新事件再退出并打印事件，用来叫醒 PM |
| `status [项目]` | PM / 人 | 打印所有项目的阶段、进行中的单、待人确认数 |

### 4.4 关键流程

**项目阶段与任务单**（每个阶段 PM 建哪些单，谁做）：

| 阶段 | 任务单 | 产出 | 下一步 |
| --- | --- | --- | --- |
| 立项 | PM 和人对齐目标、仓库、确认点 | 项目卡 | 建“需求”单 |
| 需求 | 产品：需求 | `docs/prd/<项目>.md`：用户故事、业务规则、**可检查的验收标准**、不做什么 | 需求确认（人）后进入方案 |
| 方案 | 开发：方案；QA：测试计划（并行） | 技术方案（tech-design 格式）；测试计划（每条验收标准对应的用例） | 产品检查方案是否覆盖需求 → 方案确认（人） |
| 开发 | 开发：开发 | 代码 + 本地提交（phased-dev-loop + 子 agent 代码审查） | 进入测试 |
| 测试 | QA：测试 | 测试报告；每个缺陷一张“修复”单指给开发 | 修复单全部完成且回归通过 → 验收 |
| 验收 | 产品：验收 | 逐条对照验收标准的结论 | 交付确认（人） |
| 交付 | PM 汇总 | 分支、提交列表、测试和验收结论 | 人决定推送 / 建 PR / 合并 |

`人工确认` 里没列的确认点由 PM 按角色结论自动放行（例如小项目只保留“交付”）。

**PM 的主循环**（team-pm skill 的核心）：

1. `reap` 回收挂掉的会话 → `ready` 找出可派的单 → 在并发上限内 `dispatch`。
2. 后台运行 `board.py wait`，结束本轮回复。有事件时被叫醒。
3. 处理事件：
   - 完成 → 读任务单“结果”一节，按上表建下一阶段的单、推进项目阶段；
   - 答疑 → 派对应角色回答；
   - 待人确认 / 阻塞 → 写进项目卡“待人确认”，攒一批后统一问人（有确认点卡住项目时立即问；`notify: dws` 时同时发钉钉）；
   - 会话退出 / 会话停下 → 看 `claude logs`，能重派的 `--resume` 原会话重派，同一张单重试 2 次仍失败就转为阻塞交给人。
4. 回到第 1 步。

人回复确认后，PM 把结论写回对应文档和任务单，再派下一张单。

**角色会话的通用流程**（每个角色 skill 开头都是这一段，写在公共协议里）：

1. 读派单提示里的项目卡和任务单，`item claim`。
2. 按本角色 skill 干活。只改任务单范围内的文件。
3. 遇到问题：能自己查清的自己查；需要别的角色回答的建“答疑”单并 `item block` 等回复；需要人决定的 `item ask-human`。不在会话里直接问人——后台会话没人看。
4. 产出文档提交到项目分支（只提交自己的文件：`git commit -- <路径>`）。
5. `item done`，把三五行结论写进“结果”，结束。

**并发与冲突控制：**

- 每个项目一个 worktree + 一个分支，不同项目互不影响，同一仓库的多个项目也各用各的 worktree。
- 同一项目同一时间最多一张“代码类”单（开发、修复）在跑；文档类单（需求、测试计划、验收）可以和它并行，但只提交自己的文件。
- 全局和单项目并发上限由 `ready` 统一扣减，PM 不需要自己数。

**角色会话的 Stop hook（`worker_guard.py`）：**

| 情况 | 行为 |
| --- | --- |
| 本会话没领过任务单 | 放行 |
| 本会话领的单已是完成 / 阻塞 / 待人确认 | 放行 |
| 还有没交回结果的后台子 agent | 放行 |
| 开发会话里方案开发状态是 RUNNING | 放行，交给 phased-dev-loop 的 hook |
| 单还在进行中 | 拦截，提示“做完写 `item done`，做不下去写 `item block` / `item ask-human`” |
| 连续 3 次拦截单子都没变化 | 放行，同时写一条“会话停下”事件叫醒 PM |

另外注册 Notification hook：角色会话要授权或要回答提问时写“会话停下”事件，PM 告诉人用 `claude attach <id>` 接进去处理。

hook 不写在角色 skill 的 SKILL.md 里（`hooks:` 声明的路径会被客户端改写导致不生效，见 phased-dev-loop 的 failure-modes F5），而是 `dispatch` 启动会话时用 `--settings '<json>'` 只给这个会话注册，命令里直接带上项目名和任务单号，hook 不用再猜本会话做的是哪张单。开发会话的“方案 RUNNING 时交给 phased-dev-loop”由 `worker_guard.py` 先调用 `stop_guard.py` 实现，不另外注册。

### 4.5 公共逻辑

- 看板读写（解析 `键: 值`、编号递增、文件锁、追加事件）→ `team-pm/scripts/board.py` → 阶段1
- 角色会话通用规则（领取、提问、提交、收尾）→ `team-pm/references/protocol.md` → 阶段1；三个角色 skill 都引用它，不各写一份
- 派单提示词模板 → `team-pm/references/dispatch-prompt.md` → 阶段2

### 4.6 备选方案

- **PM 会话里用子 agent 扮演各角色**：不用新开会话，但子 agent 不能再派子 agent，phased-dev-loop 的阶段并行、代码审查都用不了；所有角色的输出最终流回 PM 的上下文，项目一多就撑满；PM 会话一挂所有项目一起停。不采用。
- **Workflow 工具编排**：适合一次性跑完的批处理，但每次都要人明确同意，跑完即结束，不适合要等人确认、持续几天的项目。不采用。
- **角色会话常驻（每个项目固定一个开发会话）**：省去每次读文档的开销，但上下文越来越长，挂掉后恢复复杂。不采用；“一单一会话”下，需要连续性的内容都在文档里。
- **角色之间直接用会话间发消息（SendMessage）**：实时，但对方会话可能已结束，消息丢了没有记录，还要求每个会话开启 `crossSessionInbound`。只作为 PM 催办进行中会话的补充手段，主通道仍是任务单。

## 5. 兼容性与上线

- 新增 skill，不改现有 skill 的行为；`progress-pulse` 只追加一项“检查团队看板”。
- 三个角色 skill 依赖 `team-pm/scripts`，`team-dev` 依赖 `tech-design`、`phased-dev-loop`，都要装在 `~/.claude/skills/` 下（与 dev-flow 依赖 phased-dev-loop 的方式相同）。
- 角色会话以 `--permission-mode auto` 运行；用户可在 `config.json` 改成 `bypassPermissions`（风险自担）。
- 停用：`board.py project set <名> 阶段 暂停` 后 PM 不再派单；`claude stop` 停掉进行中的会话，看板记录保留，恢复时重派。

## 6. 风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 后台会话卡在授权提示上 | 项目停住没人知道 | Notification hook 写事件叫醒 PM，PM 让人 `claude attach` 处理；默认用 `auto` 权限模式减少提示 |
| 多个会话同时改看板 | 任务单被覆盖、重复领取 | `board.py` 写文件加锁；`claim` 先检查状态再写 |
| 并行会话多、额度消耗快 | 费用不可控 | 全局 / 单项目并发上限；角色会话按角色指定 effort（产品、QA 默认中等） |
| 角色之间来回答疑、修复打转 | 项目一直不收敛 | 同一缺陷修复超过 2 轮、同一张单重试超过 2 次 → 转阻塞交给人 |
| 自动放行的确认点做错决定 | 方向错了做了无用功 | 默认需求、方案、交付三个确认点都要人确认；只有人主动去掉才自动放行 |
| PM 会话自己挂了 | 没人派单 | PM 的所有状态都在看板里，新开会话说“继续团队项目”即可恢复；`progress-pulse` 定时检查看板，发现停滞会提醒 |

## 7. 测试方案

- `board.py`：单测覆盖建项目、建单编号、依赖判断、并发扣减、重复领取、加锁、`wait` 收到事件退出。
- `worker_guard.py`：用构造的会话记录和任务单覆盖上面 hook 表格里的每种情况。
- 端到端：准备一个小的演示仓库，同时立两个项目（如“加一个 CLI 子命令”“修一个已知 bug”），只在三个确认点回复，检查两个项目都能走到交付，期间不需要人打开任何角色会话。

## 8. 开发计划

- [x] 阶段1：公共协议与看板读写
  - 涉及模块：team-pm/references/protocol.md、team-pm/scripts/board.py
  - 依赖：无
  - 内容：看板目录、项目卡、任务单、事件格式的说明文档；`board.py` 的 project / item / ready / status 命令和文件锁；单测
  - 完成标准：`python3 -m unittest discover team-pm/tests` 通过；手动建一个项目、两张有依赖的单，`ready` 只列出可开始的那张
  - 验证：`python3 -m unittest discover team-pm/tests` 17 个用例通过（本机无 pytest，改用 unittest）；手动建项目和两张有依赖的单，`ready` 只列出 0001；改动 `team-pm/scripts/board_store.py`、`board.py`、`references/protocol.md`、`tests/test_board.py`

- [x] 阶段2：派单、等待与会话回收
  - 涉及模块：team-pm/scripts/board.py、team-pm/references/dispatch-prompt.md
  - 依赖：阶段1
  - 验证方式：独立
  - 内容：`dispatch`（建 worktree、拼派单提示、`claude --bg` 启动、记录会话 id）、`wait`、`reap`
  - 完成标准：对演示仓库 `dispatch` 一张“打印 hello 并 item done”的单，`claude agents --json` 能看到会话，`wait` 在单完成后退出并打印事件；手动 `claude stop` 后 `reap` 记下“会话退出”
  - 验证：`python3 -m unittest discover team-pm/tests` 47 个用例通过（含 test_dispatch.py 用假 claude 命令覆盖派单、重派、接着原会话、回收、等待）；实机在本仓库 worktree 派出试验单，会话领取并 `item done`，`wait` 收到“完成”事件，`reap` 停掉会话；实机发现刚启动的会话短时间没有 pid，已按 state 判断并加 90 秒宽限；独立验证提出 6 处问题已全部修复（重派后再次退出要重报、旧会话活着先停、重试满次数真正转阻塞、claude 命令异常转成看板错误、回收记录按派出次数保留并加锁）；新仓库要先被 Claude Code 信任才能派单，`project new` 会提示；改动 `team-pm/scripts/board_dispatch.py`、`board.py`、`board_store.py`、`references/dispatch-prompt.md`、`tests/test_dispatch.py`

- [x] 阶段3：角色会话的 Stop / Notification hook
  - 涉及模块：team-pm/scripts/worker_guard.py
  - 依赖：阶段1
  - 验证方式：独立
  - 内容：按 4.4 的 hook 表格实现；复用 `stop_guard.py` 的会话记录读取和计数
  - 完成标准：`python3 -m unittest team-pm/tests/test_worker_guard.py` 覆盖表格中每一行
  - 验证：`python3 -m unittest discover team-pm/tests` 通过（hook 相关 17 个用例，覆盖表格每一行及参数错误、脚本出错放行）；独立验证提出两处问题已修复：先看任务单状态再交给 stop_guard、只有开发/修复单交给 stop_guard，stop_guard 连续拦截放行时补写事件；实机确认 `--settings` 注册的 Stop hook 在后台会话里会执行；改动 `team-pm/scripts/worker_guard.py`、`tests/test_worker_guard.py`

- [x] 阶段4：team-pm skill
  - 涉及模块：team-pm/SKILL.md
  - 依赖：阶段2、阶段3
  - 内容：立项对话、主循环、事件处理、攒问题统一问人、确认点处理、失败重派、可选钉钉通知、恢复方式
  - 完成标准：SKILL.md 不超过 200 行；按其中流程手动走一遍立项 → 派需求单 → 收到完成事件
  - 验证：SKILL.md 95 行，引用的 board 命令逐个核对都存在；按流程在本仓库立项 board-version、派需求单，产品会话写出 `docs/prd/board-version.md`（9 条可执行的验收标准）并提交到项目分支，`wait` 收到完成事件，`reap` 停掉会话；补了 `project resolve`、`notify` 命令；改动 `team-pm/SKILL.md`、`board.py`、`board_store.py`

- [x] 阶段5：team-product skill
  - 涉及模块：team-product/SKILL.md、team-product/templates/prd.md
  - 依赖：阶段1
  - 内容：需求、答疑、方案覆盖检查、验收四类任务单的做法；需求文档模板（验收标准必须可检查）
  - 完成标准：用一句话需求派一张需求单，产出的需求文档每条验收标准都能写成测试用例
  - 验证：按协议和 board 命令逐条核对 SKILL.md（80 行）与模板，命令和参数都存在；实际派需求单放到阶段8端到端里一起验证；改动 `team-product/SKILL.md`、`templates/prd.md`

- [x] 阶段6：team-dev skill
  - 涉及模块：team-dev/SKILL.md
  - 依赖：阶段1
  - 内容：方案单（调 tech-design + 子 agent 自审）、开发单（调 phased-dev-loop + 子 agent 代码审查 + 本地提交）、修复单、答疑单的做法
  - 完成标准：对演示仓库派一张方案单和一张开发单，都能在没人参与的情况下 `item done`
  - 验证：按协议和 board 命令逐条核对 SKILL.md（86 行），代码审查范围改用 PM 记录的“开发基线”；实际派方案单、开发单放到阶段8端到端里一起验证；改动 `team-dev/SKILL.md`

- [x] 阶段7：team-qa skill
  - 涉及模块：team-qa/SKILL.md、team-qa/templates/test-plan.md、team-qa/templates/test-report.md
  - 依赖：阶段1
  - 内容：测试计划单（按验收标准写用例）、测试单（跑自动化测试 + 按用例验证 + 每个缺陷建修复单）、回归规则；QA 不改业务代码，只能加测试代码
  - 完成标准：对一个故意留了缺陷的分支派测试单，能建出指向开发的修复单，修复后回归通过
  - 验证：按协议和 board 命令逐条核对 SKILL.md（66 行）与两个模板；实际派测试单放到阶段8端到端里一起验证；为它补了 `item list --type/--from` 筛选；改动 `team-qa/SKILL.md`、`templates/test-plan.md`、`templates/test-report.md`

- [x] 阶段8：端到端试跑与 progress-pulse 接入
  - 涉及模块：progress-pulse/SKILL.md、docs/design（本方案的验证记录）
  - 依赖：阶段4、阶段5、阶段6、阶段7
  - 内容：按测试方案的端到端用例同时跑两个项目；progress-pulse 增加检查看板一项
  - 完成标准：两个项目都走到“交付”，人只在三个确认点回复过；`board.py status` 显示两个项目阶段为交付
  - 验证：按用户选择在本仓库临时 worktree 里同时跑 board-version、stop-guard-bg 两个项目，确认点由 PM 代答（项目卡 `人工确认: 无`）；两个项目各 7 张单（需求、方案、测试计划、方案覆盖检查、开发、测试、验收），全程没有人工介入，`board.py status` 显示两个项目都是交付；另在 board-version 分支埋一个缺陷派测试单，QA 建出修复单 0009，开发修复后 QA 回归通过（覆盖阶段5～7完成标准里的实际派单）；答疑、ask-human 后 reply 再派回原会话这条路径只有单测覆盖，没有实机跑；progress-pulse 增加检查看板；改动 `progress-pulse/SKILL.md`

## 9. 待确认问题

无。评审时确认的结论：

- 角色会话用 `claude --bg` 后台会话，一张任务单一个会话。
- 默认确认点为需求、方案、交付，项目卡里可以去掉。
- 角色会话默认权限模式 `auto`，可在 `config.json` 改。
- 要人确认时默认只在 PM 会话里说，`notify: dws` 时同时发钉钉。
- 看板放本机 `~/.claude/team/`。

## 10. 收尾记录

- 整体审查（子 agent）确认的问题已修复：回归没修好时 PM 会一直等（QA 第 1 次没修好也建新修复单，PM 按测试结果转人）；一次停机同时报“会话停下”和“会话退出”（hook 写事件时同时标记已报告）；QA 没有答疑单做法；人介入后重试次数不清零；测试单不等测试计划；并行提交带上别人暂存的文件（统一 `git commit -- <路径>`）；单号不是数字时报错堆栈；结果里的 `##` 行截断正文。
- 未改：拦截计数文件没加锁（只会让放行晚一点）；各角色 SKILL.md 的“注意”一节与协议有少量重复。
- 全量测试：team-pm 113 个、phased-dev-loop、dev-flow 的测试全部通过（team-pm 用例数包含试跑合并进来的 version 子命令和 Stop hook 识别后台 Bash 的测试）。
- 后续调整：角色默认改为以子 agent 方式运行（`team-pm/agents/` 下三个 agent 定义，预加载角色 skill，Stop hook 写在定义里，转成 SubagentStop，按子 agent 自己的会话记录找领取的单），`claude --bg` 后台会话保留为 `runner: session`。实测：子 agent 可以再派子 agent（默认最多 3 层）；子 agent 没收尾就结束时会被 hook 拦下；重派时开新子 agent 并带“接着做”提示词。每次派单都开新子 agent 或新会话，PM 在项目交付、话题切换时提醒用户 `/clear`。
