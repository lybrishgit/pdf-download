#!/bin/bash
# pdf-download organize 自動執行腳本（給 launchd 排程用）
#
# 跟 PDF-Organize.command 不同：給排程用，不需要 Terminal 視窗
#
# 行為：
#   1. cd 到專案、啟用 venv
#   2. 跑 python -m pdf_download.cli organize
#   3. log 寫到 ~/Library/Logs/pdf-download/organize-auto-*.log
#   4. 看結果送 macOS 通知：
#      - _pdfs/ 是空的 → 靜音（每日跑會很煩）
#      - 有處理 → 通知
#      - 失敗 → 通知

set -uo pipefail

PROJECT_DIR="/Users/lybrish-mini/lybrish_claude/sandbox/pdf-download"
LOG_DIR="$HOME/Library/Logs/pdf-download"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/organize-auto-$(date +%Y-%m-%d_%H%M).log"

exec > >(tee -a "$LOG_FILE") 2>&1

echo "===== auto-organize 開始 $(date) ====="

notify() {
  osascript -e "display notification \"$2\" with title \"$1\"" 2>/dev/null || true
}

if [ ! -x "$VENV_PYTHON" ]; then
  notify "PDF Organize ❌" "venv 沒裝好"
  exit 1
fi

cd "$PROJECT_DIR" || {
  notify "PDF Organize ❌" "找不到專案目錄"
  exit 1
}

"$VENV_PYTHON" -m pdf_download.cli organize
exit_code=$?

# 排程跑：空就不通知（避免每天提醒）
if [ $exit_code -ne 0 ]; then
  notify "PDF Organize ❌ 失敗" "exit $exit_code，看 log: $(basename "$LOG_FILE")"
elif grep -q "沒有 PDF 要處理" "$LOG_FILE" 2>/dev/null; then
  echo "_pdfs/ 是空的，靜默跳過通知"
else
  summary=$(grep "📊 處理" "$LOG_FILE" 2>/dev/null | head -1)
  matched=$(echo "$summary" | grep -oE "成功 [0-9]+" | grep -oE "[0-9]+" | head -1)
  unmatched=$(echo "$summary" | grep -oE "失敗/略過 [0-9]+" | grep -oE "[0-9]+" | head -1)
  matched=${matched:-0}
  unmatched=${unmatched:-0}
  if [ "$unmatched" = "0" ]; then
    notify "PDF Organize ✓ 完成" "${matched} 篇進 KB 的 00-Raw/"
  else
    notify "PDF Organize ✓ 部分完成" "${matched} 篇進 KB · ${unmatched} 篇留在 _pdfs/"
  fi
fi

echo "===== 結束 $(date), exit=$exit_code ====="
exit $exit_code
