# 🎧 1036 Playlist Dashboard

> **Multi-station automatic playlist tracker for Israeli radio stations**  
> מזהה שירים אוטומטית מתחנות רדיו ישראליות ושומר היסטוריית השמעה

A live dashboard that tracks songs played on **7 Israeli radio stations** simultaneously using [ShazamIO](https://github.com/dotX12/shazamio). Each station gets its own recognition proxy. All data flows into a single SQLite database and gets published to **GitHub Pages** every 30 seconds.

## 🚀 How it works

```
7x ShazamIO Proxies (ports 8761-8767)
  kol-hashfela │ galgalatz │ 99fm │ radio-tlv │ kan-88 │ kan-bet │ galil
        │
        ▼  poll all 7 every 30s
Multi-Station Updater
        │
        ▼
SQLite (single DB, station_id on every track)
        │
        ▼
Data Generator → 41 JSON files (aggregated + per-station)
        │
        ▼
GitHub Pages (auto-push on new tracks)
```

## 📦 Project structure

```
1036playlistdashboard/
├── docs/                        # GitHub Pages root
│   ├── index.html               # Dashboard (TODO: multi-station UI)
│   ├── stations.json            # Station metadata
│   ├── current.json             # Current track per station
│   ├── history.json             # All tracks
│   ├── hype.json                # Most played
│   ├── scatter.json             # Time-based data
│   ├── stats.json               # Aggregated stats
│   └── stations/                # Per-station data
│       ├── kol-hashfela/
│       ├── galgalatz/
│       ├── 99fm/
│       ├── radio-tlv/
│       ├── kan-88/
│       ├── kan-bet/
│       └── galil/
├── scripts/
│   ├── updater.py               # Multi-station poller daemon
│   ├── proxy_manager.py         # Start/stop all 7 proxies
│   ├── generate_data.py         # SQLite → JSON for Pages
│   ├── db.py                    # SQLite schema + queries
│   └── manage.sh                # Service manager
├── data/
│   └── playlist.db              # SQLite DB (gitignored)
├── .planning/
│   ├── ARCHITECTURE.md          # Full architecture plan
│   └── TODO.md                  # Implementation tracker
├── .env                         # GIT_TOKEN (gitignored)
├── .env.example                 # Token template
├── .gitignore
└── README.md
```

## 🛠️ Setup

### 1. Prerequisites

- **ShazamIO** — `pip install shazamio` or see [dotX12/shazamio](https://github.com/dotX12/shazamio)
- **FFmpeg** — for audio capture (`sudo dnf install ffmpeg` on Fedora)
- Python 3.10+
- Git

### 2. Clone

```bash
git clone https://github.com/brchn6/1036playlistdashboard.git
cd 1036playlistdashboard
```

### 3. Configure token (for auto-push)

```bash
cp .env.example .env
# Edit .env and add your GitHub token:
#   GIT_TOKEN=ghp_...
```

### 4. Start all services

```bash
# Start all 7 proxies + updater daemon
bash scripts/manage.sh start

# Check status
bash scripts/manage.sh status
```

### 5. Generate data once (test)

```bash
python scripts/generate_data.py
```

## 📋 Commands

```bash
bash scripts/manage.sh start       # Start all proxies + updater
bash scripts/manage.sh stop        # Stop everything
bash scripts/manage.sh status      # Health check
bash scripts/manage.sh restart     # Stop + start
bash scripts/manage.sh generate    # Run data generator
bash scripts/manage.sh proxy start galgalatz  # Single station
bash scripts/manage.sh logs        # View logs
```

## 📻 Stations

| Station | Stream | Port |
|---------|--------|------|
| 🟢 קול השפלה 103FM | `radio.streamgates.net/stream/1036kh` | 8761 |
| 🔴 גלגלצ | `glzwizzlv.bynetcdn.com/glglz_mp3` | 8762 |
| 🔵 99FM | `99.livecdn.biz/99fm_aac` | 8763 |
| 🟡 רדיו תל אביב 102FM | `102.livecdn.biz/102fm_aac` | 8764 |
| 🟣 כאן 88 | `27953.live.streamtheworld.com/KAN_88.mp3` | 8765 |
| 🟠 כאן ב | `27953.live.streamtheworld.com/KAN_BET.mp3` | 8766 |
| 🆕 קול הגליל העליון | `radio.streamgates.net/stream/galil` | 8767 |

## 🔗 Related

- [dotX12/shazamio](https://github.com/dotX12/shazamio) — Python Shazam API wrapper
- [brchn6/radio-kol-hashfela](https://github.com/brchn6/radio-kol-hashfela) — Android/iOS app for Kol Hashfela

## 📝 License

Do whatever you want. Made for the love of radio.
