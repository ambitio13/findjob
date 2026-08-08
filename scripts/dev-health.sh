#!/usr/bin/env bash
#
# dev-health.sh — BOSS local development health check.
#
# Checks, in order: Docker Compose services up, backend /health (DB + Redis),
# frontend reachable, worker startup log evidence. Exits non-zero on first
# failure and prints a next-step hint.
#
# Usage:
#   ./scripts/dev-health.sh
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed (hint printed above)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND_BASE_URL="${BACKEND_BASE_URL:-http://localhost:8000/api/v1}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://localhost:5173}"

fail() {
  echo "❌ $1" >&2
  echo "   → 下一步: $2" >&2
  exit 1
}

ok() {
  echo "✓ $1"
}

# ---------------------------------------------------------------------------
# 1. Docker Compose services
# ---------------------------------------------------------------------------
echo "== 1/4 Docker Compose 服务 =="
cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  fail "docker 命令未找到" "安装 Docker Desktop 或 colima"
fi

services=(postgres redis backend worker frontend)
for svc in "${services[@]}"; do
  state="$(docker compose ps --format '{{.Service}} {{.State}}' "$svc" 2>/dev/null || true)"
  if [[ "$state" != *"$svc running"* && "$state" != *"$svc healthy"* ]]; then
    fail "服务 $svc 未运行" "docker compose up -d $svc"
  fi
done
ok "所有 compose 服务运行中"

# ---------------------------------------------------------------------------
# 2. Backend /health (DB + Redis)
# ---------------------------------------------------------------------------
echo "== 2/4 Backend /health =="
health_json="$(curl -fsS --max-time 5 "$BACKEND_BASE_URL/health" 2>/dev/null || true)"
if [[ -z "$health_json" ]]; then
  fail "backend /health 无响应" "docker compose logs --tail=50 backend"
fi

db_status="$(echo "$health_json" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("db",""))' 2>/dev/null || true)"
if [[ "$db_status" != "ok" ]]; then
  fail "backend DB 不健康: $db_status" "docker compose logs --tail=50 postgres backend"
fi

redis_status="$(echo "$health_json" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("redis",""))' 2>/dev/null || true)"
if [[ "$redis_status" != "ok" ]]; then
  fail "backend Redis 不健康: $redis_status" "docker compose logs --tail=50 redis backend"
fi
ok "backend /health: db=ok redis=ok"

# ---------------------------------------------------------------------------
# 3. Frontend reachable
# ---------------------------------------------------------------------------
echo "== 3/4 Frontend =="
frontend_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$FRONTEND_BASE_URL" 2>/dev/null || true)"
if [[ "$frontend_code" != "200" && "$frontend_code" != "304" ]]; then
  fail "前端无响应 (HTTP $frontend_code)" "docker compose logs --tail=50 frontend"
fi
ok "前端可达 ($FRONTEND_BASE_URL)"

# ---------------------------------------------------------------------------
# 4. Worker startup log evidence
# ---------------------------------------------------------------------------
echo "== 4/4 Worker 启动 =="
worker_log="$(docker compose logs --tail=30 worker 2>/dev/null || true)"
if ! echo "$worker_log" | grep -qi "arq\|worker.*start\|Booting"; then
  fail "worker 无启动日志" "docker compose logs --tail=100 worker"
fi
ok "worker 已启动"

echo ""
echo "✅ 所有健康检查通过。"
echo "   下一步: ./scripts/e2e-smoke.sh --skip-up  (跑 BOSS smoke)"
