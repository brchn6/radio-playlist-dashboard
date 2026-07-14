# Spotify Export — Planning Session

## 🎯 Vision

Allow users to grab the history of tracks played on any station and export it
into a Spotify playlist. Currently, Spotify's radio-station updated playlists
are chronological forward — if you start listening at 6 AM, you get tracks
from 3–6 AM, not the morning unfold forward. By letting users **select the
time range themselves and flip the order**, this app gives them control over
their listening experience.

## High-Level Flow

```
User selects station + time range
        │
        ▼
Dashboard shows filtered track list
   (with checkboxes for selection)
        │
        ▼
"Export to Spotify" button
        │
        ├── Phase 1 (no API key) ──► Generate link list:
        │     "Open each track in Spotify" / copy-paste list
        │
        └── Phase 2 (with Spotify API) ──► Create actual playlist:
              Authenticate → Create playlist → Add tracks → Done
```

## The Problem This Solves

The user describes:
> *"Imagine that now it's 6 AM. If I'm starting to listen to the music, I
> will basically go back all the way to 5, 4, and 3 AM because this is how
> the playlist is organized. But if I will be able to flip it in my own app
> and then insert it straight into Spotify — that could be cool."*

So the key value prop is:

1. **Reversal control** — Spotify playlists go backwards (newest first). Our
   export should optionally reverse to chronological order (oldest first).
2. **Time-range selection** — Pick your listening window, not the last N tracks.
3. **Deduplication** — A song that played 3 times in your window should appear
   once in the playlist (or let the user choose).
4. **Per-station or cross-station** — Tracks from one station, or a mix.

---

## Phase 1 — "Search in Spotify" Link (Minimal, No API Key)

### Goal
Add a per-track button in the History tab that opens Spotify search for that
track. Zero setup, zero auth, instant value.

### Where
- **History tab** — each row gets a 🎧 spotify icon button
- **Now Playing tab** — each station card gets a 🎧 button
- **Top Artists/Songs tab** — each entry gets a 🎧 button

### Implementation
```html
<a href="https://open.spotify.com/search/{artist} {title}"
   target="_blank" title="חיפוש בספוטיפיי"
   class="np-btn spotify-btn">🎧 Spotify</a>
```

URL-encode the artist+title search query. Opens Spotify web/desktop search
results for that track. The user clicks to find the track and can add it to
their own playlist manually.

### What's needed
- [ ] Add `.spotify-btn` CSS style (green accent, consistent with existing design)
- [ ] Add button to `renderHistory()` — one per history row
- [ ] Add button to `renderNowPlaying()` — one per station card
- [ ] Add button to `renderTopArtists()` / `renderTopSongs()` — one per entry
- [ ] Helper function: `spotifySearchUrl(artist, title)`
- [ ] No server-side changes, no new env vars

### Limitations
- User lands on Spotify search results, not directly on the track page.
  Spotify doesn't allow direct track links without a track ID.
- Manual action per track — no bulk export.

---

## Phase 2 — Bulk Playlist Builder (UI in Dashboard, Backend on Server)

### Goal
Add a "Build Spotify Playlist" interface in the History tab:
1. Filter by station + time range
2. Select tracks (all or individual)
3. Copy track list (text) to clipboard
4. Or generate a "deep link" list for easy manual playlist creation

### UI Changes (dashboard only, no server)
```
┌─────────────────────────────────────────────┐
│  📋 היסטוריית השמעה                          │
│  ┌─────────────────────────────────────────┐ │
│  │  Station: [▼ All / Per-station]         │ │
│  │  From: [date picker]  To: [date picker] │ │
│  │  ☐ Reversed (oldest first)              │ │
│  │  ☐ Deduplicate                          │ │
│  │  [📋 Copy as text] [🎵 Open in Spotify] │ │
│  └─────────────────────────────────────────┘ │
│  ☑ Track 1 — Artist — Title — time          │
│  ☑ Track 2 — Artist — Title — time          │
│  ...                                         │
└─────────────────────────────────────────────┘
```

