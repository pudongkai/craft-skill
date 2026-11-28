"""项目交付：board.py 的 project merge 子命令。

把项目分支合并到目标分支并推送，再清理项目的 worktree、本地分支和远程分支。
目标分支在某个工作区里检出着就在那里合并（要求没有未提交的改动），否则临时建一个 worktree
合并完再删掉，不动用户当前的工作区。冲突时放弃合并，列出冲突文件。
推送失败时不清理，修好后重跑本命令即可（已合并过的再合并是空操作）。
清理只删已经合并进目标分支的东西：worktree 里有未提交的改动、远程分支有没合并的提交时跳过并说明。
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
    """合并、推送、清理，在项目卡记录里写一行，返回 (目标分支, 合并后的短哈希, 清理说明列表)。"""
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
    pushed = push_target(repo, target)
    notes = [pushed] + cleanup_project(repo, p.get("工作目录"), branch, target)
    doc = bs.load_project(name)
    doc.append_to_section("记录", f"- {bs.today()} 合并到 {target}（{commit}）；" + "；".join(notes))
    doc.save()
    return target, commit, notes


def merge_in_temp_worktree(repo, name, branch, target):
    tmp = os.path.join(repo, ".claude", "worktrees", f"{name}-merge-{target.replace('/', '-')}")
    code, out = run(["git", "-C", repo, "worktree", "add", tmp, target])
    if code != 0:
        raise bs.BoardError(f"建合并用的 worktree 失败：{out.strip()}")
    try:
        return merge_in(tmp, branch, target)
    finally:
        run(["git", "-C", repo, "worktree", "remove", "--force", tmp])


# ---------- 推送 ----------

def target_remote(repo, target):
    """推送目标分支用的远程：目标分支的上游远程 → 同名远程分支所在的远程 → 仓库唯一的远程。"""
    code, out = run(["git", "-C", repo, "config", f"branch.{target}.remote"], stdout_only=True)
    if code == 0 and out.strip():
        return out.strip()
    remote = remote_branch(repo, target)
    if remote:
        return remote.split("/", 1)[0]
    remotes = run(["git", "-C", repo, "remote"], stdout_only=True)[1].split()
    return remotes[0] if len(remotes) == 1 else None


def push_target(repo, target):
    """推送目标分支，返回一句说明；仓库没有远程时不推送，推送失败时报错（这时还没清理）。"""
    remote = target_remote(repo, target)
    if not remote:
        return "仓库没有可推送的远程，没有推送"
    code, out = run(["git", "-C", repo, "push", remote, target], timeout=300)
    if code != 0:
        raise bs.BoardError(f"已在本地合并到 {target}，推送到 {remote} 失败，没有清理：{out.strip()}")
    return f"已推送到 {remote}/{target}"


# ---------- 清理 ----------

def cleanup_project(repo, workdir, branch, target):
    """删项目 worktree、本地分支、远程分支，返回每一步的说明。"""
    notes = [remove_worktree(repo, workdir)]
    notes.append(delete_local_branch(repo, branch, target))
    note = delete_remote_branch(repo, branch, target)
    if note:
        notes.append(note)
    return notes


def remove_worktree(repo, workdir):
    if not workdir or not os.path.isdir(workdir):
        return "项目 worktree 已不存在"
    _, status = run(["git", "-C", workdir, "status", "--porcelain"], stdout_only=True)
    # .claude/ 下是角色会话的运行状态，不属于项目改动
    dirty = [line for line in status.splitlines() if not line[3:].startswith(".claude/")]
    if dirty:
        return f"项目 worktree {workdir} 有未提交的改动，没有删除"
    code, out = run(["git", "-C", repo, "worktree", "remove", "--force", workdir])
    if code != 0:
        return f"删除项目 worktree 失败：{out.strip()}"
    return "已删除项目 worktree"


def is_merged(repo, ref, target):
    code, _ = run(["git", "-C", repo, "merge-base", "--is-ancestor", ref, target])
    return code == 0


def delete_local_branch(repo, branch, target):
    if not branch_exists(repo, branch):
        return f"本地分支 {branch} 已不存在"
    if checked_out_at(repo, branch):
        return f"本地分支 {branch} 还检出在 {checked_out_at(repo, branch)}，没有删除"
    if not is_merged(repo, branch, target):
        return f"本地分支 {branch} 有没合并进 {target} 的提交，没有删除"
    # 已确认合并进目标分支；-d 只按当前 HEAD 判断，主工作区不在目标分支时会误拒
    code, out = run(["git", "-C", repo, "branch", "-D", branch])
    return f"已删除本地分支 {branch}" if code == 0 else f"删除本地分支 {branch} 失败：{out.strip()}"


def delete_remote_branch(repo, branch, target):
    """远程有同名项目分支时删掉；没有时返回 None。"""
    ref = remote_branch(repo, branch)
    if not ref:
        return None
    remote = ref.split("/", 1)[0]
    if not is_merged(repo, ref, target):
        return f"远程分支 {ref} 有没合并进 {target} 的提交，没有删除"
    sha = run(["git", "-C", repo, "rev-parse", ref], stdout_only=True)[1].strip()
    # 远程分支在本地看到之后又有人推了新提交时，lease 不匹配，推送会被拒绝，不会删掉别人的提交
    code, out = run(["git", "-C", repo, "push", f"--force-with-lease=refs/heads/{branch}:{sha}",
                     remote, f":refs/heads/{branch}"], timeout=300)
    return f"已删除远程分支 {ref}" if code == 0 else f"删除远程分支 {ref} 失败：{out.strip()}"
