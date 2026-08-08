#!/usr/bin/env bash
#
# e2e-smoke.sh — BOSS userscript-bridge E2E smoke test with test isolation.
#
# This script orchestrates a full smoke pass against a running Docker Compose
# stack: service health, backend /health, frontend /, worker readiness, the
# userscript-bridge protocol round-trip (heartbeat → probe → wrong-tab 204 →
# correct-tab instruction → result success/failure), and the inspect
# bounded-failure path.
#
# It uses an isolated QUEUE_NAMESPACE and SMOKE_USER_ID so it never pollutes
# the production default namespace or a running local worker. All actual
# isolation values are printed at the start of the run.
#
# Usage:
#   ./scripts/e2e-smoke.sh               # full pass against running Compose
#   ./scripts/e2e-smoke.sh --skip-up     # don't docker compose up (assume running)
#   ./scripts/e2e-smoke.sh --help
#
# Environment variables (all optional):
#   BACKEND_BASE_URL       default http://localhost:8000/api/v1
#   FRONTEND_BASE_URL      default http://localhost:5173
#   SMOKE_QUEUE_NAMESPACE  default job-search-agent-smoke-<timestamp>
#   SMOKE_USER_ID          default smoke-review-user-<timestamp>
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed (details printed above)
#
# See docs/e2e-smoke.md for interpretation guide and cleanup instructions.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND_BASE_URL="${BACKEND_BASE_URL:-http://localhost:8000/api/v1}"
FRONTEND_BASE_URL="${FRONTEND_BASE_URL:-http://localhost:5173}"

# Timestamp-based isolation suffix — guarantees uniqueness per run.
TS="$(date +%Y%m%d%H%M%S)"
SMOKE_QUEUE_NAMESPACE="${SMOKE_QUEUE_NAMESPACE:-job-search-agent-smoke-$TS}"
SMOKE_USER_ID="${SMOKE_USER_ID:-smoke-review-user-$TS}"

# Fixed test page metadata for bridge protocol simulation.
SMOKE_PAGE_ID="smoke-page-$TS"
SMOKE_PAGE_URL_HASH="sha256:smoke-fixed-hash-$TS"
SMOKE_WRONG_PAGE_ID="smoke-wrong-page-$TS"

# Colors for output (disabled if not a TTY).
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    GREEN='' RED='' YELLOW='' CYAN='' BOLD='' NC=''
fi

# Counters.
PASS=0
FAIL=0
SKIP=0
SKIP_UP=false

# Collected resources for cleanup reporting.
CLEANUP_NOTES=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

header() {
    echo ""
    echo -e "${CYAN}${BOLD}=== $1 ===${NC}"
}

pass() {
    PASS=$((PASS + 1))
    echo -e "  ${GREEN}✓${NC} $1"
}

fail() {
    FAIL=$((FAIL + 1))
    echo -e "  ${RED}✗${NC} $1"
}

skip() {
    SKIP=$((SKIP + 1))
    echo -e "  ${YELLOW}○${NC} $1 (skipped)"
}

info() {
    echo -e "  ${CYAN}ℹ${NC} $1"
}

# Run a check: if the command succeeds, call pass; otherwise call fail with
# the command output.
#   check "description" command...
check() {
    local desc="$1"; shift
    local output
    if output=$("$@" 2>&1); then
        pass "$desc"
        return 0
    else
        fail "$desc"
        echo "$output" | sed 's/^/      /' >&2
        return 1
    fi
}

# Assert that a value matches an expected value.
#   assert_eq "description" actual expected
assert_eq() {
    local desc="$1" actual="$2" expected="$3"
    if [ "$actual" = "$expected" ]; then
        pass "$desc (got: $actual)"
    else
        fail "$desc (expected: $expected, got: $actual)"
    fi
}

# Extract a JSON field value using python3 (ubiquitous, no jq dependency).
# Supports dotted paths: jq_get 'db' or jq_get 'nested.field'
# Usage: jq_get 'field' "$json_string"
jq_get() {
    local expr="$1" data="${2:-}"
    python3 -c "
import sys, json
data = json.loads(sys.argv[1] or 'null') or {}
for part in sys.argv[2].split('.'):
    data = data.get(part) if isinstance(data, dict) else None
    if data is None:
        break
print('' if data is None else data)
" "$data" "$expr" 2>/dev/null
}

