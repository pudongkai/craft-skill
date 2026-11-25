"""项目交付后把项目分支合并到目标分支：board.py 的 project merge 子命令。

只在本地合并，不推送。目标分支在某个工作区里检出着就在那里合并（要求没有未提交的改动），
否则临时建一个 worktree 合并完再删掉，不动用户当前的工作区。冲突时放弃合并，列出冲突文件。
"""
import os

import board_store as bs
from board_dispatch import branch_exists, remote_branch, run


def target_branch(project_doc):
    return project_doc.get("目标分支") or "release"


def ensure_local_target(repo, target):
    """本地没有目标分支、只有远程分支时建本地跟踪分支；都没有时报错。"""
    if branch_exists(repo, target):
        return
    remote = remote_branch(repo, target)
    if not remote:
        raise bs.BoardError(f"仓库里没有 {target} 分支，先建好或用 project set <项目> 目标分支 <分支> 改目标")
    code, out = run(["git", "-C", repo, "branch", "--track", target, remote])
    if code != 0:
        raise bs.BoardError(f"建本地 {target} 分支失败：{out.strip()}")


def checked_out_at(repo, branch):
    """branch 当前检出在哪个工作区，没有检出时返回 None。"""
    _, out = run(["git", "-C", repo, "worktree", "list", "--porcelain"], stdout_only=True)
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}":
            return path
    return None


def merge_in(workdir, branch, target):
    """在 workdir 里把 branch 合并进当前分支，返回合并后的短哈希；冲突时放弃合并并报错。"""
    code, out = run(["git", "-C", workdir, "merge", "--no-ff", "-m",
                     f"Merge branch '{branch}' into {target}", branch])
    if code == 0:
        return run(["git", "-C", workdir, "rev-parse", "--short", "HEAD"], stdout_only=True)[1].strip()
    _, conflicts = run(["git", "-C", workdir, "diff", "--name-only", "--diff-filter=U"], stdout_only=True)
    run(["git", "-C", workdir, "merge", "--abort"])
    if conflicts.strip():
        raise bs.BoardError(f"合并 {branch} 到 {target} 有冲突，已放弃合并。冲突文件：\n{conflicts.strip()}")
    raise bs.BoardError(f"合并 {branch} 到 {target} 失败：{out.strip()}")


def merge_project(name):
    """把项目分支合并到目标分支，在项目卡记录里写一行，返回 (目标分支, 合并后的短哈希)。"""
    p = bs.load_project(name)
    repo, branch, target = p.get("仓库"), p.get("分支"), target_branch(p)
    ensure_local_target(repo, target)
    workdir = checked_out_at(repo, target)
    if workdir:
        _, dirty = run(["git", "-C", workdir, "status", "--porcelain", "--untracked-files=no"],
                       stdout_only=True)
        if dirty.strip():
            raise bs.BoardError(f"{target} 检出在 {workdir}，那里有未提交的改动，先处理掉再合并")
        commit = merge_in(workdir, branch, target)
    else:
        commit = merge_in_temp_worktree(repo, name, branch, target)
    doc = bs.load_project(name)
    doc.append_to_section("记录", f"- {bs.today()} 合并到 {target}（{commit}）")
    doc.save()
    return target, commit


def merge_in_temp_worktree(repo, name, branch, target):
    tmp = os.path.join(repo, ".claude", "worktrees", f"{name}-merge-{target.replace('/', '-')}")
    code, out = run(["git", "-C", repo, "worktree", "add", tmp, target])
    if code != 0:
        raise bs.BoardError(f"建合并用的 worktree 失败：{out.strip()}")
    try:
        return merge_in(tmp, branch, target)
    finally:
        run(["git", "-C", repo, "worktree", "remove", "--force", tmp])
