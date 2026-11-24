#!/bin/sh
# 把角色子 agent 的定义链接到 ~/.claude/agents/，新开的 Claude Code 会话才能派出它们。
set -e
src="$(cd "$(dirname "$0")/../agents" && pwd)"
mkdir -p "$HOME/.claude/agents"
for f in "$src"/*.md; do
  ln -sfn "$f" "$HOME/.claude/agents/$(basename "$f")"
  echo "已链接 $(basename "$f")"
done
