# 🎧 Radio Playlist Dashboard

> **Live at → [brchn6.github.io/radio-playlist-dashboard](https://brchn6.github.io/radio-playlist-dashboard/)**  
> Real-time track monitoring across Israeli radio stations

A live dashboard that tracks songs played on **8 Israeli radio stations** simultaneously, using [ShazamIO](https://github.com/dotX12/shazamio) for audio recognition. Each station has its own recognition proxy, feeding into a single SQLite database that gets published to **GitHub Pages** — the live site refreshes within ~3 minutes.

![Dashboard demo](docs/demo.png)

## How it works

```
8× ShazamIO Proxies (one per station)
  kol-hashfela │ galgalatz │ 99fm │ radio-tlv │ kan-88 │ kan-bet │ galil │ radio-darom
        │
        ▼  polls all 8 every 20s
Multi-Station Updater (updater.py)
        │
        ▼
SQLite Database (single DB, station_id on every track)
        │
        ▼  every cycle
generate_data.py → docs/data/*.json (bounded aggregates)
        │
        ▼  git push every 2 min (only docs/data/)
GitHub Actions → GitHub Pages deploy (queued, never cancelled, free on public repos)
```

### Auto-Push Pipeline — Step by Step

The auto-push system keeps the live site in sync without any server, cron, or cloud infra. Here’s how it ticks:

**1. The daemon (`updater.py`)**

Run as a background process:
```bash
GIT_AUTO_PUSH=1 nohup python scripts/updater.py > logs/updater.log 2>&1 &
```
The `GIT_AUTO_PUSH=1` env var is the master switch. Without it, the daemon polls proxies and generates data locally, but never pushes.

**2. Main loop (every 20 seconds)**

Each cycle:
1. **Poll all 8 proxies** — fetches `/current` from each ShazamIO proxy (ports 8761–8768)
2. **Store new tracks** — if the track changed since last poll, inserts into SQLite (`data/playlist.db`)
3. **Generate static JSON** — runs `generate_data.py`, which reads SQLite and writes bounded aggregates to `docs/data/` (top.json, timeline.json, heatmap.json, history.json, etc. — 13 files total)
4. **Git push (throttled)** — calls `git_commit_and_push()`, but only if at least `PUSH_EVERY_SECONDS` (default **120 seconds**) has passed since the last push

**3. The git push logic**

```python
def git_commit_and_push(message):
    # 1. Check if anything changed in docs/data/
    git status --porcelain -- docs/data
    if nothing changed → return early (no wasteful commits)

    # 2. Stage only docs/data/ (never git add -A —
    #    avoids sweeping in-progress source edits)
    git add -- docs/data

    # 3. Commit (no [skip ci] — must trigger Pages deploy)
    git commit -m "auto: multi-station update [...timestamp...]"

    # 4. Pull-rebase to handle any remote divergence
    git pull --rebase

    # 5. Push — uses GIT_TOKEN from .env for auth, or default remote
    git push https://brchn6:{token}@github.com/... main
```

**4. Pages deploy trigger (`deploy.yml`)**

The push to `main` triggers a GitHub Actions workflow:
```yaml
on:
  push:
    branches: [main]

concurrency:
  group: pages
  cancel-in-progress: false   # queue, don't cancel
```
- `cancel-in-progress: false` means a running build **finishes** even if a newer push arrives.
- GitHub keeps only the **newest pending** run per concurrency group, so the latest data always deploys.
- Pages is configured as `build_type: workflow` (not legacy branch deploys), so the 10-builds/hour soft limit on legacy builds does not apply.

**5. Data freshness**

| Component | Freshness | Source |
|-----------|-----------|--------|
| **Now Playing** tab | ~30s | Reads local proxies directly via HTTP |
| **All other tabs** (charts, history) | ~3 min | Loads deployed Pages JSON |
| **Git commits** | Every 2 min | Throttled by `PUSH_EVERY_SECONDS=120` |
| **GitHub Pages deploy** | ~1 min after push | Actions workflow |

**6. Why it works**

- **GitHub Actions minutes are free** for public repos — 720 pushes/day costs nothing.
- **JSON payloads are bounded** — `generate_data.py` caps history at 1000 entries, so git deltas stay small per commit.
- **No legacy Pages builds** — Pages source is set to GitHub Actions, sidestepping the 10-builds/hour limit on branch deploys.
- **Builds queue instead of collapsing** — `cancel-in-progress: false` means a deploy in progress finishes, while GitHub queues the newest one so the latest data always gets served.

## 💸 Why it's built this way

Every choice in this architecture exists to keep the cost at **zero dollars**.
There are no cloud servers, no paid APIs, no database hosts.

| Decision | Why |
|----------|-----|
| **Run locally** instead of a VPS | The machine is already on at home. 8 proxies + updater use ~400 MB RAM — no reason to rent a server. |
| **SQLite** instead of PostgreSQL | Single writer, tiny dataset (~42 MB). No need for a separate database server. |
| **Precomputed JSON** instead of live APIs | Generate static files every 30s → push to git → serve from Pages. No backend server needed. |
| **Git as transport** | `git push` is free, authenticates via token, and triggers deploy automatically. No rsync, no S3, no extra infra. |
| **Public repo** | GitHub Actions minutes are unlimited on public repos. Data is public anyway — it's a live dashboard. |
| **ShazamIO** instead of a paid API | Free Python wrapper, no API key or subscription needed. |

**Total monthly cost: $0.** No subscription to cancel, no bill that grows, no reason to shut it down.

## 📦 Project structure

```
radio-playlist-dashboard/
├── docs/                        # GitHub Pages root
│   ├── index.html               # Dashboard (single-page app)
│   ├── .nojekyll
│   └── data/                    # Precomputed analytics JSON
│       ├── stations.json        # Station metadata
│       ├── current.json         # Currently playing track per station
│       ├── history.json         # Recent track history (capped)
│       ├── top.json             # Most-played tracks
│       ├── timeline.json        # Activity over time
│       ├── heatmap.json         # Day-of-week / hour heatmap
│       ├── trends.json          # Trending tracks
│       ├── stats.json           # Aggregate statistics
│       ├── cross_station.json   # Cross-station comparisons
│       └── non_music.json       # Talk / ad segments
├── scripts/
│   ├── updater.py               # Multi-station poller daemon
│   ├── proxy_manager.py         # Start/stop proxy instances
│   ├── generate_data.py         # SQLite → JSON for Pages
│   ├── db.py                    # SQLite schema + queries
│   └── manage.sh                # Service manager
├── data/
│   └── playlist.db              # SQLite database (gitignored)
├── .planning/                   # Architecture notes & design docs
├── .env                         # GIT_TOKEN (gitignored)
├── .env.example                 # Token template
├── .gitignore
└── README.md
```

## 🛠️ Setup

### Prerequisites

- **Python 3.10+**
- **ShazamIO** — `pip install shazamio` (see [dotX12/shazamio](https://github.com/dotX12/shazamio))
- **FFmpeg** — for audio capture (`sudo dnf install ffmpeg` on Fedora)
- **Git**

### Quick start

```bash
git clone https://github.com/brchn6/radio-playlist-dashboard.git
cd radio-playlist-dashboard

# Configure GitHub token for auto-push
cp .env.example .env
# Edit .env and add your token:
#   GIT_TOKEN=ghp_...

# Start all proxies + updater daemon
bash scripts/manage.sh start

# Check status
bash scripts/manage.sh status
```

### Generate data manually (test)

```bash
python scripts/generate_data.py
```

## 📋 Commands

```bash
bash scripts/manage.sh start        # Start all proxies + updater
bash scripts/manage.sh stop         # Stop everything
bash scripts/manage.sh status       # Health check
bash scripts/manage.sh restart      # Stop + start
bash scripts/manage.sh generate     # Run data generator
bash scripts/manage.sh proxy start <slug>  # Single station
bash scripts/manage.sh logs         # View logs
```

## 📻 Stations

| Station | Stream |
|---------|--------|
| 🟢 קול השפלה 103.6FM | `radio.streamgates.net/stream/1036kh` |
| 🔴 גלגלצ | `glzwizzlv.bynetcdn.com/glglz_mp3` |
| 🔵 99FM | `99.livecdn.biz/99fm_aac` |
| 🟡 רדיו תל אביב 102FM | `cdn88.mediacast.co.il/102-tlv-live/102fm_aac/icecast.audio` |
| 🟣 כאן 88 | `27953.live.streamtheworld.com/KAN_88.mp3` |
| 🟠 כאן ב | `27913.live.streamtheworld.com/KAN_BET.mp3` |
| 🟢 קול הגליל העליון | `radio.streamgates.net/stream/galil` |
| 🟢 רדיו דרום 97FM | `cdn.cybercdn.live/Darom_97FM/Live/icecast.audio` |

## 🔗 Related

- [dotX12/shazamio](https://github.com/dotX12/shazamio) — Python Shazam API wrapper
- [brchn6/radio-kol-hashfela](https://github.com/brchn6/radio-kol-hashfela) — Android/iOS app for Kol Hashfela

## 📝 License

Do whatever you want. Made for the love of radio.
