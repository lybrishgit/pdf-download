#!/bin/bash
# 移除 launchd 排程
#
# 用法：bash scripts/uninstall_schedule.sh

set -euo pipefail

PLIST_DST="$HOME/Library/LaunchAgents/com.lybrish.pdf-download.plist"

echo "🗑  移除 pdf-download 排程..."

# bootout 把 service 從 launchd 卸下
launchctl bootout "gui/$(id -u)/com.lybrish.pdf-download" 2>/dev/null || \
  echo "(原本就沒在跑，沒關係)"

# 刪掉 plist 檔
if [ -f "$PLIST_DST" ]; then
  rm -f "$PLIST_DST"
  echo "✅ 已刪除 $PLIST_DST"
else
  echo "(plist 檔已不存在)"
fi

echo ""
echo "排程已移除。手動跑 fetch 不受影響。"
echo "想重新安裝就跑 bash scripts/install_schedule.sh"