### Copy-as-text format
```
Artist — Title
Artist — Title
...
```
Easy to paste into Spotify search or a note app for manual playlist building.

### What's needed
- [ ] Filter bar UI (station, date range, reverse toggle, dedup toggle)
- [ ] Checkbox selection for each track in history
- [ ] "Select All" / "Deselect All" controls
- [ ] Copy-to-clipboard button with formatted text
- [ ] "Open All in Spotify" button (opens multiple search tabs — browser
      popup blocker consideration: open sequentially or one at a time)

---

## Phase 3 — Full Spotify API Integration (Requires API Key)

### Goal
One-click "Create Spotify Playlist" that pushes selected tracks straight into
the user's Spotify account.

### Architecture

```
Browser (Dashboard)
    │  Click "Create Playlist"
    │
    ▼
Local Flask/FastAPI endpoint (or proxy sidecar)
    │  POST /api/spotify/create-playlist
    │
    ▼
Spotify API
    │  POST /v1/playlists/{id}/tracks
    │
    ▼
Playlist created in user's Spotify account
```

### Data flow

```
1. User authenticates with Spotify (OAuth 2.0)
   └── Redirect to Spotify → user approves → returns access token

2. User selects tracks in dashboard → clicks "Create Playlist"

3. Backend resolves each track to a Spotify track ID:
   GET /v1/search?q=artist+title&type=track&limit=1
   └── Matches by artist + title, picks top result

4. Backend creates playlist:
   POST /v1/users/{user_id}/playlists
   { "name": "קול השפלה — 2026-07-13 Morning", "public": false }

5. Backend adds tracks:
   POST /v1/playlists/{playlist_id}/tracks
   { "uris": ["spotify:track:...", ...] }

6. ✅ Done — playlist appears in user's Spotify account
```

### Track ID resolution challenges

| Problem | Mitigation |
|---------|-----------|
| Artist/title string mismatch (Hebrew, live versions, remixes) | Try Shazam track ID → Spotify ISRC mapping if available |
| Multiple matches | Pick first result; flag unmatched tracks in response |
| Rate limiting | Batch up to 100 tracks per API call; respect 429 retry-after |
| Hebrew/UTF-8 | Use exact encoding; Spotify API handles Unicode well |

### Backend requirements

- **New endpoint(s)** in existing server or new sidecar
  - `GET /api/spotify/auth` — start OAuth flow
  - `GET /api/spotify/callback` — OAuth callback, store token
  - `POST /api/spotify/create-playlist` — create + populate playlist
- **Token storage** — in-memory session or SQLite `meta` table
- **Dependencies** — `requests` or `httpx` for Spotify API calls

### Env vars to add to `.env`
```
SPOTIFY_CLIENT_ID=xxx
SPOTIFY_CLIENT_SECRET=xxx
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8760/api/spotify/callback
```

### Potential backend sidecar
Given the existing services (8 proxies + updater), the Spotify API
integration could be a **small standalone service** (port 8760 or 8770)
that handles OAuth and playlist creation. This keeps it decoupled from
the collector/updater daemon.

```
spotify_api.py  (port 8760)
    │  - OAuth with Spotify
    │  - Track search/resolve
    │  - Playlist create + populate
    │
    ▼
Dashboard JS fetches from http://127.0.0.1:8760/api/...
```

---

## Phase 4 — Advanced: Smart Playlist Curation

### Ideas for future iteration

1. **Cross-station mixes** — Combine tracks from multiple stations into one
   playlist

2. **"Today's highlights" auto-playlist** — Every morning, auto-create a
   Spotify playlist of yesterday's best tracks (by play count or rating)

3. **Genre tagging** — Use Spotify audio features (tempo, energy, danceability)
   to categorize tracks

4. **Smart dedup** — If the same song appears in multiple time slots, keep
   only the best version (by audio quality or length)

5. **Scheduled export** — Daily/weekly cron that creates a playlist
   automatically

