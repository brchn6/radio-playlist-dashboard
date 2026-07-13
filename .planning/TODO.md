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

## 🔲 Phase 3 — Multi-Station Updater
- [ ] Rewrite `updater.py` with async polling (all 7 proxies)
- [ ] `station_id` tagging on insert
- [ ] Per-station dedup (track_exists checks station_id)
- [ ] Graceful handling of offline stations
- [ ] Cleanup respects per-station retention

## 🔲 Phase 4 — Per-Station Data Generation
- [ ] Update `generate_data.py` for per-station JSON
- [ ] `docs/data/stations/` directory structure
- [ ] Aggregated "all" views
- [ ] Per-station current, history, hype, scatter, stats
- [ ] Stations metadata JSON

## 🔲 Phase 5 — Multi-Station Dashboard
- [ ] Station selector tabs (7 colored pills)
- [ ] "All stations" aggregated tab
- [ ] Per-station Now Playing
- [ ] Per-station History
- [ ] Per-station Hype Tracks
- [ ] Per-station Scatter (colored by station)
- [ ] Per-station Stats
- [ ] Comparison scatter (overlay all stations)
- [ ] Lazy loading (fetch per-station JSON on tab switch)

## 🔲 Phase 6 — Deployment
- [ ] Update `scripts/manage.sh` for multi-station
- [ ] Test full start/stop cycle
- [ ] SystemD template units (optional)
- [ ] Stress test with all 7 stations running
- [ ] Resource monitoring

## 🔲 Future Ideas
- [ ] Kan Gimel (if stream becomes accessible)
- [ ] Galei Tzahal (if stream becomes accessible)
- [ ] More StreamTheWorld stations
- [ ] Web radio directory scanner
- [ ] Per-station notification on new track
- [ ] Most-played comparison across stations
- [ ] Time-of-day heatmap per station
