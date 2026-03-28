#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/.backend.pid"
ENTRYPOINT_PATH="$ROOT_DIR/main.py"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
RESTART_EXISTING="${RESTART_EXISTING:-1}"

cd "$ROOT_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] 未找到 uv，请先安装：https://docs.astral.sh/uv/" >&2
  exit 1
fi

DISPLAY_HOST="$HOST"
if [[ "$HOST" == "0.0.0.0" ]]; then
  DISPLAY_HOST="localhost"
fi

echo "[INFO] 项目目录: $ROOT_DIR"
echo "[INFO] 使用 uv 环境启动后端"
echo "[INFO] 启动地址: http://$DISPLAY_HOST:$PORT"
echo "[INFO] 按 Ctrl+C 可停止服务"

if [[ "$RESTART_EXISTING" == "1" ]]; then
  echo "[INFO] 启动前先清理当前项目记录的后端进程"
  "$ROOT_DIR/stop_backend.sh"
fi

cleanup() {
  rm -f "$PID_FILE"
}

export HOST PORT APP_RUNTIME_HINT="uv run"
trap cleanup EXIT
uv run python "$ENTRYPOINT_PATH" &
BACKEND_PID=$!
printf '%s\t%s\n' "$BACKEND_PID" "$ENTRYPOINT_PATH" > "$PID_FILE"
wait "$BACKEND_PID"
