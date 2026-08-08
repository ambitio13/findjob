#!/usr/bin/env bash
#
# dev-logs.sh — 输出 BOSS 本地开发最近日志，便于排障和提交 bug 证据。
#
# 默认输出 backend / worker / frontend 各最近 200 行。可用 --tail N 调整。
#
# Usage:
#   ./scripts/dev-logs.sh
#   ./scripts/dev-logs.sh --tail 500
#   ./scripts/dev-logs.sh --grep "boss.bridge"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TAIL=200
GREP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tail)
      TAIL="$2"
      shift 2
      ;;
    --grep)
      GREP="$2"
      shift 2
      ;;
    -h|--help)
      echo "用法: $0 [--tail N] [--grep PATTERN]"
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 1
      ;;
  esac
done

cd "$REPO_ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "❌ docker 命令未找到" >&2
  exit 1
fi

print_logs() {
  local svc="$1"
  echo "==================== $svc (tail $TAIL) ===================="
  if [[ -n "$GREP" ]]; then
    docker compose logs --tail="$TAIL" "$svc" 2>&1 | grep -E "$GREP" || echo "(无匹配)"
  else
    docker compose logs --tail="$TAIL" "$svc" 2>&1 || echo "(服务不存在或无日志)"
  fi
  echo ""
}

print_logs backend
print_logs worker
print_logs frontend

echo "提示: 串一条 run 可用  ./scripts/dev-logs.sh --grep '<instruction_id|agent_run_id>'"
