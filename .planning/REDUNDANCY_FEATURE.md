# Redundancy / "רעננו את הפלייליסט" Feature Plan

## Concept

Score each station on how repetitive its playlist is. The core mission of the project — proving which stations play the same songs on a loop.

---

## Metrics (per station, time-windowed)

### 1. Repeat Ratio — `redundancy_score`

```
repeat_plays = total_tracks - unique_tracks
redundancy   = repeat_plays / total_tracks
```

Higher = worse. A station that repeats every 3rd song = 33%.

### 2. Song Rotation — `unique_in_window`

Number of unique (artist + title) combos in the last N tracks (e.g., last 100).

Lower = worse. A station with only 40 unique songs out of 100 plays is clearly cycling.

### 3. Average Repeat Gap — `avg_repeat_interval_minutes`

For songs that appear ≥2 times: average time between consecutive plays.

Lower = worse. If "Song X" comes back every 90 minutes, the playlist is tiny.

### 4. Top Offenders — `most_repeated[]`

The specific songs that repeat most. Proof in the data.

### 5. Station Composite Score — `stale_score`

Combine into 0-100 scale (100 = most stale/redundant):

```python
stale_score = (
    redundancy_pct * 0.4 +
    (1 - unique_ratio) * 100 * 0.3 +
    normalized_gap * 0.3
)
```

---

## Implementation plan

### Phase 1 — DB queries (already partially exists)

Add a method to `db.py`:

```python
def get_redundancy_stats(self, station_id: int, window: int = 200) -> dict:
    """
    Returns:
      total_tracks, unique_tracks, repeat_plays, redundancy_pct,
      avg_repeat_interval_minutes, most_repeated[{song, count}],
      stale_score
    """
```

### Phase 2 — Static data

Add `redundancy.json` to `generate_data.py`:

```json
{
  "stations": [
    {
      "station_id": 1,
      "station_name": "קול השפלה 103FM",
      "station_slug": "kol-hashfela",
      "total_tracks": 200,
      "unique_tracks": 180,
      "repeat_plays": 20,
      "redundancy_pct": 10.0,
      "avg_repeat_gap_min": 185,
      "stale_score": 15.2,
      "top_repeats": [
        {"artist": "Stevie Wonder", "title": "Part-Time Lover", "plays": 3},
        ...
      ]
    }
  ],
  "updated_at": "..."
}
```

### Phase 3 — Dashboard tab

New HTML page / tab: **"רעננו את הפלייליסט"** or **"Redundancy"**

Layout:
1. **Header** — "איזה תחנות חוזרות על עצמן?" (Which stations repeat themselves?)
2. **Station ranking cards** — sorted by `stale_score` descending (worst first)
   - Color-coded: red (bad) → yellow → green (good)
   - Show score, repeat %, most repeated songs
3. **"The proof"** — expandable list of top offenders per station

---

## Current state (after ~4 hours of data)

| Station | Tracks | Repeats | Repeat % | Gap |
|---------|:------:|:-------:|:--------:|:---:|
| kol-hashfela | 66 | 1 | 1.5% | 185m |
| galgalatz | 53 | 0 | 0% | — |
| 99fm | 46 | 0 | 0% | — |
| kan-88 | 47 | 0 | 0% | — |
| radio-tlv | 19 | 0 | 0% | — |
| galil | 54 | 0 | 0% | — |
| radio-darom | 19 | 0 | 0% | — |
| kan-bet | 1 | 0 | 0% | — |

Too early to see strong patterns — but the data will accumulate fast with 20s polling (~180 tracks/station/day).

---

## Future ideas

- **Redundancy over time** chart — per station, track weekly trend
- **Cross-station redundancy** — which songs repeat ACROSS stations
- **"Freshness index"** — date of oldest unique song still in rotation
