#!/bin/bash
# 移除兩個 launchd 排程
#
# 用法：bash scripts/uninstall_schedule.sh

set -euo pipefail

LAUNCHAGENTS="$HOME/Library/LaunchAgents"

remove_one() {
  local label=$1
  local plist_dst="$LAUNCHAGENTS/${label}.plist"

  echo "🗑  移除 $label..."
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || \
    echo "   (原本就沒在跑)"
  if [ -f "$plist_dst" ]; then
    rm -f "$plist_dst"
    echo "   ✅ 已刪除 $plist_dst"
  fi
}

remove_one "com.lybrish.pdf-download"
remove_one "com.lybrish.pdf-organize"

echo ""
echo "排程都移除了。手動指令跟 PDF-Organize.command 不受影響。"
echo "想重新裝就跑 bash scripts/install_schedule.sh"
