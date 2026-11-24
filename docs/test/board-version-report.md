# board.py version 子命令测试报告

项目: board-version
作者: QA（团队角色）　　日期: 2026-11-24
测试计划: docs/test/board-version.md
测试单: 0008（上一轮 0006）

## 1. 概要结论

不通过：1 个缺陷（修复单 0009）

- 用例：通过 16，不通过 3（TC-1、TC-8、TC-17），阻塞 0
- 全量测试：`cd team-pm && python3 -m unittest discover -s tests` → Ran 71 tests，FAILED (failures=5)，5 条失败都是第一行多了 `v` 前缀
- 本轮起因：开发提交 baf22de 把 `cmd_version` 的第一行从 `版本: {VERSION}` 改成 `版本: v{VERSION}`，与需求 AC-1（第一行 `版本: 0.1.0`）、AC-6（改常量后第一行 `版本: 0.2.0`）不符

## 2. 执行环境

- 提交号: baf22ded461e9469407aadf8224151aee40ac17c（测试时 HEAD）
- 分支: feat/board-version
- 环境: macOS（Darwin 24.6.0），系统 `python3`，无第三方依赖；命令在 worktree 根目录运行
- 测试数据: 每条自动化用例用 `tempfile.TemporaryDirectory()` 建临时目录作为 `TEAM_BOARD` 和假 `HOME`，子进程环境去掉原有 `TEAM_BOARD`
- 测试代码: `team-pm/tests/test_version.py`（类 `VersionCommandTest`，15 个用例，上一轮已提交，本轮未改）

## 3. 用例结果

自动化用例运行 `cd team-pm && python3 -m unittest discover -s tests`。

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| TC-1 | 不通过 | `test_tc1_default_root` 失败：实际第一行 `'版本: v0.1.0'`，期望 `'版本: 0.1.0'`（第二行 `看板根目录: <H>/.claude/team` 正确） |
| TC-2 | 不通过（同 0009，计入 TC-1 根因，不单独计数） | 手工：`env -u TEAM_BOARD python3 team-pm/scripts/board.py version; echo "exit=$?"` → `版本: v0.1.0` / `看板根目录: /Users/dongkaipu/.claude/team` / `exit=0`；第二行和退出码符合，第一行不符合 AC-1 |
| TC-3 | 通过 | `test_tc3_tc17_...` 中第二行为 `看板根目录: <tmp>/xx-board`，退出码 0；手工 `TEAM_BOARD=/tmp/xx-board ... version` 第二行 `看板根目录: /tmp/xx-board` |
| TC-4 | 通过 | `test_tc4_tilde_expanded` ok |
| TC-5 | 通过 | `test_tc5_missing_dir_not_created` ok |
| TC-6 | 通过 | `test_tc6_existing_board_unchanged` ok |
| TC-7 | 通过 | `test_tc7_empty_board_stays_empty` ok |
| TC-8 | 不通过 | `test_tc8_output_follows_constant` 失败：副本常量改为 0.2.0 后第一行 `'版本: v0.2.0'`，期望 `'版本: 0.2.0'`（说明确实读常量，但格式不符） |
| TC-9 | 通过 | `test_tc9_version_defined_once` ok；`grep -rn "0\.1\.0" team-pm/scripts/` 只命中 `board.py:11:VERSION = "0.1.0"` |
| TC-10 | 通过 | `test_tc10_constant_format` ok |
| TC-11 | 通过 | `test_tc11_help_lists_version` ok；`--help` 有 `version             打印脚本版本号和看板根目录` |
| TC-12 | 通过 | `test_tc12_extra_positional_rejected` ok |
| TC-13 | 通过 | `test_tc13_unknown_option_rejected` ok |
| TC-14 | 通过 | `test_tc14_empty_team_board_uses_default` ok |
| TC-15 | 通过 | `test_tc15_relative_team_board` ok |
| TC-16 | 通过 | `test_tc16_missing_parents_not_created` ok |
| TC-17 | 不通过 | `test_tc3_tc17_team_board_absolute_full_output` 失败：stdout `'版本: v0.1.0\n看板根目录: <tmp>/xx-board\n'` != 期望 `'版本: 0.1.0\n看板根目录: <tmp>/xx-board\n'` |
| TC-18 | 不通过（同 0009，不单独计数） | 全量 71 条，5 条失败：`test_board.VersionTest.test_default_root_under_home`、`test_board.VersionTest.test_output_follows_constant`（开发自己写的用例）及上面 TC-1/TC-8/TC-17 三条，失败信息都是 `v` 前缀；其余 66 条 ok |
| TC-19 | 通过 | 手工：临时看板 `git init` 一个仓库后 `project new demo --repo ... --goal g`，`project list` → `demo  阶段 立项  优先级 P1  g`，与上一轮记录一致；`--help` 子命令列表与上一轮相同。baf22de 只改了 `cmd_version` 一行，不涉及其他子命令 |

计数说明：TC-2、TC-18 的失败由同一根因引起，概要里“不通过 3”只计 TC-1、TC-8、TC-17 这三条直接检查第一行格式的自动化用例；按用例逐条算则为 通过 14、不通过 5。

## 4. 缺陷列表

| 修复单 | 对应 | 问题 | 状态 |
| --- | --- | --- | --- |
| 0009 | TC-1、TC-2、TC-8、TC-17、TC-18 / AC-1、AC-6 | baf22de 让 `version` 第一行输出 `版本: v0.1.0`，需求要求 `版本: 0.1.0`；如果确实要带 `v`，需先由产品修改 AC-1 | 待修复 |

## 5. 回归记录

无
