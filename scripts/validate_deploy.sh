#!/usr/bin/env bash
# Radio Dashboard Deploy Validation
# Runs all checks and outputs a JSON report
set -euo pipefail

# Resolve paths relative to this script — never hardcode a host path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

REPORT=""
FAIL=0

log() { REPORT+="$1\n"; echo "$1"; }

log "=== Radio Dashboard Validation Report ==="
log "Timestamp: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
log ""

# 1. Proxy health
log "--- 1. Proxy Health ---"
HEALTH=$( .venv/bin/python scripts/proxy_manager.py health 2>&1 )
if echo "$HEALTH" | grep -q '"all_healthy": true'; then
    log "  ✅ All 8 proxies healthy"
else
    FAIL=1
    log "  ❌ Proxy health check FAILED"
    log "  $HEALTH"
fi

# 2. Each proxy responds to /current
log "--- 2. Proxy /current endpoint ---"
for port in 8761 8762 8763 8764 8765 8766 8767 8768; do
    resp=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port/current" 2>/dev/null || echo "000")
    if [ "$resp" = "200" ]; then
        log "  ✅ Port $port responds 200"
    else
        FAIL=1
        log "  ❌ Port $port: HTTP $resp"
    fi
done

# 3. Log errors
log "--- 3. Log errors since midnight ---"
MIDNIGHT=$(date -d "$(date -u '+%Y-%m-%d') 00:00:00" -u '+%s' 2>/dev/null || echo 0)
for f in logs/proxy-*.log; do
    slug=$(basename "$f" .log | sed 's/proxy-//')
    errs=$(grep -ci "error\|traceback\|exception" "$f" 2>/dev/null || echo 0)
    if [ "$errs" -gt 0 ]; then
        FAIL=1
        log "  ❌ $slug: $errs errors in log"
    else
        log "  ✅ $slug: clean log"
    fi
done

# 4. Token bucket exists and has content
log "--- 4. Token Bucket ---"
if [ -f /tmp/shazam-token-bucket ]; then
    entries=$(python3 -c "import json; print(len(json.loads(open('/tmp/shazam-token-bucket').read())))" 2>/dev/null || echo "0")
    log "  ✅ Token bucket active with $entries entries"
else
    log "  ⚠️  Token bucket file not found (may be idle period)"
fi

# 5. Staggered intervals are active
log "--- 5. Staggered Intervals ---"
for port in 8761 8762 8763 8764 8765 8766 8767 8768; do
    interval=$(curl -s "http://127.0.0.1:$port/current" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('interval_seconds','?'))" 2>/dev/null || echo "?")
    log "  Port $port: interval=$interval"
done

# 6. Supabase data flow (tracks being added)
# SQLite is gone (removed architecture). Read SUPABASE_URL + SUPABASE_SECRET_KEY
# at RUNTIME from the live project's .env (never embedded in this script) and
# COUNT tracks recognized in the last hour via the PostgREST endpoint.
log "--- 6. Data Flow (Supabase) ---"
ENV_FILE="${RADIO_DASH_ENV:-$HOME/dev/radio-playlist-dashboard/.env}"
if [ ! -f "$ENV_FILE" ]; then
    FAIL=1
    log "  ❌ .env not found at $ENV_FILE — cannot validate collector freshness"
else
    set +e
    set -a
    # shellcheck disable=SC1091
    source "$ENV_FILE" 2>/dev/null
    SOURCE_RC=$?
    set +a
    set -e
    if [ "$SOURCE_RC" -ne 0 ]; then
        FAIL=1
        log "  ❌ Could not source $ENV_FILE — cannot validate collector freshness"
    elif [ -z "${SUPABASE_URL:-}" ] || [ -z "${SUPABASE_SECRET_KEY:-}" ]; then
        FAIL=1
        log "  ❌ SUPABASE_URL / SUPABASE_SECRET_KEY missing from $ENV_FILE — cannot validate collector freshness"
    else
        SINCE=$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ')
        COUNT=$(curl -sfG --max-time 15 "$SUPABASE_URL/rest/v1/tracks" \
            --data-urlencode "select=count" \
            --data-urlencode "recognized_at=gt.$SINCE" \
            -H "apikey: $SUPABASE_SECRET_KEY" \
            -H "Authorization: Bearer $SUPABASE_SECRET_KEY" \
            2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['count'])" 2>/dev/null || echo "")
        if [ -n "$COUNT" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
            log "  ✅ $COUNT tracks recognized in the last hour"
        else
            FAIL=1
            log "  ❌ No tracks recognized in the last hour (collector may be down)"
        fi
    fi
fi

# 7. Updater service running
log "--- 7. Collector Service ---"
if systemctl --user is-active radio-updater.service &>/dev/null; then
    log "  ✅ radio-updater.service active"
else
    FAIL=1
    log "  ❌ radio-updater.service NOT active"
fi

log ""
if [ $FAIL -eq 0 ]; then
    log "✅ ALL CHECKS PASSED"
else
    log "❌ SOME CHECKS FAILED"
fi

# Write report
mkdir -p logs
echo -e "$REPORT" > "logs/validation-$(date -u '+%Y%m%d-%H%M').log"
echo -e "$REPORT"
exit $FAIL
