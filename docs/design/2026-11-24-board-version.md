# board.py version 子命令技术方案

开发状态: DONE
方案评审: 通过（2026-11-24）
需求来源: docs/prd/board-version.md
作者: 开发（团队角色）　　日期: 2026-11-24

## 1. 背景与目标

看板脚本 `team-pm/scripts/board.py` 没有版本号，也不能直接看到当前生效的看板根目录。目标：运行 `board.py version` 输出两行——脚本版本号和看板根目录的绝对路径，且不对看板产生任何读写副作用。验收标准见需求文档第 4 节 AC-1～AC-9。

## 2. 范围

**本次要做：**
- `board.py` 新增模块级常量 `VERSION = "0.1.0"`。
- 新增 `version` 子命令，按需求 BR-4 输出两行。
- `version` 不调用 `ensure_board()`，不建目录、不写文件、不写事件。
- 在 `team-pm/tests/test_board.py` 补对应测试。

**本次不做：**（同需求第 5 节）
- 不加 `--version` / `-V` 全局参数。
- 不做 JSON 等机器可读输出。
- 不打印 Python 版本、git 提交号、配置内容等其他信息。
- 不做版本检查、升级提示、数据格式迁移。
- 不给其他脚本（`board_dispatch.py`、`worker_guard.py`、其他 skill）加版本号。
- 不改 `team-pm/SKILL.md`、`references/protocol.md`：两处都没有子命令清单，`--help` 已能看到新命令。

## 3. 现状分析

- 入口 `team-pm/scripts/board.py`：
  - `build_parser()`（约 187 行）用 `sub.add_parser(...)` 注册 `project`、`item`、`ready`、`status`、`events`、`notify`；`board_dispatch.register(sub, COMMANDS)` 再注册 `dispatch`、`reap`、`wait`。
  - `COMMANDS` 字典（约 286 行）把子命令名映射到处理函数。
  - `main()`（约 290 行）解析参数后**无条件**先调 `bs.ensure_board()`，再调处理函数。`ensure_board()` 会创建 `<根目录>/projects/` 并在缺少时写 `config.json`——这和需求 BR-5（只读）冲突，是本次唯一需要改动流程的地方。
- `team-pm/scripts/board_store.py`：
  - `board_root()`（41 行）：`os.path.expanduser(os.environ.get("TEAM_BOARD") or "~/.claude/team")`。空字符串的 `TEAM_BOARD` 会落到默认值，`~` 会展开，与 BR-3 一致；但相对路径（如 `TEAM_BOARD=board`）原样返回，不是绝对路径。
- 测试 `team-pm/tests/test_board.py`：`BoardTestCase` 在 `setUp` 里把 `TEAM_BOARD` 指向临时目录下尚不存在的 `board` 子目录，`run_cli(*args)` 调 `board.main()` 并捕获标准输出。运行方式 `python3 -m unittest discover -s team-pm/tests`，当前 47 个用例全部通过。
- 仓库里目前没有任何地方出现 `0.1.0`。

### 可复用的已有代码

| 功能 | 已有实现（文件:类/方法） | 用在哪些阶段 |
| --- | --- | --- |
| 计算看板根目录（`TEAM_BOARD` / 默认值 / 展开 `~`） | `team-pm/scripts/board_store.py:board_root` | 阶段1 |
| 测试夹具：临时看板目录、调用 CLI 捕获输出 | `team-pm/tests/test_board.py:BoardTestCase`、`run_cli` | 阶段1 |

## 4. 方案设计

### 4.1 整体思路

只改 `board.py` 一个文件：加常量、加子命令、在 `main()` 里让 `version` 跳过 `ensure_board()`。根目录直接复用 `bs.board_root()`，外面再套一层 `os.path.abspath`，保证输出永远是绝对路径，且和其他命令实际读写的是同一个目录（其他命令用相对路径时也是相对当前目录解析）。

### 4.2 数据模型

无。

### 4.3 接口设计

命令行：

```
board.py version
```

- 无位置参数、无选项；多给参数由 argparse 报错（退出码 2，标准错误打印用法）。
- `--help` 子命令列表里显示：`version  打印脚本版本号和看板根目录`。
- 标准输出固定两行：

```
版本: 0.1.0
看板根目录: /Users/xxx/.claude/team
```

- 退出码 0；不论根目录是否存在。

### 4.4 关键流程

代码草图（`board.py`）：

```python
VERSION = "0.1.0"
# 不需要看板目录存在的子命令，main() 不为它们创建看板
NO_BOARD_COMMANDS = {"version"}

def cmd_version(a):
    print(f"版本: {VERSION}")
    print(f"看板根目录: {os.path.abspath(bs.board_root())}")

# build_parser() 里
sub.add_parser("version", help="打印脚本版本号和看板根目录")

# COMMANDS 里加 "version": cmd_version

# main() 里
if a.cmd not in NO_BOARD_COMMANDS:
    bs.ensure_board()
COMMANDS[a.cmd](a)
```

- `VERSION` 放在 `board.py` 顶部、`PRIORITY_ORDER` 旁边：版本号描述的是命令行脚本本身，放在入口文件里最容易找；`board_store.py` 是存储层，不放这里。
- 用集合 `NO_BOARD_COMMANDS` 而不是在 `main()` 里写 `!= "version"`：意图更清楚，以后再有只读诊断命令直接加进去。
- `cmd_version` 不读 `config.json`、不加锁、不调用 `append_event`。
- 路径用 `os.path.abspath`，不用 `os.path.realpath`：`realpath` 会解析软链接（macOS 的 `/tmp` 会变成 `/private/tmp`），输出就和使用者设的 `TEAM_BOARD` 对不上了（AC-2）。