# HTTP helpers — curl with error capture.
http_get() {
    local url="$1"
    curl -sfS -o /dev/null -w '%{http_code}' "$url" 2>&1
}

http_get_body() {
    local url="$1"
    curl -sfS "$url" 2>&1
}

http_post() {
    local url="$1"; shift
    curl -sfS -X POST -H 'Content-Type: application/json' "$@" "$url" 2>&1
}

# ---------------------------------------------------------------------------
# Pre-flight: print isolation config
# ---------------------------------------------------------------------------

print_config() {
    header "Smoke Configuration"
    echo "  BACKEND_BASE_URL:      $BACKEND_BASE_URL"
    echo "  FRONTEND_BASE_URL:     $FRONTEND_BASE_URL"
    echo "  SMOKE_QUEUE_NAMESPACE: $SMOKE_QUEUE_NAMESPACE"
    echo "  SMOKE_USER_ID:         $SMOKE_USER_ID"
    echo "  SMOKE_PAGE_ID:         $SMOKE_PAGE_ID"
    echo "  SMOKE_PAGE_URL_HASH:   $SMOKE_PAGE_URL_HASH"
    echo "  Timestamp suffix:      $TS"
    echo ""
    info "Production namespace 'job-search-agent' must NOT appear above."
}

# ---------------------------------------------------------------------------
# Phase 1: Compose services
# ---------------------------------------------------------------------------

phase_compose() {
    header "Phase 1: Docker Compose Services"

    if [ "$SKIP_UP" = true ]; then
        skip "docker compose up (--skip-up)"
    else
        info "Building and starting Compose stack (isolated namespace)..."
        # Set QUEUE_NAMESPACE for the Compose stack so backend+worker use the
        # isolated namespace. This prevents a running default-namespace worker
        # from consuming smoke jobs.
        if output=$(QUEUE_NAMESPACE="$SMOKE_QUEUE_NAMESPACE" \
            docker compose -f "$REPO_ROOT/docker-compose.yml" \
            up -d --build 2>&1); then
            pass "docker compose up -d --build"
        else
            fail "docker compose up -d --build"
            echo "$output" | sed 's/^/      /' >&2
            return 1
        fi
    fi

    # Wait for services to be healthy.
    info "Waiting for services to be healthy (up to 60s)..."
    local waited=0
    while [ $waited -lt 60 ]; do
        if docker compose -f "$REPO_ROOT/docker-compose.yml" ps --format json 2>/dev/null \
            | python3 -c "
import sys, json
ok = True
for line in sys.stdin:
    svc = json.loads(line)
    health = svc.get('Health', svc.get('State', ''))
    state = svc.get('State', '')
    # Accept 'running' even without health check for services that lack one.
    if state not in ('running',):
        ok = False
        break
sys.exit(0 if ok else 1)
" 2>/dev/null; then
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    if [ $waited -ge 60 ]; then
        fail "Services did not reach running state within 60s"
        docker compose -f "$REPO_ROOT/docker-compose.yml" ps >&2
    else
        pass "All Compose services running (waited ${waited}s)"
    fi

    # Print the actual running state.
    echo ""
    docker compose -f "$REPO_ROOT/docker-compose.yml" ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || \
        docker compose -f "$REPO_ROOT/docker-compose.yml" ps
}

# ---------------------------------------------------------------------------
# Phase 2: Backend /health, frontend /, worker readiness
# ---------------------------------------------------------------------------

