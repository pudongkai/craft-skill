---
name: watch-task
description: 主管让你「盯着」「盯梢」「盯一下」他电脑上某个 Claude Code 会话（一个在干活的 cc 任务）时用：只要它停下不干活了——做完、要决策、出错、等授权、退出——就通知主管；主管说「不用盯了 / 别盯梢了 / 解除」就撤掉。也用于替主管给被盯的会话回一句话（如「开始写代码」）。主管转来或回复以「[盯任务]」开头的通知（hook 直接发给主管的）时也用它。
---
# 盯任务

脚本：`python3 scripts/watch.py <命令>`（相对本技能目录，运行前拼成绝对路径）。

| 命令 | 作用 |
|---|---|
| `sessions` | 列出本机正在跑的 Claude Code 会话：名称、busy/idle、pid、会话 id、目录 |
| `add <名称\|会话id\|pid>` | 开始盯（名称就是主管给会话起的名字，如「clauve订阅」）。只写本技能的 `state/`，不改被盯项目的文件；hook 还没注册时会提示 |
| `remove <名称\|会话id\|all>` | 解除 |
| `list` | 现在在盯哪些、各自状态；hook 没注册时第一行提示 |
| `install` / `uninstall` | 把 hook 注册进（或删出）用户级 `~/.claude/settings.json`。**由主管在终端运行**，见“首次使用” |
| `check` | 检查所有在盯的会话（含 hook 记下的事件）；没新情况输出 `OK`，否则每个会话一段 `【名称】…`；通知没发出去时多一段 `【通知】…` |
| `reply-check <名称>` | 替主管回话前的检查：找到那个会话，确认 `crossSessionInbound` 是 `accept`（不是的话输出要提醒主管的话，退出码 3）。真正发送用会话间发消息，见“流程”第 3 步 |

## 首次使用

1. 本机 dws 登录了你（数字员工）自己的钉钉账号（`DWS_SHIM_DISABLE=1 dws profile list` 里能看到你那条）。
2. 照 `config.example.json` 写 `state/config.json`：`dws_profile` 取上面列表里**你自己**那条的 `profile`；
   `to_user_id` 是主管的 userId（`roster contact me` 的 supervisor）；dws 不在 `~/.local/bin/dws` 时填 dws 的完整路径。
   `state/` 不在仓库里，同步技能时要保留，否则配置会被清掉。
3. 请主管跑一次 `python3 "<本技能目录>/scripts/hook.py" --send 测试 "[盯任务] 测试通知"`，确认收到你发的单聊。
4. 请主管在自己的终端运行一次 `python3 "<本技能目录>/scripts/watch.py" install`（把路径拼成绝对路径发给主管）。
   这一步要写 `~/.claude/settings.json`，不在我能写的目录里，所以必须由主管来做；只需要做一次，以后盯哪个会话都不用再改设置。
5. 要替主管回话的，请主管把 `crossSessionInbound` 设成 `accept`（`/config` 里的「Messages from your other sessions」，
   或 `~/.claude/settings.json` 里加 `"crossSessionInbound": "accept"`）。不设的话，被盯会话开着跳过权限确认的模式时，
   我发过去的消息会被拦下等主管在终端里点确认，5 分钟没人点就丢掉。
没配 `state/config.json` 时 hook 只记事件、不发通知，只能在主管问起时 `check`。

## 怎么通知主管（不靠定时任务，也不用叫醒我）

`install` 把 `scripts/hook.py` 注册到用户级 `~/.claude/settings.json`（Stop / Notification / SessionEnd；别人的设置原样保留，
`uninstall` 时只删我们加的）。本机所有会话停下时都会运行它，hook 按 `state/watches.json` 里的会话 id 过滤，没在盯的直接跳过。
被盯会话一触发：

