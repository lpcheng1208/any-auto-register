#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/.backend.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "[INFO] 未发现当前项目记录的后端进程"
  exit 0
fi

IFS=$'\t' read -r PID ENTRYPOINT_PATH < "$PID_FILE" || true
PID="${PID:-}"
ENTRYPOINT_PATH="${ENTRYPOINT_PATH:-$ROOT_DIR/main.py}"
if [[ -z "$PID" ]]; then
  rm -f "$PID_FILE"
  echo "[INFO] PID 文件为空，已清理"
  exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "[INFO] 后端进程已退出，已清理 PID 文件"
  exit 0
fi

COMMAND_LINE="$(ps -o command= -p "$PID" 2>/dev/null || true)"
if [[ "$COMMAND_LINE" != *"$ENTRYPOINT_PATH"* ]]; then
  rm -f "$PID_FILE"
  echo "[INFO] PID 文件已过期，已清理"
  exit 0
fi

echo "[INFO] 准备停止后端 PID: $PID"
kill "$PID" 2>/dev/null || true

for _ in {1..20}; do
  if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "[INFO] 停止完成"
    exit 0
  fi
  sleep 0.25
done

echo "[WARN] PID=$PID 未在预期时间退出，改为强制停止"
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "[INFO] 停止完成"
