#!/usr/bin/env bash
# Radio Dashboard Deploy Validation
# Runs all checks and outputs a JSON report
set -euo pipefail

cd /home/barc/dev/radio-playlist-dashboard

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
log "--- 6. Data Flow ---"
TRACKS=$(sqlite3 data/playlist.db "SELECT COUNT(*) FROM tracks WHERE recognized_at > datetime('now', '-1 hour');" 2>/dev/null || echo "?")
if [ "$TRACKS" != "?" ] && [ "$TRACKS" -gt 0 ] 2>/dev/null; then
    log "  ✅ $TRACKS tracks in last hour"
elif [ "$TRACKS" = "?" ]; then
    log "  ⚠️  Could not query SQLite (sqlite3 not installed?)"
else
    FAIL=1
    log "  ❌ No tracks in last hour (collector may be down)"
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