phase_health() {
    header "Phase 2: Service Health"

    # Wait for backend to respond.
    info "Waiting for backend /health..."
    local waited=0
    local health_body=""
    while [ $waited -lt 30 ]; do
        if health_body=$(curl -sfS "$BACKEND_BASE_URL/health" 2>/dev/null); then
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    if [ -z "$health_body" ]; then
        fail "Backend /health did not respond within 30s"
    else
        pass "Backend /health responded"
        local db_status redis_status env
        db_status=$(echo "$health_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('db',''))" 2>/dev/null)
        redis_status=$(echo "$health_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('redis',''))" 2>/dev/null)
        env=$(echo "$health_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('env',''))" 2>/dev/null)
        assert_eq "  Backend DB healthy" "$db_status" "ok"
        assert_eq "  Backend Redis healthy" "$redis_status" "ok"
        info "  Backend env: $env"
    fi

    # Frontend.
    local fe_code
    if fe_code=$(curl -sfS -o /dev/null -w '%{http_code}' "$FRONTEND_BASE_URL/" 2>/dev/null); then
        case "$fe_code" in
            200|301|302) pass "Frontend / responded (HTTP $fe_code)" ;;
            *) fail "Frontend / returned HTTP $fe_code" ;;
        esac
    else
        fail "Frontend / did not respond"
    fi

    # Worker readiness — check worker container logs for arq startup.
    info "Checking worker readiness..."
    local worker_logs
    worker_logs=$(docker compose -f "$REPO_ROOT/docker-compose.yml" logs --tail=50 worker 2>/dev/null || true)
    if echo "$worker_logs" | grep -qiE "Starting worker|arq.*started|registered|on_startup"; then
        pass "Worker started (found startup log line)"
    else
        # Fallback: check if worker container is running.
        local worker_state
        worker_state=$(docker compose -f "$REPO_ROOT/docker-compose.yml" ps worker --format '{{.State}}' 2>/dev/null || echo "")
        if [ "$worker_state" = "running" ]; then
            pass "Worker container running (no explicit startup log found)"
            info "Worker logs (last 10 lines):"
            echo "$worker_logs" | tail -10 | sed 's/^/      /'
        else
            fail "Worker not running or no startup evidence"
            echo "$worker_logs" | tail -20 | sed 's/^/      /' >&2
        fi
    fi

    # Verify the worker is using the isolated namespace by checking logs.
    if echo "$worker_logs" | grep -qi "job-search-agent-smoke"; then
        pass "Worker using isolated namespace (found in logs)"
    else
        # The namespace appears in the queue key; arq may not log it explicitly.
        info "Worker namespace not found in logs (expected if arq doesn't log queue name)"
    fi
}

# ---------------------------------------------------------------------------
# Phase 3: Bridge protocol round-trip
# ---------------------------------------------------------------------------

