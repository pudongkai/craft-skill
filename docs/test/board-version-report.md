# board.py version 子命令测试报告

项目: board-version
作者: QA（团队角色）　　日期: 2026-11-24
测试计划: docs/test/board-version.md
测试单: 0006

## 1. 概要结论

通过

- 用例：通过 19，不通过 0，阻塞 0
- 全量测试：`cd team-pm && python3 -m unittest discover -s tests` → Ran 71 tests，OK（原有 56 条 + 新增 15 条，0 失败 0 错误）

## 2. 执行环境

- 提交号: 2075e30（开发单 0005 的提交，测试时 HEAD）
- 分支: feat/board-version
- 环境: macOS（Darwin 24.6.0），系统 `python3`，无第三方依赖；命令在 worktree 根目录运行
- 测试数据: 每条自动化用例用 `tempfile.TemporaryDirectory()` 建临时目录作为 `TEAM_BOARD` 和假 `HOME`，子进程环境去掉原有 `TEAM_BOARD`
- 新增的测试代码: `team-pm/tests/test_version.py`（类 `VersionCommandTest`，15 个用例，用 subprocess 跑真实 `board.py` 进程）

## 3. 用例结果

自动化用例都在 `team-pm/tests/test_version.py::VersionCommandTest`，运行 `cd team-pm && python3 -m unittest tests.test_version -v`，15 个全部 ok。

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| TC-1 | 通过 | `test_tc1_default_root`：去掉 TEAM_BOARD、HOME=临时目录，输出恰好 `版本: 0.1.0`、`看板根目录: <H>/.claude/team`，退出码 0，无 `~` |
| TC-2 | 通过 | 手工：`env -u TEAM_BOARD python3 team-pm/scripts/board.py version; echo "exit=$?"` → `版本: 0.1.0` / `看板根目录: /Users/dongkaipu/.claude/team` / `exit=0`（`$HOME=/Users/dongkaipu`） |
| TC-3 | 通过 | `test_tc3_tc17_team_board_absolute_full_output`：TEAM_BOARD=`<tmp>/xx-board`（不存在），第二行为该路径，退出码 0 |
| TC-4 | 通过 | `test_tc4_tilde_expanded`：TEAM_BOARD=`~/my-board` → `看板根目录: <H>/my-board`，无 `~` |
| TC-5 | 通过 | `test_tc5_missing_dir_not_created`：运行后 `<tmp>/not-exist` 仍不存在 |
| TC-6 | 通过 | `test_tc6_existing_board_unchanged`：用 `project new`、两次 `item new`、`item claim`、`item block` 建看板并产生 `events.jsonl`，运行 version 前后所有文件的相对路径、内容、mtime 完全相同 |
| TC-7 | 通过 | `test_tc7_empty_board_stays_empty`：空目录运行后 `os.listdir` 仍为空 |
| TC-8 | 通过 | `test_tc8_output_follows_constant`：复制 scripts 到临时目录，把 `VERSION = "0.1.0"` 改成 `0.2.0`，副本输出第一行 `版本: 0.2.0` |
| TC-9 | 通过 | `test_tc9_version_defined_once`：扫描 `team-pm/scripts/*.py`，`0.1.0` 只命中 `board.py` 的 `VERSION = "0.1.0"` 一行 |
| TC-10 | 通过 | `test_tc10_constant_format`：`board.VERSION` 匹配 `^\d+\.\d+\.\d+$` 且等于 `0.1.0` |
| TC-11 | 通过 | `test_tc11_help_lists_version`：`--help` 退出码 0，有 `    version             打印脚本版本号和看板根目录` 一行 |
| TC-12 | 通过 | `test_tc12_extra_positional_rejected`：`version extra` 退出码 2，stderr 含 `usage:` 和 `unrecognized arguments: extra`，stdout 为空 |
| TC-13 | 通过 | `test_tc13_unknown_option_rejected`：`version --foo` 退出码非 0，stderr 含 `usage:` 和 `--foo` |
| TC-14 | 通过 | `test_tc14_empty_team_board_uses_default`：TEAM_BOARD='' → `看板根目录: <H>/.claude/team` |
| TC-15 | 通过 | `test_tc15_relative_team_board`：在 `<C>` 下 TEAM_BOARD=`rel-board` → `看板根目录: <C>/rel-board` |
| TC-16 | 通过 | `test_tc16_missing_parents_not_created`：TEAM_BOARD=`<tmp>/a/b/c`，退出码 0，`<tmp>/a` 不存在 |
| TC-17 | 通过 | `test_tc3_tc17_team_board_absolute_full_output`：stdout 完全等于 `"版本: 0.1.0\n看板根目录: <tmp>/xx-board\n"`，stderr 为空 |
| TC-18 | 通过 | `cd team-pm && python3 -m unittest discover -s tests` → `Ran 71 tests ... OK` |
| TC-19 | 通过 | 手工：在基线 a4ee0aa（临时 detached worktree）和 2075e30 上用各自新建的临时看板跑 `project new demo` 后的 `project list` 和 `--help`。`project list` 输出完全相同（`demo  阶段 立项  优先级 P1  g`）；`--help` 的 diff 只有子命令列表多出 `version` 和新增一行 `version  打印脚本版本号和看板根目录` |

## 4. 缺陷列表

无

## 5. 回归记录

无
