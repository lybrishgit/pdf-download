#!/bin/bash
# pdf-download 自動抓取腳本（給 launchd 排程用，也可手動跑）
#
# 流程：
#   1. cd 到專案目錄、啟用 venv
#   2. 跑 python -m pdf_download.cli fetch
#   3. log 寫到 ~/Library/Logs/pdf-download/YYYY-MM-DD_HHMM.log
#   4. 跑完用 macOS notification 顯示「N 篇已就緒、X 篇必讀」
#   5. 失敗時也用 notification 提示，並指向 log 位置

set -uo pipefail

# ---------- 路徑設定（絕對路徑，launchd 環境沒有 cwd 概念） ----------
PROJECT_DIR="/Users/lybrish-mini/lybrish_claude/sandbox/pdf-download"
LOG_DIR="$HOME/Library/Logs/pdf-download"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"

# launchd 預設 PATH 很短，補完整一點才能找到 osascript / 系統工具
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG="en_US.UTF-8"
export LC_ALL="en_US.UTF-8"

# ---------- 準備 log ----------
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M).log"

# 把 stdout/stderr 都導到 log（同時 echo 到原本的 stdout 給 launchd 抓）
exec > >(tee -a "$LOG_FILE") 2>&1

echo "===== pdf-download fetch 開始: $(date) ====="
echo "log: $LOG_FILE"

# ---------- 通知函式 ----------
notify() {
  local title="$1"
  local message="$2"
  local subtitle="${3:-}"

  # 用 osascript 送 macOS 原生通知；如果 notification permission 沒開會靜默失敗
  if [ -n "$subtitle" ]; then
    osascript -e "display notification \"$message\" with title \"$title\" subtitle \"$subtitle\"" 2>/dev/null || true
  else
    osascript -e "display notification \"$message\" with title \"$title\"" 2>/dev/null || true
  fi
}

# ---------- 跑 fetch ----------
cd "$PROJECT_DIR" || {
  notify "pdf-download ❌" "找不到專案目錄: $PROJECT_DIR"
  exit 1
}

if [ ! -x "$VENV_PYTHON" ]; then
  notify "pdf-download ❌" "venv 沒裝好" "請執行 pip install -r requirements.txt"
  exit 1
fi

# 跑 fetch；--force 不加，state file 自然會跳過已抓過的期
"$VENV_PYTHON" -m pdf_download.cli fetch
EXIT_CODE=$?

# ---------- 解析結果並通知 ----------
if [ $EXIT_CODE -ne 0 ]; then
  notify "pdf-download ❌ 失敗" "exit code $EXIT_CODE，請查看 log" "$(basename "$LOG_FILE")"
  echo "===== 失敗結束: $(date), exit=$EXIT_CODE ====="
  exit $EXIT_CODE
fi

# 找出 inbox 路徑（從 config.yaml 抓）
INBOX_ROOT=$("$VENV_PYTHON" -c "
import yaml, os
cfg = yaml.safe_load(open('$PROJECT_DIR/config.yaml'))
print(os.path.expanduser(cfg['inbox_root']))
" 2>/dev/null)

TODAY=$(date +%Y-%m-%d)
LATEST_DIR="$INBOX_ROOT/$TODAY"

if [ ! -d "$LATEST_DIR" ]; then
  # 可能是已抓過全部的期、沒新東西
  notify "pdf-download ✓ 無新內容" "本週沒有新一期可抓" "已抓的期不會重複"
  echo "===== 完成（沒新內容）: $(date) ====="
  exit 0
fi

# 統計：篇數、必讀篇數、期刊數
TOTAL_ARTICLES=$(grep -hc "^## [0-9]" "$LATEST_DIR"/*.md 2>/dev/null | awk '{s+=$1}END{print s}')
MUST_READ=$(grep -h "AI 評析 — " "$LATEST_DIR"/*.md 2>/dev/null | grep -c "必讀" | tr -d ' ')
JOURNAL_COUNT=$(ls "$LATEST_DIR"/*.md 2>/dev/null | grep -v "INDEX.md" | wc -l | tr -d ' ')

# 預設值（當 grep 失敗時）
TOTAL_ARTICLES="${TOTAL_ARTICLES:-0}"
MUST_READ="${MUST_READ:-0}"
JOURNAL_COUNT="${JOURNAL_COUNT:-0}"

MSG="${TOTAL_ARTICLES} 篇文章；其中 ${MUST_READ} 篇必讀 ⭐⭐⭐⭐+"
SUB="${JOURNAL_COUNT} 個期刊 · 點擊在 Finder 開啟"

notify "pdf-download ✓ 本週摘要已就緒" "$MSG" "$SUB"
echo "===== 完成: $(date) ====="
echo "篇數=$TOTAL_ARTICLES, 必讀=$MUST_READ, 期刊=$JOURNAL_COUNT"
echo "輸出: $LATEST_DIR"

exit 0
