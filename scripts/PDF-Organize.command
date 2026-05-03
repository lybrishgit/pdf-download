#!/bin/bash
# PDF-Organize.command — 雙擊執行 organize 的 Terminal 啟動檔
#
# 為什麼用 .command 而非 .app：
# .app 是獨立 binary，要單獨給 FDA。Terminal 已經有 FDA 了，
# .command 透過 Terminal 跑就能直接存取 GDrive，省一次 TCC 設定。
#
# 雙擊行為：
#   1. macOS 開啟 Terminal、執行此 script
#   2. 跑 organize、顯示結果在 Terminal
#   3. 跑完按任意鍵關閉視窗（也會送 macOS 通知）

# Apple Silicon arch 防呆
if [ "$(uname -m)" = "x86_64" ] && [ -x /usr/bin/arch ]; then
  exec arch -arm64 /bin/bash "$0" "$@"
fi

# 切到專案目錄（用絕對路徑，避免被雙擊時 cwd 不對）
PROJECT_DIR="/Users/lybrish-mini/lybrish_claude/projects/pdf-download"
cd "$PROJECT_DIR" || { echo "找不到 $PROJECT_DIR"; sleep 3; exit 1; }

source venv/bin/activate

# 同時寫到 log 檔，方便事後檢查
LOG_DIR="$HOME/Library/Logs/pdf-download"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/organize-$(date +%Y-%m-%d_%H%M).log"

clear
echo "════════════════════════════════════════════════"
echo "   PDF Organize — 掃 _pdfs/ → 改名搬進 KB"
echo "════════════════════════════════════════════════"
echo ""
echo "log: $LOG_FILE"
echo ""

python -m pdf_download.cli organize 2>&1 | tee "$LOG_FILE"
exit_code=${PIPESTATUS[0]}

echo ""
echo "════════════════════════════════════════════════"

# 送 macOS 通知（依結果決定訊息）
notify() {
  osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
}

if [ $exit_code -ne 0 ]; then
  notify "PDF Organize ❌" "失敗，請看上面 Terminal 輸出或 log"
elif grep -q "沒有 PDF 要處理" "$LOG_FILE" 2>/dev/null; then
  notify "PDF Organize ✓" "_pdfs/ 是空的，沒東西要處理"
else
  summary=$(grep "📊 處理" "$LOG_FILE" 2>/dev/null | head -1)
  matched=$(echo "$summary" | grep -oE "成功 [0-9]+" | grep -oE "[0-9]+" | head -1)
  unmatched=$(echo "$summary" | grep -oE "失敗/略過 [0-9]+" | grep -oE "[0-9]+" | head -1)
  matched=${matched:-0}
  unmatched=${unmatched:-0}
  if [ "$unmatched" = "0" ]; then
    notify "PDF Organize ✓ 完成" "${matched} 篇全部進 KB 的 00-Raw/"
  else
    notify "PDF Organize ✓ 部分完成" "${matched} 篇進 KB · ${unmatched} 篇留在 _pdfs/"
  fi
fi

echo ""
read -n 1 -p "  按任意鍵關閉視窗..."
echo ""
# 跑完自動關 Terminal 視窗（背景非阻塞）
osascript -e 'tell application "Terminal" to close (every window whose name contains "PDF-Organize")' 2>/dev/null &
exit $exit_code
