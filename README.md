# 🎧 1036 פלייליסט דשבורד — Kol Hashfela Playlist Dashboard

> **Automatic playlist tracker for [Radio Kol Hashfela 103.6FM](https://1036kh.com)**  
> מזהה שירים אוטומטית מהרדיו ושומר היסטוריית השמעה

A live dashboard that tracks every song played on **Kol Hashfela 103.6FM** (רדיו קול השפלה) using the [ShazamIO Proxy](https://github.com/brchn6/shazamio-proxy). The playback history is updated every 30 seconds and published to **GitHub Pages**.

## 🚀 How it works

```
┌─────────────────┐     poll /30s     ┌───────────────────┐
│  ShazamIO Proxy  │ ◄────────────── │  updater.py (local)│
│  (localhost:8765) │                  │  writes data       │
└─────────────────┘                  └────────┬──────────┘
       │                                       │
       │ Shazam API                             │ playlist.json
       ▼                                       ▼
┌─────────────────┐                  ┌───────────────────┐
│  Kol Hashfela    │                  │  docs/data/        │
│  103.6FM stream  │                  │  playlist.json     │
└─────────────────┘                  └────────┬──────────┘
                                              │ git push
                                              ▼
                                     ┌───────────────────┐
                                     │  GitHub Pages      │
                                     │  index.html        │
                                     └───────────────────┘
```

## 📦 Project structure

```
1036playlistdashboard/
├── docs/                        # GitHub Pages root
│   ├── index.html               # Dashboard page (RTL Hebrew)
│   └── data/
│       └── playlist.json        # Auto-generated playlist data
├── scripts/
│   └── updater.py               # Daemon: polls proxy every 30s
├── .github/workflows/
│   └── pages.yml                # Deploy to GitHub Pages
├── .gitignore
├── README.md
└── requirements.txt
```

## 🛠️ Setup

### 1. Prerequisites

- **ShazamIO Proxy** — running at `http://localhost:8765`  
  See [brchn6/shazamio-proxy](https://github.com/brchn6/shazamio-proxy)
- Python 3.10+ (stdlib only for the updater)
- Git

### 2. Clone & configure

```bash
git clone https://github.com/brchn6/1036playlistdashboard.git
cd 1036playlistdashboard
```

### 3. Run the updater

```bash
# One-shot test
python scripts/updater.py --once

# Continuous (polls every 30 seconds)
python scripts/updater.py

# Custom proxy URL
PROXY_URL=http://localhost:8765 python scripts/updater.py
```

### 4. Deploy to GitHub Pages

The **GitHub Actions workflow** (`.github/workflows/pages.yml`) handles deployment automatically. Every push to `main` deploys to Pages.

Or manually:

```bash
# Push to main — GHA deploys automatically
git add -A
git commit -m "update playlist"
git push
```

### 5. Run the updater as a service (systemd user service)

```bash
cp scripts/1036updater.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now 1036updater.service
# Check status:
systemctl --user status 1036updater.service
journalctl --user -u 1036updater.service -f
```

## 🔄 Auto-push on each new track

The updater can optionally commit & push to GitHub whenever a new track is detected:

```bash
GIT_PUSH=1 python scripts/updater.py
```

Or add this to the systemd service:
```
Environment=GIT_PUSH=1
```

## 📊 Dashboard features

- **Now Playing** — current song with artist, title, and Shazam link
- **Playlist History** — last 200 tracks with timestamps
- **Stats** — total tracks and unique artists
- **Auto-refresh** — polls `playlist.json` every 30 seconds
- **RTL Hebrew UI** — right-to-left layout
- **Dark theme** — matches the Kol Hashfela app aesthetic

## 🔗 Related projects

- [brchn6/shazamio-proxy](https://github.com/brchn6/shazamio-proxy) — Local Shazam recognition proxy
- [brchn6/radio-kol-hashfela](https://github.com/brchn6/radio-kol-hashfela) — Android/iOS app for Kol Hashfela
- [brchn6/brchn6.github.io](https://github.com/brchn6/brchn6.github.io) — Personal site

## 📝 License

Do whatever you want. Made for the love of radio.
