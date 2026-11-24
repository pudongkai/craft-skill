# board.py version 子命令验收结论

项目: board-version
作者: 产品（团队角色）　　日期: 2026-11-24
验收单: 0007
需求文档: docs/prd/board-version.md
测试报告: docs/test/board-version-report.md

## 1. 验收范围

- 验收时提交号: d0a71ee（开发提交 2075e30 + QA 测试提交）
- 分支: feat/board-version
- 方式: 读测试报告，并在 worktree 根目录自己跑了全部 9 条验收标准；临时看板放在会话临时目录，跑完已删除

## 2. 逐条结论

| 编号 | 结论 | 依据 |
| --- | --- | --- |
| AC-1 | 通过 | 自己跑 `env -u TEAM_BOARD python3 team-pm/scripts/board.py version`：输出 `版本: 0.1.0` / `看板根目录: /Users/dongkaipu/.claude/team`，退出码 0；报告 TC-1、TC-2 |
| AC-2 | 通过 | 自己跑 `TEAM_BOARD=<临时目录>/xx-board`（不存在）：第二行为该绝对路径，退出码 0；报告 TC-3、TC-17 |
| AC-3 | 通过 | 自己跑 `TEAM_BOARD='~/my-board'`：第二行 `看板根目录: /Users/dongkaipu/my-board`，无 `~`；报告 TC-4 |
| AC-4 | 通过 | AC-2 运行后 `ls` 确认 `xx-board` 不存在，`~/my-board` 也未被创建；报告 TC-5、TC-16 |
| AC-5 | 通过 | 用 `project new`、`item new`、`item claim`、`item block` 建临时看板（含 `events.jsonl`），version 前后所有文件的 shasum 列表完全一致；报告 TC-6、TC-7 |
| AC-6 | 通过 | 复制 scripts 到临时目录、把 `VERSION` 改成 `0.2.0` 后第一行为 `版本: 0.2.0`；`grep -rn 0.1.0 team-pm/scripts/` 只命中 `board.py:11 VERSION = "0.1.0"`；报告 TC-8～TC-10 |
| AC-7 | 通过 | `--help` 子命令列表含 `version`，说明为“打印脚本版本号和看板根目录”；报告 TC-11 |
| AC-8 | 通过 | `version extra` 退出码 2，stderr 为 argparse 用法报错 `unrecognized arguments: extra`；报告 TC-12、TC-13 |
| AC-9 | 通过 | `cd team-pm && python3 -m unittest discover -s tests` → Ran 71 tests，OK；报告 TC-18、TC-19（原有命令输出前后对比一致） |

“本次不做”核对：代码只加了 `version` 子命令（`team-pm/scripts/board.py` 的 `cmd_version`），没有 `--version` 全局参数、JSON 输出或其他脚本的版本号。

## 3. 修复单

无

## 4. 总体结论

验收通过：9 条验收标准全部通过，无修复单。
