# Playlist Selector

Smart playlist candidate selection from Israeli radio airplay data.

## How it works

Combines two signals to find "bulletproof" tracks for a Spotify playlist:

1. **Frequency score** - How many days does this track appear in the time slot across the dataset? A track played every day for 3 weeks scores higher than a one-day wonder.

2. **Co-occurrence graph (PageRank)** - Tracks are connected by edges when they play sequentially within 3-15 minutes. PageRank finds tracks that are "hubs" in the station's musical flow - surrounded by other popular tracks. A track embedded in a strong neighborhood scores higher than an isolated one.

**Combined score** = `alpha * frequency + (1 - alpha) * pagerank`

Default alpha=0.5 balances both signals. Adjust the slider in the dashboard to favor frequency (closer to 1) or graph centrality (closer to 0).

## Files

| File | Purpose |
|------|---------|
| `scripts/playlist_selector.py` | Core analysis engine (stdlib only, no pip installs) |
| `docs/playlist-explorer.html` | Interactive dashboard |
| `docs/playlist-candidates-all.json` | Pre-computed candidates for all 8 stations |
| `scripts/add-tracks.py` | Spotify web player automation (chrome-devtools) |
| `scripts/login.py` | One-time Spotify login for the automation browser |

## Usage

### 1. Generate candidates

Run on head1:

```bash
cd ~/dev/radio-playlist-dashboard

# All stations, morning slot
python3 scripts/playlist_selector.py \
  --station kol-hashfela \
  --start-hour 6 --end-hour 12 \
  --alpha 0.5 --min-days 3 --top 50 \
  --output data/playlist-candidates.json

# Or regenerate for all stations
python3 scripts/playlist_selector.py --station kol-hashfela --start-hour 6 --end-hour 22 --alpha 0.5 --min-days 2 --top 30 --output /tmp/kol.json
python3 scripts/playlist_selector.py --station kan-88 --start-hour 6 --end-hour 22 --alpha 0.5 --min-days 2 --top 30 --output /tmp/kan.json
# ... etc for each station
```

### 2. Browse and select

Open `docs/playlist-explorer.html` in a browser (or serve via GitHub Pages at `brchn6.github.io/radio-playlist-dashboard/playlist-explorer.html`).

- Pick a station, time range, and min-days
- Adjust the alpha slider (frequency vs graph balance)
- Check the tracks you want
- Click "Export Selected" - downloads `selected_tracks.txt`

### 3. Add to Spotify

```bash
# One-time login (visible browser, 30s)
python3 scripts/login.py

# Add tracks (requires logged-in session)
python3 scripts/add-tracks.py <PLAYLIST_ID> selected_tracks.txt
```

## Data pipeline

```
8 Shazam proxies (8761-8768)  →  Collector (every 20s)  →  Supabase Postgres
                                                              ↓
                                                    tracks_mirror.jsonl (local cache)
                                                              ↓
                                                    playlist_selector.py
                                                              ↓
                                                    playlist-candidates-all.json
                                                              ↓
                                                    playlist-explorer.html (dashboard)
                                                              ↓
                                                    selected_tracks.txt
                                                              ↓
                                                    add-tracks.py (Spotify automation)
```

## Options

```
--station SLUG       Station slug (kol-hashfela, kan-88, 99fm, galgalatz, etc.)
--start-hour HH      Local Israel hour to start (0-23, default: 0)
--end-hour HH        Local Israel hour to end (0-23, default: 23)
--alpha FLOAT        Frequency weight: 0=graph only, 1=frequency only (default: 0.5)
--min-days N         Minimum days a track must appear (default: 2)
--cooccurrence-min   Min gap between plays (minutes, default: 3)
--cooccurrence-max   Max gap between plays (minutes, default: 15)
--top N              Number of top tracks to output (default: 50)
--mirror PATH        Path to tracks_mirror.jsonl
--output PATH        Output JSON (default: stdout)
```