### 4.5 公共逻辑

无（根目录计算复用 `board_root()`）。

### 4.6 备选方案

- **用 argparse 的 `action="version"`**：只能做全局 `--version`，需求明确只要子命令，且要两行自定义输出，不采用。
- **把 `abspath` 加进 `board_root()`**：会改变所有命令的路径表示（目前相对路径在其他命令里是原样用的），超出本次范围；在 `cmd_version` 里单独套一层即可，行为上是同一个目录。
- **版本常量放 `board_store.py`**：需求允许，但存储层不该关心脚本版本，不采用。

## 5. 兼容性与上线

- 只新增子命令，对现有子命令无影响；`main()` 对其他命令的流程不变。
- 回滚：撤销本次提交即可，没有数据变更。
- 已安装的 skill 是 `~/.claude/skills/team-pm -> /Users/dongkaipu/workspace/craft-skill/team-pm`（主检出），本分支合并到主检出之前，PM 实际运行的 `board.py` 不会有 `version`；验收时要在本 worktree 里运行 `team-pm/scripts/board.py`。

## 6. 风险

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 漏掉跳过 `ensure_board()`，`version` 建出看板目录 | 违反 BR-5，AC-4/AC-5 不通过 | 测试检查运行后根目录仍不存在 |
| 测试用 `$HOME` 下的真实看板目录，或改了 `HOME` 没恢复 | 污染使用者的看板；后面依赖 `HOME` 的用例（`git init`、`expanduser`）结果随执行顺序变化 | AC-1/AC-3 用例用 `unittest.mock.patch.dict(os.environ, ...)` 包住整个用例，在里面设 `HOME`、删 `TEAM_BOARD`，退出时自动恢复 |

## 7. 测试方案

在 `team-pm/tests/test_board.py` 新增 `VersionTest(BoardTestCase)`，用 `run_cli` 调用。改 `HOME` / 删 `TEAM_BOARD` 的用例一律用 `unittest.mock.patch.dict(os.environ, ...)` 包住，不直接改 `os.environ`：

| 用例 | 覆盖 |
| --- | --- |
| `board.VERSION` 符合 `^\d+\.\d+\.\d+$` | BR-2 |
| 未设 `TEAM_BOARD`、`HOME` 指向临时目录：输出恰好两行，第一行 `版本: {board.VERSION}`，第二行 `看板根目录: <HOME>/.claude/team` | AC-1 |
| `TEAM_BOARD=<临时目录>/x-board`（不存在）：第二行是该路径，运行后目录仍不存在 | AC-2、AC-4 |
| `TEAM_BOARD="~/my-board"`、`HOME` 指向临时目录：第二行为 `<HOME>/my-board`，不含 `~` | AC-3 |
| 已建好看板（先跑一次 `project new` 产生事件）：运行 `version` 前后根目录下文件列表（`os.walk`）和 `events.jsonl` 内容相同 | AC-5 |
| 临时把 `board.VERSION` 改成 `0.2.0`（`unittest.mock.patch.object`）：第一行变为 `版本: 0.2.0` | AC-6（前半） |
| `board.main(["version", "extra"])` 抛 `SystemExit`，code 为 2；用 `redirect_stderr` 捕获的标准错误含 `usage` 和 `unrecognized arguments` | AC-8 |
| `run_cli("--help")` 抛 `SystemExit(0)`，标准输出含 `version` 和 `打印脚本版本号和看板根目录`（走 `main()`，包含 `board_dispatch.register` 之后的完整子命令列表） | AC-7 |

AC-6 后半（代码里除常量外没有硬编码 `0.1.0`）用 `grep -rn "0\.1\.0" team-pm/` 检查，只应命中 `VERSION` 一行；因此测试里不写 `0.1.0` 字面量，值本身由这条 grep 检查确认。AC-9 由全量测试覆盖。

手工检查：`python3 team-pm/scripts/board.py version`、`TEAM_BOARD=/tmp/xx-board python3 team-pm/scripts/board.py version; ls /tmp/xx-board`（应不存在）、`python3 team-pm/scripts/board.py --help`。

## 8. 开发计划

- [x] 阶段1：version 子命令及测试
  - 涉及模块：team-pm/scripts/board.py、team-pm/tests/test_board.py
  - 依赖：无
  - 内容：按 4.4 加 `VERSION` 常量、`NO_BOARD_COMMANDS`、`cmd_version`、子命令注册和 `main()` 跳过 `ensure_board()`；按第 7 节新增 `VersionTest` 用例。
  - 完成标准：`python3 -m unittest discover -s team-pm/tests` 全部通过（含新增用例）；`grep -rn "0\.1\.0" team-pm/` 只命中 `VERSION = "0.1.0"` 一行；`d=$(mktemp -d)/nb; TEAM_BOARD=$d python3 team-pm/scripts/board.py version && test ! -e "$d"` 输出两行且退出码 0。
  - 验证记录：`python3 -m unittest discover -s team-pm/tests` 56 个用例全部通过（新增 `VersionTest` 9 个，含代码审查后补的“已建看板但没有事件”用例）；`grep -rn "0\.1\.0" team-pm/` 只命中 `team-pm/scripts/board.py:11:VERSION = "0.1.0"`；`TEAM_BOARD=<临时目录>/nb board.py version` 输出两行、退出码 0、目录未创建；`board.py --help` 列出 `version  打印脚本版本号和看板根目录`。AC-5 用例里 `project new` 不写事件，改为建项目后再调 `bs.append_event` 产生 `events.jsonl`。

## 9. 待确认问题

无
