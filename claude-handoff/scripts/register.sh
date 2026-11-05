#!/bin/zsh
# 把本机当前的地址登记到 iCloud 云盘，另一台 Mac 接力时按这里的记录找到本机。
# 由 launchd 在登录、网络变化时和每隔 5 分钟运行一次（见 install.sh）。
set -u

REG_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeHandoff/hosts"
mkdir -p "$REG_DIR" || exit 0

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

# 默认路由的接口排在最前，其余只取硬件网口，不登记虚拟机网桥这类地址
collect_ips() {
  local primary port ip seen=""
  primary=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
  for port in $primary $(networksetup -listallhardwareports | awk '/^Device:/{print $2}'); do
    [[ " $seen " == *" $port "* ]] && continue
    seen="$seen $port"
    ip=$(ipconfig getifaddr "$port" 2>/dev/null) || continue
    printf '"%s",' "$ip"
  done
}

name=$(scutil --get ComputerName)
host=$(scutil --get LocalHostName)
ips=$(collect_ips)
file="$REG_DIR/$host.json"
tmp="$REG_DIR/.$host.json.tmp"

cat > "$tmp" <<EOF
{
  "name": "$(json_escape "$name")",
  "host": "$(json_escape "$host")",
  "user": "$(json_escape "$USER")",
  "ips": [${ips%,}],
  "updated": $(date +%s)
}
EOF
mv -f "$tmp" "$file"