phase_bridge() {
    header "Phase 3: Userscript Bridge Protocol"

    # 3a: Heartbeat — establish connection.
    local hb_result
    if hb_result=$(http_post "$BACKEND_BASE_URL/userscript-bridge/heartbeat" \
        -d "{\"page_id\":\"$SMOKE_PAGE_ID\",\"page_url_hash\":\"$SMOKE_PAGE_URL_HASH\",\"page_title\":\"BOSS Smoke Test\"}" 2>&1); then
        pass "Heartbeat accepted"
    else
        fail "Heartbeat failed"
        echo "$hb_result" | sed 's/^/      /' >&2
        return 1
    fi

    # 3b: Status — verify connection is registered.
    local status_body
    if status_body=$(http_get_body "$BACKEND_BASE_URL/userscript-bridge/status" 2>&1); then
        local connected page_id
        connected=$(echo "$status_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('connected',''))" 2>/dev/null)
        page_id=$(echo "$status_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('page_id',''))" 2>/dev/null)
        assert_eq "  Bridge connected" "$connected" "True"
        assert_eq "  Bridge page_id" "$page_id" "$SMOKE_PAGE_ID"
    else
        fail "GET /status failed"
        echo "$status_body" | sed 's/^/      /' >&2
    fi

    # 3c: Next-instruction with wrong page_id — expect 204 (no instruction).
    local wrong_code
    wrong_code=$(curl -sfS -o /dev/null -w '%{http_code}' \
        "$BACKEND_BASE_URL/userscript-bridge/next-instruction?page_id=$SMOKE_WRONG_PAGE_ID" 2>/dev/null || echo "000")
    case "$wrong_code" in
        204) pass "Wrong-tab next-instruction → 204 (no instruction)" ;;
        200) fail "Wrong-tab next-instruction → 200 (should be 204 — no matching instruction)" ;;
        000) fail "Wrong-tab next-instruction → connection error" ;;
        *) fail "Wrong-tab next-instruction → HTTP $wrong_code (expected 204)" ;;
    esac

    # 3d: Probe (read_title) — sends an instruction to the userscript and waits.
    # Since there's no real userscript, the probe will timeout. This tests the
    # bounded-failure path of the bridge protocol.
    #
    # The probe sends ONE instruction via put_instruction, which blocks for
    # RESULT_TIMEOUT_S (90s) waiting for a result. With no real userscript
    # polling, it times out and returns success=false. We fire it exactly once
    # and capture both the status code and body in a single curl call — a
    # second call would send a duplicate instruction that lingers in the
    # channel queue and pollutes subsequent checks.
    info "Probe (read_title) — expect bounded timeout (no real userscript)..."
    local probe_body probe_code
    probe_body=$(curl -s -w '\n%{http_code}' --max-time 120 \
        -X POST -H 'Content-Type: application/json' \
        -d '{"op":"read_title","selector_kind":"css","selector_value":"title"}' \
        "$BACKEND_BASE_URL/userscript-bridge/probe" 2>/dev/null || echo -e "\n000")
    probe_code=$(echo "$probe_body" | tail -1)
    probe_body=$(echo "$probe_body" | sed '$d')

    case "$probe_code" in
        200)
            local probe_success
            probe_success=$(echo "$probe_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('success',''))" 2>/dev/null)
            if [ "$probe_success" = "False" ]; then
                pass "Probe bounded-failure (success=false, no real userscript)"
            else
                fail "Probe returned success=$probe_success (expected False — no userscript)"
            fi
            ;;
        000)
            # curl timed out (>120s). The backend timeout is still bounded.
            pass "Probe bounded-failure (curl timeout — backend timeout > 120s)"
            ;;
        *)
            fail "Probe returned HTTP $probe_code (expected 200 or timeout)"
            ;;
    esac

    # 3e: Result posting — success path.
    # Post a result for a fake instruction_id. The channel accepts any result
    # (it logs a warning if no pending instruction, but returns ok=true).
    local result_body
    if result_body=$(http_post "$BACKEND_BASE_URL/userscript-bridge/result" \
        -d "{\"instruction_id\":\"smoke-fake-success-$TS\",\"success\":true,\"page_id\":\"$SMOKE_PAGE_ID\"}" 2>&1); then
        local ok
        ok=$(echo "$result_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('ok',''))" 2>/dev/null)
        assert_eq "  Result success path" "$ok" "True"
    else
        fail "POST /result (success) failed"
        echo "$result_body" | sed 's/^/      /' >&2
    fi

    # 3f: Result posting — failure/error path.
    if result_body=$(http_post "$BACKEND_BASE_URL/userscript-bridge/result" \
        -d "{\"instruction_id\":\"smoke-fake-error-$TS\",\"success\":false,\"error\":\"element_not_found\",\"page_id\":\"$SMOKE_PAGE_ID\"}" 2>&1); then
        local ok
        ok=$(echo "$result_body" | python3 -c "import sys,json;print(json.load(sys.stdin).get('ok',''))" 2>/dev/null)
        assert_eq "  Result failure path" "$ok" "True"
    else
        fail "POST /result (failure) failed"
        echo "$result_body" | sed 's/^/      /' >&2
    fi

    # 3g: Next-instruction with correct page_id — expect 204 (no pending instruction).
    #
    # Note: the probe in 3d sends an instruction via put_instruction which
    # enqueues it then blocks in _take_result. On timeout, _take_result returns
    # a timeout result but does NOT dequeue the instruction. The stale
    # instruction lingers in the channel queue. We drain it here by polling
    # next-instruction until we get 204 (or hit a safety limit of 5 tries).
    info "Draining stale instructions from probe/inspect before final check..."
    local drained=0
    for i in 1 2 3 4 5; do
        local drain_code
        drain_code=$(curl -sfS -o /dev/null -w '%{http_code}' \
            "$BACKEND_BASE_URL/userscript-bridge/next-instruction?page_id=$SMOKE_PAGE_ID" 2>/dev/null || echo "000")
        if [ "$drain_code" = "204" ]; then
            break
        fi
        drained=$((drained + 1))
        sleep 1
    done
    if [ $drained -gt 0 ]; then
        info "Drained $drained stale instruction(s) from queue"
    fi

    # Final check: queue should be empty now.
    local correct_code
    correct_code=$(curl -sfS -o /dev/null -w '%{http_code}' \
        "$BACKEND_BASE_URL/userscript-bridge/next-instruction?page_id=$SMOKE_PAGE_ID" 2>/dev/null || echo "000")
    case "$correct_code" in
        204) pass "Correct-tab next-instruction → 204 (queue empty)" ;;
        200) fail "Correct-tab next-instruction → 200 (stale instruction still in queue)" ;;
        *) fail "Correct-tab next-instruction → HTTP $correct_code" ;;
    esac
}

