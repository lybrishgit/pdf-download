#!/bin/bash
# 安裝 launchd 排程：每週日 16:00 自動跑 pdf-download fetch
#
# 用法：bash scripts/install_schedule.sh
#
# 移除排程請跑 scripts/uninstall_schedule.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_NAME="com.lybrish.pdf-download.plist"
PLIST_SRC="$SCRIPT_DIR/$PLIST_NAME"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

# ---------- 預檢：確認 inbox 可寫 ----------
echo "🔍 預檢：測試 inbox 是否可寫..."

INBOX_ROOT=$("$PROJECT_DIR/venv/bin/python" -c "
import yaml, os
cfg = yaml.safe_load(open('$PROJECT_DIR/config.yaml'))
print(os.path.expanduser(cfg['inbox_root']))
" 2>/dev/null)

if [ -z "$INBOX_ROOT" ]; then
  echo "❌ 讀不到 config.yaml 的 inbox_root，請檢查設定檔。"
  exit 1
fi

# 嘗試建立 inbox 資料夾
TEST_FILE="$INBOX_ROOT/.pdf-download-write-test"
if mkdir -p "$INBOX_ROOT" 2>/dev/null && touch "$TEST_FILE" 2>/dev/null; then
  rm -f "$TEST_FILE"
  echo "✅ inbox 可寫: $INBOX_ROOT"
else
  echo ""
  echo "⚠️  ⚠️  ⚠️  inbox 無法寫入: $INBOX_ROOT"
  echo ""
  if [[ "$INBOX_ROOT" == *"CloudStorage"* ]]; then
    echo "原因：macOS 的 TCC 預設不讓 Terminal/launchd 寫 CloudStorage（GDrive/iCloud）。"
    echo ""
    echo "兩個解法擇一："
    echo ""
    echo "  解法 A：給 Terminal 完整磁碟取用權"
    echo "    1. 系統設定 → 隱私與安全性 → 完整磁碟取用權"
    echo "    2. 加入 Terminal.app（或 iTerm/Warp）並打勾"
    echo "    3. 重開 Terminal 後再跑這個 install script"
    echo ""
    echo "  解法 B：把 inbox 改到一般資料夾，再用 GDrive 桌面 client 同步"
    echo "    1. 編輯 config.yaml，把 inbox_root 改成 '~/Documents/Journals-Inbox'"
    echo "    2. 在 Google Drive 桌面 client → 設定 → 從電腦同步資料夾"
    echo "    3. 加入 ~/Documents/Journals-Inbox"
    echo ""
  else
    echo "請檢查資料夾權限。"
  fi
  echo "預檢失敗，停止安裝。"
  exit 1
fi

echo ""
echo "📋 來源 plist:    $PLIST_SRC"
echo "📋 安裝到:        $PLIST_DST"

# 1. 複製 plist 到 LaunchAgents
mkdir -p "$HOME/Library/LaunchAgents"
cp -f "$PLIST_SRC" "$PLIST_DST"

# 2. 如果已經 load 過，先 bootout 再 bootstrap（避免 "service already loaded"）
launchctl bootout "gui/$(id -u)/com.lybrish.pdf-download" 2>/dev/null || true

# 3. bootstrap 進去 user agent domain
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

# 4. 確認狀態
echo ""
echo "✅ 已安裝排程！下次觸發時間："
launchctl print "gui/$(id -u)/com.lybrish.pdf-download" 2>/dev/null \
  | grep -E "next fire|state" || echo "(launchctl print 沒回應，但 bootstrap 成功)"

echo ""
echo "📅 排程：每週日 16:00 (Asia/Taipei)"
echo "📁 log:  ~/Library/Logs/pdf-download/"
echo ""
echo "想立刻測試一次？跑 launchctl kickstart gui/\$(id -u)/com.lybrish.pdf-download"
echo "想移除排程？跑 bash $SCRIPT_DIR/uninstall_schedule.sh"
