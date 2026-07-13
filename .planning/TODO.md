# Multi-Station Implementation — TODO

## ✅ Done
- [x] Single-station ShazamIO proxy (port 8765 → will migrate to 8761)
- [x] Single-station updater (JSON-based)
- [x] SQLite database with tracks table
- [x] Data generator → GitHub Pages
- [x] 5-tab dashboard (Now, History, Hype, Scatter, Stats)
- [x] 45-day retention cleanup
- [x] Station investigation — 7 working stations found
- [x] Architecture plan

## ✅ Phase 1 — Database
- [x] Add `stations` table schema
- [x] Add `station_id` column to `tracks`
- [x] Migration script (station_id=1 for existing data)
- [x] Update `db.py` with station-aware queries
- [x] Seed 7 stations from config

## ✅ Phase 2 — Multi-Proxy Manager
- [x] Create `scripts/proxy_manager.py`
- [x] `start_all()` — spawn 7 proxies (ports 8761-8767)
- [x] `stop_all()` — kill all proxies
- [x] `status()` — health check all ports
- [x] `start_one(slug)` / `stop_one(slug)`
- [x] Logs per station

## ✅ Phase 3 — Multi-Station Updater
- [x] Rewrite `updater.py` with multi-proxy polling (all 7 proxies)
- [x] `station_id` tagging on insert
- [x] Per-station dedup (track_exists checks station_id)
- [x] Graceful handling of offline stations
- [x] Cleanup respects per-station retention

## ✅ Phase 4 — Per-Station Data Generation
- [x] Update `generate_data.py` for per-station JSON
- [x] `docs/data/stations/` directory structure (41 files)
- [x] Aggregated "all" views
- [x] Per-station current, history, hype, scatter, stats
- [x] Stations metadata JSON

## ✅ Phase 5 — Multi-Station Dashboard
- [x] Station selector pills (7 colored)
- [x] "All stations" aggregated tab
- [x] Per-station Now Playing grid
- [x] Per-station History (filtered by pill)
- [x] Per-station Hype Tracks
- [x] Per-station Scatter (colored by station)
- [x] Per-station Stats
- [x] Comparison scatter (overlay all stations)

## 🔲 Phase 6 — Resilience
- [ ] **429 (rate limit) handling in proxy** — exponential backoff (1min→2min→4min→…→15max) when Shazam returns 429, instead of aggressive 5s retry
- [ ] Shared rate limiter across all 8 proxies (stagger API calls so they don't all hit Shazam simultaneously)

## 🔲 Phase 7 — Polish
- [ ] 99FM and 102FM referer header fix (streams return 403 sometimes)
- [ ] SystemD template units for proxies
- [ ] Stress test with all 7 stations running 24h
- [ ] Resource monitoring

## 🚀 Spotify Export — Take History to Spotify
See full plan: `.planning/SPOTIFY-EXPORT.md`

### Milestone 1: 🎧 "Open in Spotify" link (Phase 1)
- [ ] Helper function `spotifySearchUrl()` in dashboard JS
- [ ] Spotify button in History rows
- [ ] Spotify button in Now Playing cards
- [ ] Spotify button in Top Songs/Artists
- [ ] CSS styling for `.spotify-btn`

### Milestone 2: 📋 Bulk playlist builder (Phase 2)
- [ ] Filter bar (station, date range, reverse toggle, dedup toggle)
- [ ] Checkbox per history row + Select All / Deselect All
- [ ] Copy selected tracks as formatted text to clipboard
- [ ] "Open All in Spotify" button

### Milestone 3: 🔌 Full Spotify API integration (Phase 3)
- [ ] Create `scripts/spotify_api.py` (standalone service, port 8760)
- [ ] Spotify Developer app → client ID + secret in `.env`
- [ ] OAuth flow: auth → callback → token storage
- [ ] Track search/resolve endpoint
- [ ] Playlist create + populate endpoint
- [ ] Dashboard JS connected to local API

### Milestone 4: 🧠 Smart curation (Phase 4)
- [ ] Cross-station mix option
- [ ] Auto-playlist by time-of-day pattern
- [ ] Scheduled export cron

## 🔲 Future Ideas
- [ ] Kan Gimel (if stream becomes accessible)
- [ ] Galei Tzahal (if stream becomes accessible)
- [ ] More StreamTheWorld stations
- [ ] Web radio directory scanner
- [ ] Per-station notification on new track
- [ ] Most-played comparison across stations
- [ ] Time-of-day heatmap per station
