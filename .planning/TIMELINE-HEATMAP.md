# Timeline & Heatmap — Planning Session

## Current State

| View | Data Source | Time Window | What It Counts |
|------|------------|-------------|----------------|
| **Timeline** | `timeline.json` points | Last 48h, user picks a day | Total plays per hour for that day |
| **Heatmap: Station×Hour** | `heatmap.json` station_hour matrix | Last 7 days (raw totals) | Total plays per hour over 7 days |
| **Heatmap: Day×Hour** | `heatmap.json` dow_hour matrix | Last 30 days (raw totals) | Total plays per hour×day over 30 days |
| **Heatmap: Bar chart** | Same station_hour matrix | Last 7 days | Total plays summed across stations |

## The Problem

The numbers **don't match** between timeline and heatmap because they cover different time windows:

- Timeline: 1 day → e.g., 16 plays in hour X
- Heatmap: 7 days → e.g., 42 plays in hour X

42 across 7 days = 6/day average, but 16 ≠ 6 because today might not be an average day.

## Options

### Option A: Both show per-day averages (already tried, user rejected)
- Timeline stays as-is (1 day)
- Heatmap divides by days → "daily average"
- Problem: rounded values, today ≠ average day, confusing

### Option B: Both show raw totals, clearly labeled
- Timeline: "סך שירים ביום שנבחר" (total on selected day)
- Heatmap: "סך שירים ב-7 הימים האחרונים" (total over last 7 days)
- Clear labels so user understands the difference

### Option C: Timeline removed (user suggested it)
- Heatmap already covers station×hour
- Timeline adds per-day drill-down
- Maybe merge timeline into heatmap as a day-picker?

## What Each Tab Actually Needs To Do

**Timeline tab:**
- Pick a day → show grid of stations × 24 hours
- Each cell = total plays (count, not unique)
- Click a cell → list of songs that played
- No zoom levels, no colors on song list

**Heatmap tab:**
- Station × Hour heatmap: which hours are busiest per station
- Day-of-week × Hour heatmap: which days are busy overall
- Hour bar chart: distribution across the day
- Non-music heatmap: minutes of talk/ads (already labeled as minutes)

## Decision Needed

1. Do we keep the timeline tab? If yes:
   - Does it show the same metric as the heatmap?
   - Or clearly labeled as different?

2. Heatmap: raw totals or per-day averages?
   - Raw totals = straightforward, bigger numbers
   - Per-day = comparable to timeline, but rounded

3. If per-day: should the backend generate it (cleaner) or frontend divide it (hacky)?

## My Recommendation

Keep both tabs. Use **Option B**:
- Timeline = raw totals for selected day (what I had before)
- Heatmap = raw totals over 7/30 days (what it was originally)
- **Clear labels** so it's obvious they're different windows
- No dividing, no rounding, no hacks