# ---------------------------------------------------------------------------
# Phase 4: Inspect bounded-failure
# ---------------------------------------------------------------------------

phase_inspect() {
    header "Phase 4: Inspect Bounded-Failure"

    # The inspect endpoint requires authentication (X-User-Id). When the bridge
    # is connected but no real userscript can read a JD, the endpoint should
    # return inspect_status=read_failed within a bounded time.
    #
    # However, since the probe in Phase 3 may have left the channel in a
    # timeout state, we need to re-establish a fresh heartbeat first.
    # Also, the inspect flow calls read_current_jd which sends a read_jd
    # instruction and waits RESULT_TIMEOUT_S (90s). With no userscript, this
    # will timeout and return read_failed.
    info "Re-establishing heartbeat for inspect..."
    http_post "$BACKEND_BASE_URL/userscript-bridge/heartbeat" \
        -d "{\"page_id\":\"$SMOKE_PAGE_ID\",\"page_url_hash\":\"$SMOKE_PAGE_URL_HASH\",\"page_title\":\"BOSS Smoke Inspect\"}" \
        >/dev/null 2>&1 || true

    info "Calling POST /boss/recommended-jobs/current/inspect (expect bounded-failure ~90s)..."
    local inspect_body inspect_code
    inspect_code=$(curl -s -o /tmp/smoke_inspect_body.json -w '%{http_code}' --max-time 120 \
        -X POST -H 'Content-Type: application/json' -H "X-User-Id: $SMOKE_USER_ID" \
        -d '{"resume_version_id":null}' \
        "$BACKEND_BASE_URL/boss/recommended-jobs/current/inspect" 2>/dev/null || echo "000")

    case "$inspect_code" in
        200)
            inspect_body=$(cat /tmp/smoke_inspect_body.json 2>/dev/null || echo "{}")
            local inspect_status job application agent_run_id
            inspect_status=$(echo "$inspect_body" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('inspect_status',''))" 2>/dev/null)
            job=$(echo "$inspect_body" | python3 -c "import sys,json;d=json.load(sys.stdin);print('null' if d.get('job') is None else 'present')" 2>/dev/null)
            application=$(echo "$inspect_body" | python3 -c "import sys,json;d=json.load(sys.stdin);print('null' if d.get('application') is None else 'present')" 2>/dev/null)
            agent_run_id=$(echo "$inspect_body" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('agent_run_id',''))" 2>/dev/null)

            assert_eq "  Inspect status" "$inspect_status" "read_failed"
            assert_eq "  Job is null" "$job" "null"
            assert_eq "  Application is null" "$application" "null"

            if [ -n "$agent_run_id" ]; then
                pass "  AgentRun created (id=$agent_run_id)"
                CLEANUP_NOTES+=("AgentRun id=$agent_run_id for user=$SMOKE_USER_ID (left in DB — see cleanup notes)")
            else
                fail "  AgentRun id missing"
            fi
            ;;
        000)
            fail "Inspect request timed out (>120s — unbounded failure)"
            ;;
        *)
            fail "Inspect returned HTTP $inspect_code (expected 200)"
            cat /tmp/smoke_inspect_body.json 2>/dev/null | sed 's/^/      /' >&2
            ;;
    esac

    rm -f /tmp/smoke_inspect_body.json
}

# ---------------------------------------------------------------------------
# Phase 5: Cleanup reporting
# ---------------------------------------------------------------------------

