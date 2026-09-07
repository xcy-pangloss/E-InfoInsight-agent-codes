#!/usr/bin/env bash
# 启动 e-InfoInsight 网页管理系统（后端 + 前端）
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "启动后端 (FastAPI :8643)..."
.venv/bin/uvicorn app:app --app-dir web/backend --host 127.0.0.1 --port 8643 &
BACKEND_PID=$!

echo "启动前端 (Vite :5174)..."
cd web/frontend
bun dev &
FRONTEND_PID=$!
cd "$ROOT"

echo
echo "=========================================="
echo "  e-InfoInsight 网页管理系统已启动"
echo "  前端: http://127.0.0.1:5174"
echo "  后端API文档: http://127.0.0.1:8643/docs"
echo "=========================================="
echo "按 Ctrl+C 停止"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
