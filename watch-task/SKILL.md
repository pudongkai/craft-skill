---
name: watch-task
description: 主管让你「盯着」「盯梢」「盯一下」他电脑上某个 Claude Code 会话（一个在干活的 cc 任务）时用：只要它停下不干活了——做完、要决策、出错、等授权、退出——就通知主管；主管说「不用盯了 / 别盯梢了 / 解除」就撤掉。也用于替主管给被盯的会话回一句话（如「开始写代码」）。收到主管发来以「[盯任务]」开头的消息（hook 叫醒你的）时也用它。
---
# 盯任务

脚本：`python3 scripts/watch.py <命令>`（相对本技能目录，运行前拼成绝对路径）。

| 命令 | 作用 |
|---|---|
| `sessions` | 列出本机正在跑的 Claude Code 会话：名称、busy/idle、pid、会话 id、目录 |
| `add <名称\|会话id\|pid>` | 开始盯，并把 hook 注册进那个会话的项目（名称就是主管给会话起的名字，如「clauve订阅」） |
| `remove <名称\|会话id\|all>` | 解除；该项目没有在盯的会话了就删掉 hook |
| `list` | 现在在盯哪些、各自状态 |
| `check` | 检查所有在盯的会话（含 hook 记下的事件）；没新情况输出 `OK`，否则每个会话一段 `【名称】…`；叫醒消息没发出去时多一段 `【叫醒】…` |
| `reply <名称> <文字>` | 在那个会话所在的 Terminal 标签里替主管输入一句并回车（长文字会被当成粘贴留在输入框，所以脚本会再补一次回车）；只在它停在输入框（idle / shell）时发 |

## 首次使用

1. 主管在本机装好 dws 并用自己的钉钉账号 `dws auth login`。
2. 照 `config.example.json` 写 `state/config.json`：`dws_profile` 取 `DWS_SHIM_DISABLE=1 dws profile list` 里主管那条的 `profile`；
   `chat_id` 是主管和你单聊的会话 id（【当前会话】里的会话 ID）；dws 不在 `~/.local/bin/dws` 时填 `dws`。
3. 用 dws 以主管身份往这个单聊发一条测试消息（`chat +messages-send --as user --chat-id … --text … --yes`，带 `--profile`、环境变量 `DWS_SHIM_DISABLE=1`），确认你被叫醒。
没配 `state/config.json` 时 hook 只记事件、不叫醒，只能在主管问起时 `check`。

## 怎么叫醒我（不靠定时任务）

`add` 把 `scripts/hook.py` 临时注册到被盯会话项目的 `.claude/settings.local.json`（Stop / Notification / SessionEnd；
别人的设置原样保留，`remove` 时只删我们加的，文件因此变空就删文件）。被盯会话一触发：

1. hook 把事件记进 `state/events.jsonl`（最后一句话、要授权的提示、还没交回结果的后台子 agent 数）；
2. 用 dws **以主管身份**往主管和我的单聊发一条 `[盯任务] <名称> 停下了 / 需要你处理：… / 会话退出了。运行盯任务技能的 check…`，
   这条消息把我叫醒。配置在 `state/config.json`（`dws_profile` 主管的 dws 账号、`chat_id` 单聊会话 id），发送记录在 `state/wake.log`。
   和 Roster 抢 dws 锁失败会自动重试；最终没发出去（含 dws 只预演没真发）记进 `state/wake-failed.jsonl`，下次 `check` 时报出来。

不叫醒的情况：Stop 时会话记录里还有没交回结果的后台子 agent（没真停，交回后会自己接着干）；
Notification 只在要授权、要回答提问（`permission_prompt`、`elicitation_*`、`agent_needs_input`）时叫醒，空闲等输入由 Stop 报，其余类型不报。

## 收到「[盯任务] …」消息时

这是 hook 替主管发来的，不是主管手打的。跑 `check`，按结果回复主管：
- 哪个会话、为什么停（做完 / 要你决策 / 出错 / 等授权 / 退出），把它最后的话概括成 3 行内；要决策的把选项列清楚，提示可以直接回我一句让我转给它。
- `check` 输出 `OK`（已经报过或它又开工了）→ 整条回复只写 `NO-REPLY`。

## 判定「停止干活」（check）

- hook 报了 Stop，或会话状态（`~/.claude/sessions/<pid>.json` 的 `status`）不是 `busy`（`shell` 是停着但开着后台 shell，也算停下）且已持续 1 分钟 → 「停下了」，附最后的话；最后的话里有问句/要确认的 → 标「要你决策」。同一次停下只报一次。
- 还在干活，但某个操作 10 分钟没结果 → 「可能卡住或在等授权」（hook 抓不到这种，只有被叫醒或主管问起时 check 才会发现）。
- 会话记录里出现 API/系统错误 → 报。
- 进程没了 → 「会话已退出」，自动撤掉这一项。

## 流程

1. **开始盯**：主管说「盯一下 X」「盯梢 X」→ `sessions` 找到对应会话（对不上或有多个就把候选列给主管确认）→ `add`。
2. **被叫醒**：见上。
3. **替主管回话**：主管说「让它 …」→ `reply <名称> "<话>"`，等 30–60 秒再读会话记录确认它收到并开工，把它的回应概括给主管。
   `reply` 拒绝发送（它在干活或停在授权选择上）时，把原因告诉主管，不要换别的办法硬发。
4. **解除**：主管说「不用盯了」「别盯梢了」→ `remove <名称>`（或 `all`）。

## 注意

- 我自己起后台进程用 roster 发钉钉消息**发不出去**（只有回复对话的时候才有权限），所以走 dws 主管身份叫醒我，再由我在对话里回复。
- dws 登录会过期（refresh 约 30 天）；`check` 报【叫醒】失败、原因是未登录/鉴权失败时，请主管重新 `dws auth login`。
- 本技能目录被删或搬走前先 `remove all`，否则被盯项目的 `settings.local.json` 里会留下失效的 hook（脚本不在时 hook 直接跳过，不影响会话）。
- `reply` 只支持 macOS 自带 Terminal；会话不在 Terminal 里时直说做不了。