1. hook 把事件记进 `state/events.jsonl`（最后一句话、要授权的提示、还没交回结果的后台子 agent 数）；
2. 用 dws **以我（数字员工）的账号**直接单聊发给主管：`[盯任务] <名称> 停下了 / 需要你处理：… / 会话退出了`，
   停下时附上它最后说的话（截到 300 字），结尾提示主管有事回我一句。hook 跑在主管的 Claude 进程里，不受我的沙箱限制。
   配置在 `state/config.json`（`dws_profile` 我的 dws 账号、`to_user_id` 主管的 userId；没有 `to_user_id` 时发到 `chat_id`），
   发送记录在 `state/wake.log`。
   和 Roster 抢 dws 锁失败会自动重试；最终没发出去（含 dws 只预演没真发）记进 `state/wake-failed.jsonl`，下次 `check` 时报出来。

不发通知的情况：Stop 时会话记录里还有没交回结果的后台子 agent（没真停，交回后会自己接着干）；
Notification 只在要授权、要回答提问（`permission_prompt`、`elicitation_*`、`agent_needs_input`）时通知，空闲等输入由 Stop 报，其余类型不报。

## 主管回复「[盯任务] …」通知时

通知是 hook 用我的账号直接发给主管的，我当时并不知道。主管回我「让它…」「看看怎么回事」时，先跑 `check` 看现状，
再按「替主管回话」处理，或查清停下的原因（最后的话、报错、要决策的选项）汇报给主管。

## 判定「停止干活」（check）

- hook 报了 Stop，或会话状态（`~/.claude/sessions/<pid>.json` 的 `status`）不是 `busy`（`shell` 是停着但开着后台 shell，也算停下）且已持续 1 分钟 → 「停下了」，附最后的话；最后的话里有问句/要确认的 → 标「要你决策」。同一次停下只报一次。
- 还在干活，但某个操作 10 分钟没结果 → 「可能卡住或在等授权」（hook 抓不到这种，只有主管问起时 check 才会发现）。
- 会话记录里出现 API/系统错误 → 报。
- 进程没了 → 「会话已退出」，自动撤掉这一项。

## 流程

1. **开始盯**：主管说「盯一下 X」「盯梢 X」→ `sessions` 找到对应会话（对不上或有多个就把候选列给主管确认）→ `add`。
   `add` 提示 hook 没注册时，照提示把 `install` 命令发给主管，请他运行一次；不要自己去改被盯项目或 `~/.claude` 下的设置文件。
   在主管装好之前，会话照样记成在盯，主管问起时 `check` 能查到状态，只是停下时没法主动通知主管。
2. **收到通知后**：见上。
3. **替主管回话**：主管说「让它 …」→
   1. 运行 `reply-check <名称>`。退出码 3（`crossSessionInbound` 不是 `accept` 或确认不了）→ 把它输出的提醒原样转给主管，
      请主管设置好再说，这次不发；退出码 2 → 找不到会话，把候选列给主管确认。
   2. 检查通过 → 用 `ListAgents` 找到这个会话（名称对得上 `reply-check` 输出的会话），用 `SendMessage` 把主管的话发给它，
      消息开头写明「主管让我转告：」。不要用 AppleScript 往终端里打字，Roster 沙箱不允许控制“终端”。
   3. 等 30–60 秒再读会话记录确认它收到并开工，把它的回应概括给主管。
   会话间发消息需要 Claude Code v2.1.224 及以上；`ListAgents` 里找不到这个会话时，告诉主管做不了，不要换别的办法硬发。
4. **解除**：主管说「不用盯了」「别盯梢了」→ `remove <名称>`（或 `all`）。

## 注意

- 通知走 dws 直接发，不经过 roster 的审批记录；接收人固定是主管本人，不要把 `to_user_id` 改成别人。
- dws 登录会过期（refresh 约 30 天）；`check` 报【通知】失败、原因是未登录/鉴权失败时，请主管用我的账号重新 `dws auth login`。
- 本技能目录被删或搬走前请主管运行 `uninstall`，否则 `~/.claude/settings.json` 里会留下失效的 hook（脚本不在时 hook 直接跳过，不影响会话）。
  搬到新位置后在新位置重新 `install`。
- `crossSessionInbound: accept` 对本机所有发消息的会话都放行，不能只放行我一个；发过去的话会被对方当成指令执行。
  只转主管明确说过的话，不自己加指令。
