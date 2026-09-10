# Radio Playlist Selection - Research Baseline

> **Status:** Study phase. All approaches documented, none implemented yet.
> **Data:** 79K recognized tracks, 8 Israeli radio stations, 37 days (2026-07-13 to 2026-08-19).
> **Goal:** Find the "bulletproof" tracks for a given station + time slot - tracks that are
> representative of the station's musical identity, suitable for a curated Spotify playlist.

---

## The Problem

Given:
- A station (e.g. kol-hashfela 103.6FM)
- A time slot (e.g. morning 06:00-12:00)
- 37 days of Shazam recognition data (artist, title, timestamp)

Find:
- The most representative tracks for that station+slot
- Ranked by a score that captures "this station would definitely play this"

Two candidates were already explored:
1. **Frequency** - how many days does this track appear? (implemented)
2. **PageRank** - how central is this track in the co-occurrence graph? (implemented)

This document maps ALL analytical approaches before choosing what to implement next.

---

## Approach Catalog

### 1. Frequency Analysis (DONE)

**Question:** How often does this track appear in the time slot across days?

**Method:** Count unique days played, normalize by max days.

**Score:** `freq = days_played / max_days`

**Strengths:** Simple, interpretable, captures "staple" status.
**Weaknesses:** Ignores musical context. A track played 10 times in isolation scores same as one woven into the station's flow.

**Status:** Implemented in `playlist_selector.py`.

---

### 2. PageRank on Co-occurrence Graph (DONE)

**Question:** Which tracks are central hubs in the station's sequential play network?

**Method:** Build directed graph where edges = sequential plays (3-15 min gap). Run PageRank.

**Score:** `pagerank = normalized PageRank score`

**Strengths:** Captures musical context. Tracks surrounded by other popular tracks score higher.
**Weaknesses:** Undirected interpretation of a directed phenomenon. Doesn't capture "what comes next" probability.

**Status:** Implemented in `playlist_selector.py`.

---

### 3. Markov Chain Analysis

**Question:** If a listener just heard track A, what's the probability they hear track B next?

**Method:** Build transition matrix T where `T[A][B] = count(A->B) / count(A->*)`.

**What it reveals:**
- **Transition probabilities** - P(next track | current track)
- **Stationary distribution** - long-run play frequency (converges to same as PageRank)
- **Mixing time** - how quickly the station "forgets" what it just played
- **Hitting time** - expected steps to reach a specific track from any starting point
- **Absorbing states** - tracks that一旦entered, the station tends to stay in a cluster

**What to compute:**
```
- Transition matrix T (sparse, N x N where N = unique tracks)
- Stationary distribution pi (eigenvector of T^T with eigenvalue 1)
- Entropy H(node) = -sum(P(next) * log(P(next))) per node
  - Low entropy = deterministic DJ flow
  - High entropy = random/shuffled
- Mixing time = steps until distribution is within epsilon of stationary
```

**Application to playlist:**
- Simulate 1000 random walks of 30 steps each
- Score = frequency of appearance across all walks
- This captures "gravitational center" of the station's flow

**Dependencies:** None (pure stdlib).

**Estimated effort:** Small. The co-occurrence counts already exist. Normalize rows -> done.

---

### 4. Probability Graph (networkX-style, pure stdlib)

**Question:** Same as Markov, but represented as a weighted directed graph for graph algorithms.

**Method:** Take the transition matrix, represent as graph where edge weights = probabilities.

**What networkX algorithms apply:**
- **Community detection** (Louvain) - clusters of tracks that play together
- **Betweenness centrality** - tracks that bridge different clusters
- **Shortest path** - minimal transition chain between any two tracks
- **k-core decomposition** - innermost core of the station's identity
- **Maximal cliques** - groups where every track plays near every other
- **Random walks** - simulate listening sessions

**What to compute:**
```
- Graph G = directed, weighted by transition probabilities
- Communities = Louvain or greedy modularity
- Betweenness = which tracks bridge communities
- k-core = innermost dense subgraph (the station's DNA)
- Random walk stationarity = compare with raw frequency
```

**Application to playlist:**
- Pick top-K tracks from each community -> diverse representative playlist
- Pick tracks with high betweenness -> tracks that connect different moods
- Pick k-core tracks -> the absolute core identity

**Dependencies:** None (implement Louvain/communities from scratch, or use simple connected components).

**Estimated effort:** Medium. Graph algorithms from scratch are doable but need careful implementation.

---

### 5. Temporal Pattern Analysis

**Question:** Does the station play different music at different times? Are there "dayparts" with distinct playlists?

**Method:** Cluster tracks by their time-of-day distribution.

**What to compute:**
```
- For each track: histogram of play hours (24 bins)
- Cluster tracks by hour distribution (k-means or hierarchical)
- Result: "morning tracks", "afternoon tracks", "evening tracks"
- Within each cluster: frequency + co-occurrence scoring
```

**Application to playlist:**
- User selects time slot -> filter to that daypart's cluster
- Score within the cluster (not globally)
- More precise than raw hour filtering

**Dependencies:** None (k-means is simple).

**Estimated effort:** Medium.

---

### 6. Cross-Station Similarity

**Question:** Which stations are musically similar? Do they share "canonical" tracks?

**Method:** Compare track sets between stations using Jaccard similarity, cosine similarity on play-count vectors.

**What to compute:**
```
- For each station: vector of (track -> play_count)
- Jaccard(set_A, set_B) = |A ∩ B| / |A ∪ B|
- Cosine(vec_A, vec_B) = dot product / (|A| * |B|)
- Find "universal hits" = tracks played on 5+ stations
- Find "station signatures" = tracks unique to one station
```

