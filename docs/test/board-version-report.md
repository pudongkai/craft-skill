# board.py version 子命令测试报告

项目: board-version
作者: QA（团队角色）　　日期: 2026-11-24
测试计划: docs/test/board-version.md
测试单: 0010（回归；前几轮 0006、0008）

## 1. 概要结论

通过

- 用例：通过 19，不通过 0，阻塞 0
- 全量测试：`cd team-pm && python3 -m unittest discover -s tests` → Ran 71 tests，OK
- 本轮为回归：修复单 0009（提交 5ea8474）把 `cmd_version` 第一行恢复为 `版本: {VERSION}`，已修好；上一轮 5 条不通过的用例全部通过

## 2. 执行环境

- 提交号: 5ea8474f6b2d905551c0e50e77848cf1042aa1f5（测试时 HEAD）
- 分支: feat/board-version
- 环境: macOS（Darwin 24.6.0），系统 `python3`，无第三方依赖；命令在 worktree 根目录运行
- 测试数据: 每条自动化用例用 `tempfile.TemporaryDirectory()` 建临时目录作为 `TEAM_BOARD` 和假 `HOME`，子进程环境去掉原有 `TEAM_BOARD`
- 测试代码: `team-pm/tests/test_version.py`（类 `VersionCommandTest`，15 个用例，之前轮次已提交，本轮未改）

## 3. 用例结果

自动化用例运行 `cd team-pm && python3 -m unittest discover -s tests`。

| 编号 | 结果 | 证据 |
| --- | --- | --- |
| TC-1 | 通过 | `test_tc1_default_root` ok（第一行 `版本: 0.1.0`，第二行 `看板根目录: <H>/.claude/team`） |
| TC-2 | 通过 | 手工：`env -u TEAM_BOARD python3 team-pm/scripts/board.py version; echo "exit=$?"` → `版本: 0.1.0` / `看板根目录: /Users/dongkaipu/.claude/team` / `exit=0` |
| TC-3 | 通过 | `test_tc3_tc17_...` ok；手工 `TEAM_BOARD=/tmp/xx-board ... version` → `版本: 0.1.0` / `看板根目录: /tmp/xx-board` / `exit=0` |
| TC-4 | 通过 | `test_tc4_tilde_expanded` ok |
| TC-5 | 通过 | `test_tc5_missing_dir_not_created` ok；手工运行后 `ls /tmp/xx-board` → `No such file or directory` |
| TC-6 | 通过 | `test_tc6_existing_board_unchanged` ok |
| TC-7 | 通过 | `test_tc7_empty_board_stays_empty` ok |
| TC-8 | 通过 | `test_tc8_output_follows_constant` ok（副本常量改为 0.2.0 后第一行 `版本: 0.2.0`） |
| TC-9 | 通过 | `test_tc9_version_defined_once` ok；`grep -rn "0\.1\.0" team-pm/scripts/` 只命中 `board.py:11:VERSION = "0.1.0"` |
| TC-10 | 通过 | `test_tc10_constant_format` ok |
| TC-11 | 通过 | `test_tc11_help_lists_version` ok；`--help` 有 `version             打印脚本版本号和看板根目录` |
| TC-12 | 通过 | `test_tc12_extra_positional_rejected` ok |
| TC-13 | 通过 | `test_tc13_unknown_option_rejected` ok |
| TC-14 | 通过 | `test_tc14_empty_team_board_uses_default` ok |
| TC-15 | 通过 | `test_tc15_relative_team_board` ok |
| TC-16 | 通过 | `test_tc16_missing_parents_not_created` ok |
| TC-17 | 通过 | `test_tc3_tc17_team_board_absolute_full_output` ok（stdout 恰好两行 `版本: 0.1.0` / `看板根目录: <tmp>/xx-board`） |
| TC-18 | 通过 | 全量 Ran 71 tests，OK；开发写的 `test_board.VersionTest` 9 条全部 ok |
| TC-19 | 通过 | 手工：临时看板里 `git init` 一个仓库后 `project new demo --repo ... --goal g`，`project list` → `demo  阶段 立项  优先级 P1  g`，与之前轮次一致；`git diff --stat b9c7f4e HEAD` 只有 `team-pm/scripts/board.py | 2 +-`（`cmd_version` 一行） |

## 4. 缺陷列表

| 修复单 | 对应 | 问题 | 状态 |
| --- | --- | --- | --- |
| 0009 | TC-1、TC-2、TC-8、TC-17、TC-18 / AC-1、AC-6 | baf22de 让 `version` 第一行输出 `版本: v0.1.0`，需求要求 `版本: 0.1.0`；如果确实要带 `v`，需先由产品修改 AC-1 | 已修好（0010 回归验证） |

## 5. 回归记录

### 第 1 轮（测试单 0010）

- 提交号: 5ea8474f6b2d905551c0e50e77848cf1042aa1f5
- 修复单 0009 → 已修好：`git show 5ea8474` 只把 `print(f"版本: v{VERSION}")` 改回 `print(f"版本: {VERSION}")`；按复现步骤 `env -u TEAM_BOARD python3 team-pm/scripts/board.py version` → 第一行 `版本: 0.1.0`；原失败的 5 条自动化用例（`test_board.VersionTest.test_default_root_under_home` / `test_output_follows_constant`，`test_version.VersionCommandTest.test_tc1_default_root` / `test_tc3_tc17_team_board_absolute_full_output` / `test_tc8_output_follows_constant`）全部 ok
- 受影响用例：TC-1、TC-2、TC-3、TC-5、TC-8、TC-9、TC-11、TC-17、TC-18、TC-19 重新验证，全部通过（其余用例随全量测试一起通过）
- 全量测试: Ran 71 tests，OK
- 新缺陷: 无
