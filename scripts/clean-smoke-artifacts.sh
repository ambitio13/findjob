#!/usr/bin/env bash
#
# clean-smoke-artifacts.sh — 清理 BOSS smoke 测试残留资源。
#
# 只清理固定前缀的 smoke/test 资源，绝不触碰生产 namespace 或无前缀数据。
#
# 清理范围:
#   - Redis: 队列键 job-search-agent-smoke-* （通过 arq namespace）
#   - DB:    UserProfile / AgentRun 表中 user_id 以 'smoke-' 开头的行
#
# 禁止: flushdb、宽泛 delete、无 WHERE 前缀约束的 SQL。
#
# Usage:
#   ./scripts/clean-smoke-artifacts.sh          # 交互确认
#   ./scripts/clean-smoke-artifacts.sh --yes    # 跳过确认
#
# Exit codes:
#   0 — 清理完成（或无可清理项）
#   1 — 参数错误 / 服务不可达 / 用户取消

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

ASSUME_YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y)
      ASSUME_YES=1
      shift
      ;;
    -h|--help)
      echo "用法: $0 [--yes]"
      echo "只清理 smoke- 前缀的 UserProfile/AgentRun 行与 job-search-agent-smoke-* Redis 键。"
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 1
      ;;
  esac
done

cd "$REPO_ROOT"

BACKEND_BASE_URL="${BACKEND_BASE_URL:-http://localhost:8000/api/v1}"
REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://app:app@localhost:5432/job_search_agent}"

# ---------------------------------------------------------------------------
# 安全前缀约束（硬编码，不接受外部覆盖以防止误删）
# ---------------------------------------------------------------------------
SMOKE_NS_PREFIX="job-search-agent-smoke-"
SMOKE_USER_PREFIX="smoke-"

echo "将清理以下资源（仅固定前缀）:"
echo "  - Redis 键: ${SMOKE_NS_PREFIX}*"
echo "  - DB 行:    user_id LIKE '${SMOKE_USER_PREFIX}%' (UserProfile, AgentRun)"
echo "  - 目标 Redis: $REDIS_URL"
echo "  - 目标 DB:    $DATABASE_URL"
echo ""

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "确认清理？(yes/no) " ans
  if [[ "$ans" != "yes" ]]; then
    echo "已取消。"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# 1. Redis: 删除 smoke namespace 队列键
# ---------------------------------------------------------------------------
echo "== Redis =="
if command -v redis-cli >/dev/null 2>&1; then
  redis_host="$(echo "$REDIS_URL" | sed -E 's|redis://([^:/]+).*|\1|')"
  redis_port="$(echo "$REDIS_URL" | sed -E 's|redis://[^:/]+:([0-9]+).*|\1|')"
  [[ "$redis_host" == "$REDIS_URL" ]] && redis_host="localhost"
  [[ "$redis_port" == "$REDIS_URL" ]] && redis_port="6379"
  redis_db="$(echo "$REDIS_URL" | sed -E 's|.*/([0-9]+)$|\1|')"
  [[ "$redis_db" == "$REDIS_URL" ]] && redis_db="0"

  keys="$(redis-cli -h "$redis_host" -p "$redis_port" -n "$redis_db" \
    --scan --pattern "${SMOKE_NS_PREFIX}*" 2>/dev/null || true)"
  if [[ -z "$keys" ]]; then
    echo "  (无 ${SMOKE_NS_PREFIX}* 键)"
  else
    echo "$keys" | while read -r k; do
      [[ -n "$k" ]] && redis-cli -h "$redis_host" -p "$redis_port" -n "$redis_db" DEL "$k" >/dev/null
      echo "  删除: $k"
    done
  fi
else
  echo "  ⚠ redis-cli 未安装，跳过 Redis 清理。下一步: brew install redis"
fi

# ---------------------------------------------------------------------------
# 2. DB: 删除 smoke- 前缀用户行
# ---------------------------------------------------------------------------
echo "== DB =="
if command -v psql >/dev/null 2>&1; then
  pg_url="$(echo "$DATABASE_URL" | sed -E 's|postgresql\+psycopg://|postgresql://|')"
  deleted="$(psql "$pg_url" -t -A -c \
    "DELETE FROM agent_runs WHERE user_id LIKE '${SMOKE_USER_PREFIX}%';" 2>/dev/null || echo "ERR")"
  echo "  agent_runs 删除行数: ${deleted:-0}"
  deleted="$(psql "$pg_url" -t -A -c \
    "DELETE FROM user_profiles WHERE id LIKE '${SMOKE_USER_PREFIX}%';" 2>/dev/null || echo "ERR")"
  echo "  user_profiles 删除行数: ${deleted:-0}"
else
  echo "  ⚠ psql 未安装，跳过 DB 清理。下一步: brew install libpq && brew link libpq"
fi

echo ""
echo "✅ smoke 残留清理完成。"
echo "   注: 生产 namespace (job-search-agent) 资源未触碰。"
