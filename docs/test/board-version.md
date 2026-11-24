# board.py version 子命令测试计划

项目: board-version
作者: QA（团队角色）　　日期: 2026-11-24
需求文档: docs/prd/board-version.md

## 1. 测试范围

- US-1 版本号：CLI 子命令 `python3 team-pm/scripts/board.py version` 第一行输出的版本号，以及版本号只在一处常量定义（BR-1、BR-2）。
- US-2 看板根目录：`version` 第二行输出的看板根目录，覆盖未设置、设置为绝对路径、含 `~`、空字符串、相对路径几种 `TEAM_BOARD`（BR-3）。
- 输出格式：固定两行“标签: 值”（BR-4）。
- 只读：不创建目录、不改文件、不写事件（BR-5）。
- 参数处理与帮助：多余参数报错、`--help` 列出 `version`（BR-6）。
- 已有功能不受影响（AC-9）。

## 2. 不测什么

- `board.py --version` / `-V` 全局参数：需求“本次不做”。
- JSON 等机器可读输出、Python 版本 / git 提交号等其他信息：需求“本次不做”。
- 其他脚本（team-dev、team-product、team-qa 等）的版本号：需求“本次不做”。

## 3. 环境与数据

- 环境：项目 worktree 本地，Python 3（系统自带 `python3`），无第三方依赖。命令都在 worktree 根目录下运行。
- 测试数据：每条自动化用例用 `tempfile.TemporaryDirectory()` 建临时目录，作为 `TEAM_BOARD` 或假的 `HOME`；用例结束自动删除。AC-1 用例把子进程的 `HOME` 设成临时目录，避免依赖和改动真实的 `~/.claude/team`。
- 全量测试命令：`cd team-pm && python3 -m unittest discover -s tests`（当前 47 条，基线全部通过）。
- 自动化测试位置：新建 `team-pm/tests/test_version.py`，沿用 unittest。需要检查退出码、标准输出 / 标准错误、环境变量的用例用 `subprocess.run([sys.executable, <board.py 路径>, ...], env=..., capture_output=True, text=True)` 跑真实进程；由 QA 在测试单里写。

## 4. 用例