6. **Exclusion rules** — Skip tracks the user has marked as "don't add this"

---

## Implementation Priority

```
Phase 1 — "Open in Spotify" link
  ├── Low effort, instant value
  ├── No server changes, no API key needed
  └── ~1 hour of coding

Phase 2 — Bulk playlist builder
  ├── Medium effort
  ├── Dashboard-only changes (no server)
  ├── Copy-to-clipboard workflow
  └── ~2-3 hours of coding

Phase 3 — Full Spotify API integration
  ├── High effort
  ├── Requires Spotify Developer account + API key
  ├── New backend service needed
  └── ~4-6 hours of coding

Phase 4 — Smart curation
  ├── Experimental
  └── Only after Phase 3 is stable
```

---

## Key Design Decisions

### 1. Order reversal
**Default = newest first** (matches current History display).
Toggle option flips to **oldest first** (chronological playback order).

### 2. Deduplication
**On by default**. When enabled, only the first occurrence of each
(artist, title) pair is kept. User can toggle off for full history.

### 3. Track selection granularity
- **All tracks** in time range (fast)
- **Per-track checkbox** (flexible but slower UX)
- **"Select top N"** by play count

### 4. Spotify link target
- `open.spotify.com/search/{query}` — always works, no auth
- `api.spotify.com/v1/search` — needs auth, returns actual track URIs

### 5. OAuth session persistence
For Phase 3, storing the Spotify access token in the browser
(`sessionStorage`) is simplest. For longer sessions, store in the
backend's `meta` table with an expiry timestamp and auto-refresh.

---

## Open Questions / Risks

| Question | Notes |
|----------|-------|
| **Spotify search accuracy** | Hebrew tracks, live versions, "(Radio Edit)" suffixes may cause mismatches. Need a fallback display of "best guess" vs "confirmed" |
| **Rate limits** | Spotify API: 429 after too many requests. Batch adds of 100 tracks per call helps |
| **OAuth UX** | Browser popup or redirect? On a headless server, the user needs a browser to approve |
| **Token refresh** | Access tokens expire after 1 hour. Refresh token needed for long-lived sessions |
| **Concurrent users** | Only 1 user (dashboard owner) for now. Cookie/session not needed |
| **Playlist visibility** | Default to `public: false` (private) to avoid cluttering the user's profile |
| **Track not found on Spotify** | Some niche tracks (especially Israeli radio) may not be on Spotify. Show in response as "not found" |

---

## Milestone Tracking

### Milestone 1: 🎧 Open in Spotify link ✅
- [x] Helper function `spotifySearchUrl()` added to dashboard JS
- [x] Spotify button in History rows
- [x] Spotify button in Now Playing cards
- [x] Spotify button in Top Songs/Artists
- [x] CSS styling for `.spotify-link`

### Milestone 2: 📋 Bulk playlist builder ✅
- [x] Filter bar (date, reverse, dedup) above History tab
- [x] Checkbox per track row
- [x] Select All / Deselect All
- [x] Copy selected tracks as formatted text to clipboard
- [x] "Open All in Spotify" button (opens each in a new tab)

### Milestone 3: 🔌 Spotify API integration ✅
- [x] Create `scripts/spotify_api.py` (standalone service on port 9900)
- [x] Spotify Developer app created → client ID added to `.env`
- [x] OAuth flow: auth → callback → token storage (in-memory)
- [x] `POST /create-playlist` endpoint with batch track resolution
- [x] Track search/resolve via Spotify search API (built into create-playlist)
- [x] Dashboard JS calls local API (graceful fallback when service is down)
- [x] Error handling: unmatched tracks, token expiry with auto-refresh
- [x] `SPOTIFY_CLIENT_SECRET` added to `.env`

### Milestone 4: 🧠 Smart curation
- [ ] Cross-station mix option (already works via "All" station pill)
- [ ] Auto-playlist by time-of-day pattern
- [ ] Exclusion rules UI
- [ ] Scheduled export cron
