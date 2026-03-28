#!/bin/bash
# 将 Python 后端打包为单文件可执行程序，输出到 electron/backend/
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/../"

cd "$BACKEND_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] 未找到 uv，请先安装：https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "[1/3] 同步后端依赖..."
uv sync

echo "[2/3] 打包后端..."
uv run pyinstaller --onefile --name backend \
  --add-data "platforms:platforms" \
  --add-data "core:core" \
  --add-data "api:api" \
  --add-data "services:services" \
  --add-data "static:static" \
  main.py

echo "[3/3] 复制产物到 electron/backend/"
mkdir -p "$SCRIPT_DIR/backend"
cp dist/backend* "$SCRIPT_DIR/backend/"

echo "完成! 可执行文件: $SCRIPT_DIR/backend/backend"