| 编号 | 对应 AC | 前置条件 | 步骤 | 期望结果 | 方式 |
| --- | --- | --- | --- | --- | --- |
| TC-1 | AC-1 | 子进程环境去掉 `TEAM_BOARD`，`HOME` 设为临时目录 `<H>` | 运行 `python3 team-pm/scripts/board.py version` | 退出码 0；标准输出恰好两行：`版本: 0.1.0`、`看板根目录: <H>/.claude/team`；输出不含 `~` | 自动化（QA，team-pm/tests/test_version.py） |
| TC-2 | AC-1 | 当前用户环境，未设置 `TEAM_BOARD` | 在 shell 里运行 `env -u TEAM_BOARD python3 team-pm/scripts/board.py version; echo "exit=$?"` | 输出两行，第二行为 `看板根目录: $HOME/.claude/team`（`$HOME` 展开后的实际值）；`exit=0` | 手工 |
| TC-3 | AC-2 | `TEAM_BOARD=<tmp>/xx-board`，该目录不存在 | 运行 `version` | 退出码 0；第二行为 `看板根目录: <tmp>/xx-board` | 自动化（QA，team-pm/tests/test_version.py） |
| TC-4 | AC-3 | `TEAM_BOARD='~/my-board'`，`HOME=<H>` | 运行 `version` | 第二行为 `看板根目录: <H>/my-board`，不含 `~` | 自动化（QA，team-pm/tests/test_version.py） |
| TC-5 | AC-4 | `TEAM_BOARD=<tmp>/not-exist`，目录不存在 | 运行 `version` | 退出码 0；运行后 `<tmp>/not-exist` 仍不存在 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-6 | AC-5 | 用现有命令建一个看板（`board.py project new ...` 或 `board_store.create_project`），并追加若干事件，`events.jsonl` 存在 | 记下看板根目录下全部文件的相对路径、大小、修改时间和 `events.jsonl` 内容；运行 `version`；再记一次 | 前后文件列表、各文件内容（含 `events.jsonl`）完全相同 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-7 | AC-5 | 看板根目录已存在但为空目录（无 `events.jsonl`） | 运行 `version` | 运行后目录仍为空，没有生成 `events.jsonl`、`config.json` 等任何文件 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-8 | AC-6 | 把 `team-pm/scripts/` 复制到临时目录，在副本里把版本常量的值从 `0.1.0` 改成 `0.2.0` | 运行副本的 `board.py version` | 第一行为 `版本: 0.2.0`（证明打印的是常量而非写死的字符串） | 自动化（QA，team-pm/tests/test_version.py） |
| TC-9 | AC-6 | 无 | 运行 `grep -rn "0\.1\.0" team-pm/scripts/` | 只有一处命中，即版本常量的定义行 | 自动化（QA，team-pm/tests/test_version.py，扫描 scripts 目录下 .py 文件） |
| TC-10 | AC-6 | 无 | 读取版本常量的值 | 匹配 `^\d+\.\d+\.\d+$`，且等于 `0.1.0` | 自动化（QA，team-pm/tests/test_version.py） |
| TC-11 | AC-7 | 无 | 运行 `python3 team-pm/scripts/board.py --help` | 退出码 0；子命令列表里有 `version`，同一行或紧随其后有一句中文说明（含至少一个汉字） | 自动化（QA，team-pm/tests/test_version.py） |
| TC-12 | AC-8 | 无 | 运行 `board.py version extra` | 退出码非 0（argparse 为 2）；标准错误含 `usage:` 和 `unrecognized arguments: extra` 之类的用法报错；标准输出为空 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-13 | AC-8 | 无 | 运行 `board.py version --foo` | 退出码非 0；标准错误有 argparse 用法报错 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-14 | AC-1、BR-3 | `TEAM_BOARD=''`（设置为空字符串），`HOME=<H>` | 运行 `version` | 退出码 0；第二行为 `看板根目录: <H>/.claude/team`（空值按未设置处理） | 自动化（QA，team-pm/tests/test_version.py） |
| TC-15 | AC-2、BR-3 | 工作目录为临时目录 `<C>`，`TEAM_BOARD=rel-board`（相对路径） | 在 `<C>` 下运行 `version` | 退出码 0；第二行为 `看板根目录: <C>/rel-board`（绝对路径，以 `/` 开头） | 自动化（QA，team-pm/tests/test_version.py） |
| TC-16 | AC-4、BR-5 | `TEAM_BOARD=<tmp>/a/b/c`，多级父目录都不存在 | 运行 `version` | 退出码 0；`<tmp>/a` 不存在 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-17 | AC-1、BR-4 | 同 TC-3 | 运行 `version`，检查完整标准输出 | 标准输出等于 `"版本: 0.1.0\n看板根目录: <tmp>/xx-board\n"`：恰好两行、冒号后一个半角空格、无多余空行；标准错误为空 | 自动化（QA，team-pm/tests/test_version.py） |
| TC-18 | AC-9 | 无 | 运行全量测试 `cd team-pm && python3 -m unittest discover -s tests` | 全部通过（含原有 47 条和新增用例），无失败、无错误 | 自动化（已有测试 + 本计划新增） |
| TC-19 | AC-9 | `TEAM_BOARD` 设为临时目录 | 依次运行 `board.py project list`、`board.py --help`，对比改动前后（基线提交上同样命令）的输出 | 原有子命令输出不变，`--help` 只多出 `version` 一项 | 手工 |

## 5. 回归范围

- 看板其他子命令（project / item / 事件 / 派单等）：改动会碰 `board.py` 的参数解析，可能还会在 `board_store.py` 加常量。跑全量测试（TC-18），并手工对比 `--help` 和 `project list` 输出（TC-19）。
- 看板根目录的解析（`board_store` 里取 `TEAM_BOARD` 的函数）：如果开发为了 `version` 改了这个函数（例如加 `abspath`），其他命令读写的目录也会跟着变；全量测试里依赖 `TEAM_BOARD` 的用例覆盖这部分。

## 6. 说明

- TC-15（相对路径 `TEAM_BOARD`）：需求 BR-3 要求“与其他命令实际使用的目录一致”且“输出绝对路径”，两者合起来理解为“按当前工作目录展开成绝对路径后打印”。其他命令按相对路径读写时，实际目录也正是“当前工作目录下的 rel-board”，两者不冲突。
- TC-14（`TEAM_BOARD` 为空字符串）：按 BR-3“设置了非空的 `TEAM_BOARD` 时用它”，空值按未设置处理。
- TC-8 在 scripts 副本上改常量，不改仓库里的业务代码；常量放在 `board.py` 还是 `board_store.py` 由开发定，测试单里按实际位置改。
- TC-9 把需求 AC-6“除该常量外没有别处硬编码版本号”限定在 `team-pm/scripts/` 下的代码；测试代码和文档里出现 `0.1.0`（作为期望值）不算。
- 没有建答疑单。
