# Egress Optimization — Slow-Cadence Aggregates + Slower Poll

> **Status:** Implemented - shipped on 2026-08-10 (Option 1 + Option 2 combined)
> **Date:** 2026-08-10
> **Problem:** Free-tier Supabase egress (5 GB) exceeded: 10.15 GB used. Post-fix burn is still ~70.7 KB gz per poll per open tab = 6.26 GB/month/tab, because 9 aggregate files change hash every collector cycle and every open tab re-downloads them every 30s.

## Why egress happens (the mechanism)

```
collector cycle (~20-50s) → generate_data.py rewrites ALL aggregates
   → publish.py uploads files whose hash changed + manifest.json
      → every open tab polls Storage every 30s (POLL=30000 in docs/index.html)
         → downloads every file whose hash moved
            → egress billed per byte served
```

The tab is what converts "file changed in Storage" into billed egress. Files changing every cycle + tabs polling every 30s = guaranteed re-download loop. Only `recent.json` and `current.json` genuinely need per-cycle freshness.

## Measured wire sizes (gzipped, CDN on-the-fly)

| File | gz bytes/poll | Needs per-cycle? |
|------|--------------|------------------|
| recent.json | 24,019 | ✅ yes (last 300 tracks) |
| current.json | 1,187 | ✅ yes (now playing) |
| history_index.json | 131 | no - changes once/day |
| **transitions.json** | **20,576** | ❌ meta-analysis |
| **top.json** | **9,604** | ❌ windowed leaders |
| **bpm_key.json** | **7,342** | ❌ meta-analysis |
| **trends.json** | **5,567** | ❌ daily trends |
| **cross_station.json** | **3,135** | ❌ meta-analysis |
| **stats.json** | **843** | ❌ aggregated stats |
| manifest.json | ~1,500 | yes (hash index) |
| **TOTAL per poll** | **~70.7 KB** | |

Already throttled (existing pattern to reuse): `clusters.json` (24 h), `transition_map.json` (24 h) via mtime gate in `generate_data.py`.

## Option 1 — Slow-cadence for slow aggregates (5 min refresh)

**Goal:** the 6 slow files above only change once every 5 min instead of every cycle. Their hashes stay stable between refreshes, so tabs stop re-downloading them each poll.

### Changes in `scripts/generate_data.py`

**1. New constant** (near line 66, with the existing refresh constants):

```python
SLOW_REFRESH_SECONDS = 300   # 5 min: slow aggregates (top/transitions/bpm/trends/cross/stats)
```

**2. New helper** (next to `write_json`, line 275). Reuses the mtime-gate pattern already used for clusters/transition_map:

```python
def maybe_write_json(path: Path, rel: str, build, sizes: dict[str, int], min_age_s: int, now: datetime) -> None:
    """Write path only if it is older than min_age_s (or missing).
    If skipped, record the existing file size so the manifest stays stable.
    build is a zero-arg callable returning the payload to write."""
    if path.exists():
        age_s = (now - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds()
        if age_s < min_age_s:
            sizes[rel] = path.stat().st_size
            print(f"  [{rel}] skipped - {age_s/60:.1f} min old (< {min_age_s/60:.0f} min refresh)", flush=True)
            return
    write_json(path, build(), sizes, rel)
```

**3. Gate the six writes.** Each becomes a lazy lambda so the build only runs when the file is due:

- Line ~1234: `write_json(output_dir / "top.json", {"windows": build_top(tracks, now, first_seen_map)}, ...)`
  → `maybe_write_json(output_dir / "top.json", "top.json", lambda: {"windows": build_top(tracks, now, first_seen_map)}, sizes, SLOW_REFRESH_SECONDS, now)`
- Line ~1337: `trends.json` → same pattern with `build_trends(tracks, now)`
- Line ~1361: `cross_station.json` → same with its payload builder
- Line ~1376: `bpm_key.json` → same with `build_bpm_key(tracks, slugs)`
- Line ~1378: `transitions.json` → same with `{"stations": ...}` payload. **Caveat:** `build_transitions()` output is also consumed for `transition_map.json` (24 h gated). Keep the transition_map logic working as-is; do NOT skip the build when transition_map is due.
- Line ~1449: `stats.json` → same with its stats payload

**4. Frontend impact of skipping:** the History/Top/Deep tabs show data up to 5 min stale for these panels. Acceptable - the data is still produced from the same tracks, just less frequently. The `manifest.json` hash for each gated file is unchanged between refreshes, so `loadTracked()` serves from cache (no re-download).

### Effect (option 1 alone)

| Scenario | per poll | per day | per month |
|----------|----------|---------|-----------|
| 1 tab, 30s poll | ~30 KB avg (fast 25.4 + slow 47 KB/10 polls) | ~87 MB | **~2.6 GB** |
| 2 tabs | | ~174 MB | ~5.2 GB |

## Option 2 — Poll every 60s instead of 30s

**Goal:** halve all per-tab traffic.

### Change in `docs/index.html`

- Line ~533: `const POLL = 30000;` → `const POLL = 60000;`

**Frontend impact:** Now Playing freshness drops from ~30s to ~60s (already bounded by the collector cycle + Shazam recognition latency; the `current.json` data is not real-time anyway). Live timer text (line 623) is independent of POLL and stays 1s.

## Combined effect (options 1 + 2)

| Scenario | per poll avg | per day | per month |
|----------|--------------|---------|-----------|
| 1 tab, 60s poll | ~30 KB (fast 25.4 + slow 4.7 avg) | ~43 MB | **~1.3 GB** |
| 2 tabs | | ~86 MB | ~2.6 GB |
| 3 tabs | | ~130 MB | ~3.9 GB |

Within the 5 GB free tier for typical 1-3 tab usage, with room for the ~2.8 GB/month of residual bucket egress from other sources (history shards on demand, etc.).

## Files touched

| File | Change |
|------|--------|
| `scripts/generate_data.py` | `SLOW_REFRESH_SECONDS` const, `maybe_write_json()` helper, gate 6 writes |
| `docs/index.html` | `POLL` 30000 → 60000 |

## Rollout / verification

1. Apply changes on head1 (`~/dev/radio-playlist-dashboard`).
2. **JS syntax check before push** (AGENTS.md rule): `node -e '...'` on `docs/index.html`.
3. `python3 -m py_compile scripts/generate_data.py`.
4. Restart updater: `systemctl --user restart radio-updater`.
5. Watch `logs/updater.log` for `[top.json] skipped - ...` style lines proving the gates fire.
6. Run `publish.py --dry-run` twice ~1 min apart: second run should show only `recent.json` / `current.json` / today's shard / `history_index.json` changed, NOT the 6 slow files.
7. Commit + push (deploys frontend via Actions); data publishes automatically from the updater.
8. Check Supabase egress meter after 24-48 h: should be far below the pre-fix rate.

## Out of scope (noted for future)

- Frontend: don't refetch today's shard every poll when History tab is open (only refresh every 5 min). The shard is 117 KB gz and changes every cycle - if someone parks on History, it's +10.3 GB/month. Revisit after options 1+2 are measured.
- Reducing `recent.json` payload (300 tracks → fewer fields or smaller limit) - would cut the largest per-poll file further.
