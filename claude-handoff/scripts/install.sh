#!/bin/zsh
# 安装（或卸载）登记本机地址的 launchd 任务。
#   install.sh            安装并立即登记一次
#   install.sh uninstall  卸载，并删掉本机在 iCloud 云盘里的登记
set -eu

LABEL="com.claude-handoff.register"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$HOME/.claude/skills/claude-handoff/scripts/register.sh"
REG_FILE="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeHandoff/hosts/$(scutil --get LocalHostName).json"
DOMAIN="gui/$(id -u)"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  rm -f "$PLIST" "$REG_FILE"
  echo "已卸载登记任务"
  exit 0
fi

[[ -f "$SCRIPT" ]] || { echo "找不到 $SCRIPT，先把 claude-handoff 装到 ~/.claude/skills/"; exit 1; }
[[ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]] || { echo "本机没有开启 iCloud 云盘"; exit 1; }

mkdir -p "$HOME/Library/LaunchAgents"
# 网络变化时 /var/run/resolv.conf 会被重写，借此在换网络后立即重新登记
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$SCRIPT</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>WatchPaths</key><array><string>/var/run/resolv.conf</string></array>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardErrorPath</key><string>/tmp/$LABEL.err</string>
</dict>
</plist>
EOF

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
sleep 2
if [[ -f "$REG_FILE" ]]; then
  echo "已安装，本机登记："
  cat "$REG_FILE"
else
  echo "已安装，但还没看到登记文件，查看 /tmp/$LABEL.err"
  exit 1
fi
