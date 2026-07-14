# 🎧 Radio Playlist Dashboard

> **Live at → [brchn6.github.io/radio-playlist-dashboard](https://brchn6.github.io/radio-playlist-dashboard/)**  
> Real-time track monitoring across Israeli radio stations

A live dashboard that tracks songs played on **8 Israeli radio stations** simultaneously, using [ShazamIO](https://github.com/dotX12/shazamio) for audio recognition. Each station has its own recognition proxy, feeding into a single SQLite database that gets published to **GitHub Pages** — the live site refreshes within ~3 minutes.

## 🚀 How it works

```
8× ShazamIO Proxies (one per station)
  kol-hashfela │ galgalatz │ 99fm │ radio-tlv │ kan-88 │ kan-bet │ galil │ radio-darom
        │
        ▼  polls all 8 every 30s
Multi-Station Updater
        │
        ▼
SQLite Database (single DB, station_id on every track)
        │
        ▼
Data Generator → bounded analytics JSON (top / timeline / heatmap / trends / history)
        │
        ▼  git push every 2 min
GitHub Actions → GitHub Pages deploy (queued, never cancelled, free on public repos)
```

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