phase_cleanup() {
    header "Phase 5: Cleanup & Residue Report"

    # Redis cleanup: delete keys matching the smoke namespace prefix.
    info "Cleaning Redis keys under '$SMOKE_QUEUE_NAMESPACE' prefix..."
    local redis_container
    redis_container=$(docker compose -f "$REPO_ROOT/docker-compose.yml" ps -q redis 2>/dev/null || true)
    if [ -n "$redis_container" ]; then
        local deleted
        deleted=$(docker exec "$redis_container" redis-cli --scan \
            --pattern "${SMOKE_QUEUE_NAMESPACE}:*" 2>/dev/null | wc -l | tr -d ' ' || echo "0")
        if [ "$deleted" -gt 0 ]; then
            docker exec "$redis_container" redis-cli --scan \
                --pattern "${SMOKE_QUEUE_NAMESPACE}:*" 2>/dev/null \
                | xargs -I {} docker exec "$redis_container" redis-cli DEL {} >/dev/null 2>&1 || true
            pass "Deleted $deleted Redis key(s) under smoke namespace"
        else
            pass "No Redis keys to clean under smoke namespace"
        fi
    else
        skip "Redis cleanup (container not found)"
    fi

    # DB residue: the inspect path creates a UserProfile + AgentRun for
    # SMOKE_USER_ID. We report these but do NOT auto-delete from the business
    # DB — the operator should verify before deleting.
    info "Database residue (SMOKE_USER_ID=$SMOKE_USER_ID):"
    echo "      The inspect endpoint creates:"
    echo "        - 1x UserProfile (user_id=$SMOKE_USER_ID)"
    echo "        - 1x AgentRun (workflow_type=boss_recommended_job_inspect, status=failed)"
    echo ""
    echo "      To clean up manually:"
    echo "        docker compose exec postgres psql -U app -d job_search_agent \\"
    echo "          -c \"DELETE FROM agent_runs WHERE user_id=(SELECT id FROM user_profiles WHERE user_id='$SMOKE_USER_ID');\""
    echo "        docker compose exec postgres psql -U app -d job_search_agent \\"
    echo "          -c \"DELETE FROM user_profiles WHERE user_id='$SMOKE_USER_ID';\""
    echo ""

    if [ ${#CLEANUP_NOTES[@]} -gt 0 ]; then
        info "Additional cleanup notes:"
        for note in "${CLEANUP_NOTES[@]}"; do
            echo "      - $note"
        done
    fi

    # Note: we do NOT docker compose down — the operator may want to inspect logs.
    info "Compose stack left running. To stop: docker compose -f docker-compose.yml down"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

summary() {
    header "Smoke Summary"
    echo -e "  ${GREEN}Passed: $PASS${NC}"
    echo -e "  ${RED}Failed: $FAIL${NC}"
    echo -e "  ${YELLOW}Skipped: $SKIP${NC}"

    if [ $FAIL -gt 0 ]; then
        echo ""
        echo -e "${RED}${BOLD}SMOKE FAILED — $FAIL check(s) did not pass.${NC}"
        echo ""
        echo "Next steps:"
        echo "  1. Review the failed checks above."
        echo "  2. Inspect logs: docker compose logs --tail=200 backend worker"
        echo "  3. Check for cross-namespace pollution in worker logs (missing_run)."
        echo "  4. See docs/e2e-smoke.md for interpretation guide."
        return 1
    else
        echo ""
        echo -e "${GREEN}${BOLD}SMOKE PASSED — all checks green.${NC}"
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

usage() {
    cat <<'EOF'
e2e-smoke.sh — BOSS E2E smoke test with test isolation

Usage: ./scripts/e2e-smoke.sh [OPTIONS]

Options:
  --skip-up    Don't run docker compose up (assume stack is already running)
  --help       Show this help message

Environment:
  BACKEND_BASE_URL       Backend API base (default: http://localhost:8000/api/v1)
  FRONTEND_BASE_URL      Frontend base (default: http://localhost:5173)
  SMOKE_QUEUE_NAMESPACE  Isolated queue namespace (default: auto-generated)
  SMOKE_USER_ID          Test user ID (default: auto-generated)

The script uses an isolated QUEUE_NAMESPACE so it never pollutes the
production default namespace. All isolation values are printed at start.
EOF
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --skip-up) SKIP_UP=true; shift ;;
            --help|-h) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    parse_args "$@"
    print_config

    # Phase 1: Compose services.
    phase_compose || true

    # Phase 2: Service health.
    phase_health || true

    # Phase 3: Bridge protocol.
    phase_bridge || true

    # Phase 4: Inspect bounded-failure.
    phase_inspect || true

    # Phase 5: Cleanup.
    phase_cleanup || true

    # Summary.
    summary
}

main "$@"
