#!/bin/bash
# 安裝兩個 launchd 排程：
#   1) pdf-download fetch  — 每週日 16:00 抓 11 本期刊摘要
#   2) pdf-download organize — 每天 03:00 自動把 _pdfs/ 處理進 KB 00-Raw/
#
# 用法：bash scripts/install_schedule.sh
# 移除：bash scripts/uninstall_schedule.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHAGENTS="$HOME/Library/LaunchAgents"

# ---------- 預檢：inbox 可寫 ----------
echo "🔍 預檢：測試 inbox 是否可寫..."
INBOX_ROOT=$("$PROJECT_DIR/venv/bin/python" -c "
import yaml, os
cfg = yaml.safe_load(open('$PROJECT_DIR/config.yaml'))
print(os.path.expanduser(cfg['inbox_root']))
" 2>/dev/null)

if [ -z "$INBOX_ROOT" ]; then
  echo "❌ 讀不到 config.yaml 的 inbox_root"
  exit 1
fi

TEST_FILE="$INBOX_ROOT/.pdf-download-write-test"
if mkdir -p "$INBOX_ROOT" 2>/dev/null && touch "$TEST_FILE" 2>/dev/null; then
  rm -f "$TEST_FILE"
  echo "✅ inbox 可寫: $INBOX_ROOT"
else
  echo ""
  echo "⚠️  inbox 無法寫入: $INBOX_ROOT"
  if [[ "$INBOX_ROOT" == *"CloudStorage"* ]]; then
    echo "請設定 系統設定 → 隱私與安全性 → 完整磁碟取用權 → 加入 Terminal"
  fi
  exit 1
fi
echo ""

# ---------- 安裝兩個 plist ----------
install_one() {
  local label=$1
  local plist_name="${label}.plist"
  local plist_src="$SCRIPT_DIR/$plist_name"
  local plist_dst="$LAUNCHAGENTS/$plist_name"

  echo "📋 安裝 $label..."
  mkdir -p "$LAUNCHAGENTS"
  cp -f "$plist_src" "$plist_dst"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist_dst"
  echo "   ✅ 已載入 $label"
}

install_one "com.lybrish.pdf-download"
install_one "com.lybrish.pdf-organize"
echo ""

# ---------- 確認 ----------
echo "📅 排程已就緒："
echo "   • fetch    每週日 16:00 → 抓 11 本期刊摘要"
echo "   • organize 每天 03:00   → _pdfs/ 改名搬進 KB 00-Raw/"
echo "                            （接你 KB 4:00 的 run_daily.py）"
echo ""
echo "📁 log:  ~/Library/Logs/pdf-download/"
echo ""
echo "想立刻測試？"
echo "   launchctl kickstart gui/\$(id -u)/com.lybrish.pdf-download"
echo "   launchctl kickstart gui/\$(id -u)/com.lybrish.pdf-organize"
echo ""
echo "想移除？bash $SCRIPT_DIR/uninstall_schedule.sh"
