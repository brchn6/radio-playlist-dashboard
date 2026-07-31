#!/usr/bin/env bash
# Install the collector on a Linux host with systemd (the "head1" box).
#
# The collector used to run on a workstation under `nohup`. On 2026-07-14 it died
# silently and stayed dead for 58 minutes — radio is live, so that hour of songs
# is gone forever. That is what these units exist to prevent: Restart=always, plus
# a timer that revives dead proxies.
#
#   bash deploy/install.sh
#
# Prerequisites the script checks for but will not install silently:
#   - ffmpeg          (sudo apt install -y ffmpeg) — the proxies capture audio with it
#   - .env            SUPABASE_URL + SUPABASE_SECRET_KEY (copy it in by scp, never git)
#
# The collector writes directly to Supabase Postgres — there is no local DB to
# copy. A new host needs only the repo, .env, venvs, and the systemd units.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
cd "$ROOT"

echo "==> checks"
command -v ffmpeg >/dev/null || { echo "FATAL: ffmpeg missing — sudo apt install -y ffmpeg"; exit 1; }
[ -f .env ] || { echo "FATAL: .env missing — needs SUPABASE_URL and SUPABASE_SECRET_KEY"; exit 1; }
grep -q '^SUPABASE_SECRET_KEY=' .env || { echo "FATAL: .env has no SUPABASE_SECRET_KEY"; exit 1; }
chmod 600 .env
echo "    ffmpeg: $(command -v ffmpeg)"
echo "    .env:   present (mode $(stat -c %a .env))"

echo "==> venvs"
# Collector: supabase + numpy + scikit-learn.
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Proxies: shazamio. proxy_manager.py looks for shazamio/.venv/bin/python specifically.
#
# audioop-lts is dropped on Python < 3.13: it is a BACKPORT of the stdlib `audioop`
# module that 3.13 removed, and it refuses to install on older versions — where
# `audioop` is still built in and the backport is unnecessary.
[ -d shazamio/.venv ] || python3 -m venv shazamio/.venv
shazamio/.venv/bin/pip install -q --upgrade pip
if shazamio/.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info < (3,13) else 1)'; then
    grep -v '^audioop-lts' shazamio/requirements.txt > /tmp/req-shazamio.txt
    shazamio/.venv/bin/pip install -q -r /tmp/req-shazamio.txt
else
    shazamio/.venv/bin/pip install -q -r shazamio/requirements.txt
fi
echo "    collector venv + shazamio venv ready"

echo "==> systemd user units"
mkdir -p "$UNIT_DIR" logs
cp deploy/systemd/*.service deploy/systemd/*.timer "$UNIT_DIR/"
systemctl --user daemon-reload
systemctl --user enable -q radio-proxies.service radio-updater.service radio-proxies-heal.timer

# Without linger, user units die at logout and never come back after a reboot —
# which would defeat the entire point of this.
loginctl enable-linger "$USER" 2>/dev/null || \
    echo "    WARN: could not enable linger; run: sudo loginctl enable-linger $USER"

echo "==> start"
systemctl --user start radio-proxies.service
systemctl --user start radio-updater.service
systemctl --user start radio-proxies-heal.timer
sleep 5
for u in radio-proxies radio-updater; do
    printf '    %-16s %s\n' "$u" "$(systemctl --user is-active $u.service)"
done
printf '    %-16s %s\n' "heal.timer" "$(systemctl --user is-active radio-proxies-heal.timer)"

cat <<'EOF'

Done. Watch it:   journalctl --user -u radio-updater -f
                  tail -f logs/updater.log

⚠  ONE COLLECTOR AT A TIME. The collector writes directly to Supabase Postgres;
   a second collector would insert duplicate plays because the (station_id,
   shazam_key, recognized_at) natural key does not collide at slightly different
   timestamps — nothing catches it. Stop the old collector before starting this
   one; nothing else needs copying. If you are decommissioning the old host,
   drain its data/retry_queue.jsonl first, or un-flushed rows are lost.
EOF