**Application to playlist:**
- "Universal hits" = safe choices for any playlist
- "Station signatures" = what makes kol-hashfela different from galgalatz
- Cross-station co-occurrence = do the same sequences appear on multiple stations?

**Dependencies:** None.

**Estimated effort:** Small.

---

### 7. Audio Feature Analysis

**Question:** What does the station sound like? BPM, key, energy distributions.

**Method:** Use Shazam metadata (bpm, musical_key already in the mirror). Compute distributions per station+slot.

**What to compute:**
```
- BPM distribution per station (histogram, mean, std)
- Musical key distribution (circle of fifths heatmap)
- Energy proxy = plays per hour (high-energy stations play more)
- "Sonic fingerprint" = vector of (bpm_mean, bpm_std, key_entropy, ...)
- Find tracks that match the station's sonic profile
```

**Application to playlist:**
- Score tracks by how well they match the station's audio profile
- A track with BPM 120 on a station that averages BPM 110 = good fit
- A track in C major on a station that favors minor keys = poor fit

**Dependencies:** None (data already in mirror).

**Estimated effort:** Small.

---

### 8. Hidden Markov Model (HMM)

**Question:** Does the station have hidden states (mood segments) that determine what plays next?

**Method:** Assume the DJ switches between hidden moods (upbeat, mellow, nostalgic, etc.) and emits tracks from each mood.

**What to compute:**
```
- Hidden states = k moods (e.g. 3-5)
- Emission probabilities = P(track | mood)
- Transition probabilities = P(mood_next | mood_current)
- Baum-Welch to learn parameters
- Viterbi to decode most likely mood sequence
```

**Application to playlist:**
- Identify the mood sequence pattern
- Generate a playlist that follows the typical mood arc
- More sophisticated than raw Markov (captures latent structure)

**Dependencies:** None (HMM from scratch is ~100 lines).

**Estimated effort:** Large. HMM training is finicky.

---

### 9. Sequence Prediction (n-gram)

**Question:** What tracks tend to follow a given sequence of 2-3 tracks?

**Method:** Count n-grams (A->B->C patterns) in the play history.

**What to compute:**
```
- Bigram counts: P(B|A) (same as Markov)
- Trigram counts: P(C|A,B) - context-sensitive transitions
- Find common "triplets" = signature sequences of the station
```

**Application to playlist:**
- Build playlist by chaining trigrams
- More context-aware than Markov (considers previous 2 tracks, not just 1)

**Dependencies:** None.

**Estimated effort:** Small (just counting).

---

### 10. Collaborative Filtering (Station-to-Station)

**Question:** If station A and station B both play track X, do they also both play track Y?

**Method:** Treat stations as "users" and tracks as "items". Apply user-based collaborative filtering.

**What to compute:**
```
- Station-track matrix (8 stations x N tracks, values = play counts)
- Similarity between stations (cosine)
- For a target station: find similar stations, recommend their top tracks
- "If kol-hashfela is like 99fm, and 99fm plays X a lot, kol-hashfela should too"
```

**Application to playlist:**
- Augment station's own track list with recommendations from similar stations
- Useful for stations with less data (kan-bet has only 4K plays)

**Dependencies:** None.

**Estimated effort:** Small.

---

## Summary: What to Implement

| # | Approach | Effort | Novelty | Impact |
|---|----------|--------|---------|--------|
| 1 | Frequency | Done | Low | High |
| 2 | PageRank | Done | Medium | High |
| 3 | Markov chain | Small | Medium | High |
| 4 | Probability graph | Medium | High | High |
| 5 | Temporal patterns | Medium | Medium | Medium |
| 6 | Cross-station similarity | Small | Low | Medium |
| 7 | Audio features | Small | Medium | Medium |
| 8 | HMM | Large | High | Medium |
| 9 | N-gram sequences | Small | Low | Medium |
| 10 | Collaborative filtering | Small | Low | Medium |

**Recommended next implementations:**
1. **Markov chain** (3) - directly extends the existing co-occurrence work
2. **Probability graph** (4) - adds community detection and betweenness
3. **Audio features** (7) - orthogonal signal (what it sounds like, not when it plays)

---

## Data Available

| Field | Source | Notes |
|-------|--------|-------|
| artist | Shazam | |
| title | Shazam | |
| recognized_at | Collector | UTC timestamp, second precision |
| bpm | Shazam | ~80% of tracks |
| musical_key | Shazam | e.g. "B minor" |
| station_slug | Collector | 8 stations |
| isrc | Shazam | International recording ID |
| url | Shazam | Shazam page link |

**Derived (already computed):**
- Co-occurrence counts (pair_counts dict)
- PageRank scores (pr dict)
- Frequency scores (days played / max days)
- Combined scores (alpha * freq + (1-alpha) * pagerank)

---

## Files

| File | Purpose |
|------|---------|
| `scripts/playlist_selector.py` | Frequency + PageRank scoring engine |
| `scripts/export_graph.py` | Export co-occurrence graph for vis.js |
| `scripts/app.py` | Streamlit dashboard |
| `docs/playlist-explorer.html` | Static dashboard (legacy) |
| `docs/cooccurrence-graph.html` | Interactive graph visualization |
| `data/tracks_mirror.jsonl` | Local track cache (79K rows) |
| `data/playlist-candidates*.json` | Pre-computed ranked candidates |
| `data/graph-*.json` | Per-station graph data for vis.js |
