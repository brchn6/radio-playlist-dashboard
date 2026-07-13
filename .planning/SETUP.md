# Setup & Migration Guide

## What you need

| Dependency | Where it lives | In git? |
|------------|----------------|---------|
| Python 3 | System | — |
| `shazamio` + dependencies | `shazamio/.venv/` | ❌ (recreated via `requirements.txt`) |
| `ffmpeg` | System package | — |
| GitHub token (for auto-push) | `.env` | ❌ (template at `.env.example`) |
| SQLite database | `data/playlist.db` | ❌ (runtime data, copy manually) |

---

## Fresh setup on a new machine

```bash
# 1. Clone
git clone git@github.com:brchn6/radio-playlist-dashboard.git
cd radio-playlist-dashboard

# 2. Install system deps
sudo apt install ffmpeg    # or brew install ffmpeg

# 3. Set up shazamio virtual environment
cd shazamio
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..

# 4. Configure GitHub token (for auto-push)
cp .env.example .env
# Edit .env with your token from https://github.com/settings/tokens
# (scopes needed: repo, workflow)

# 5. (Optional) Restore history from old machine
# Copy data/playlist.db from old machine

# 6. Start everything
bash scripts/manage.sh start
```

---

## Migration checklist

- [ ] `git clone` the repo
- [ ] Install `ffmpeg`
- [ ] Create venv + `pip install -r shazamio/requirements.txt`
- [ ] Create `.env` with GitHub token
- [ ] (optional) Copy `data/playlist.db` from old machine
- [ ] Run `bash scripts/manage.sh start`

---

## Memory usage (approximate)

| Component | RAM per instance | Total (8 stations) |
|-----------|:----------------:|:------------------:|
| Shazamio proxy | ~68 MB | ~544 MB |
| Updater daemon | ~28 MB | ~28 MB |
| **Total** | | **~570 MB** |

Scaling to all 66 fm1 stations would require ~4.5 GB RAM (one proxy per station).

---

## Architecture notes

- **8 proxies** (ports 8761-8768) run from `shazamio/shazamio_proxy.py`, each consuming a radio stream and running Shazam recognition every 20s
- On recognition failure, proxies retry every 5s (not waiting a full cycle)
- **Updater** polls all proxies every 20s, stores new tracks + non-music events in SQLite
- Non-music events (commercials, talk, silence) are logged in `non_music_log` table
- Static JSON data is generated every cycle into `docs/data/` for GitHub Pages
- Auto-push to `main` happens every cycle (with `[skip ci]` to prevent Actions spam)
- GitHub Pages deploy triggered via REST API every ~10 minutes

---

## Files structure

```
radio-playlist-dashboard/
├── shazamio/                       ← Bundled ShazamIO proxy
│   ├── shazamio_proxy.py           ← Audio capture + recognition loop
│   ├── requirements.txt            ← Python dependencies (shazamio, aiohttp)
│   └── .venv/                      ← Virtual env (not in git)
├── scripts/
│   ├── proxy_manager.py            ← Start/stop/health for all proxies
│   ├── updater.py                  ← Polls proxies, stores tracks, generates data
│   ├── db.py                       ← SQLite schema + queries
│   ├── generate_data.py            ← Static JSON generation for Pages
│   └── manage.sh                   ← Convenience wrapper
├── data/
│   └── playlist.db                 ← SQLite database (not in git)
├── docs/data/                      ← Static JSON (tracked in git, served by Pages)
├── logs/                           ← Proxy + updater logs (not in git)
├── .env                            ← GitHub token (not in git)
└── .env.example                    ← Template for .env
```
