#!/usr/bin/env python3
"""Streamlit app for smart playlist candidate selection from radio airplay data.

Combines frequency (days played) with co-occurrence graph centrality (PageRank)
to surface the "bulletproof" tracks from Israeli radio stations.
"""
import json
import subprocess
import sys
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="Radio Playlist Selector", page_icon="📻", layout="wide")

from radio_utils import load_tracks, slot_filter, STATIONS, PROJECT

MIRROR = PROJECT / "data" / "tracks_mirror.jsonl"
SELECTOR = PROJECT / "scripts" / "playlist_selector.py"

# ---------------------------------------------------------------------------
# Cache the mirror data load
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def cached_load_tracks():
    return load_tracks(str(MIRROR))


# ---------------------------------------------------------------------------
# Analysis (runs the Python script, captures JSON)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def run_analysis(station, start_hour, end_hour, alpha, min_days, co_min, co_max, top_n):
    cmd = [
        sys.executable, str(SELECTOR),
        "--station", station,
        "--start-hour", str(start_hour),
        "--end-hour", str(end_hour),
        "--alpha", str(alpha),
        "--min-days", str(min_days),
        "--cooccurrence-min", str(co_min),
        "--cooccurrence-max", str(co_max),
        "--top", str(top_n),
        "--mirror", str(MIRROR),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        st.error(f"Analysis failed: {r.stderr[:500]}")
        return None
    return json.loads(r.stdout)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📻 Radio Playlist Selector")
st.caption("Frequency + co-occurrence graph ranking from Israeli radio airplay data")

# Sidebar controls
with st.sidebar:
    st.header("Parameters")

    station = st.selectbox(
        "Station",
        options=list(STATIONS.keys()),
        format_func=lambda s: STATIONS[s],
        index=0,
    )

    col1, col2 = st.columns(2)
    with col1:
        start_hour = st.number_input("Start (local)", 0, 23, 6)
    with col2:
        end_hour = st.number_input("End (local)", 0, 23, 22)

    alpha = st.slider(
        "Frequency vs Graph",
        0.0, 1.0, 0.5, 0.05,
        help="1.0 = pure frequency, 0.0 = pure PageRank graph centrality",
    )

    min_days = st.select_slider(
        "Min days in slot",
        options=[1, 2, 3, 5, 7, 10],
        value=2,
    )

    with st.expander("Advanced"):
        co_min = st.number_input("Co-occurrence min (min)", 1, 30, 3)
        co_max = st.number_input("Co-occurrence max (min)", 5, 60, 15)
        top_n = st.number_input("Max candidates", 10, 200, 50)

    st.divider()
    st.markdown(
        "**How it works:**\n"
        "- **Freq** = days played / max days\n"
        "- **Graph** = PageRank on sequential-play edges\n"
        "- **Score** = α·freq + (1-α)·graph"
    )

# Run analysis
with st.spinner("Analyzing..."):
    result = run_analysis(station, start_hour, end_hour, alpha, min_days, co_min, co_max, top_n)

if not result:
    st.stop()

candidates = result["candidates"]
stats = result["stats"]

# Stats bar
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Plays in slot", f"{stats['plays_in_slot']:,}")
c2.metric("Unique songs", f"{stats['unique_songs_in_slot']:,}")
c3.metric("Graph nodes", f"{stats['nodes_in_graph']:,}")
c4.metric("Graph edges", f"{stats['edges_in_graph']:,}")
c5.metric("Max days", stats["max_days_seen"])

# Build display DataFrame
import pandas as pd

rows = []
for c in candidates:
    nbrs = c.get("top_neighbors", [])
    nbr_str = ", ".join(f"{n['artist']} - {n['title']} ({n['plays_together']}x)" for n in nbrs[:3])
    rows.append({
        " ": False,  # checkbox placeholder
        "#": c["rank"],
        "Score": c["combined_score"],
        "Artist": c["artist"],
        "Title": c["title"],
        "Days": c["days"],
        "Plays": c["total_plays"],
        "Freq": c["freq_score"],
        "Graph": c["pr_score"],
        "Neighbors": nbr_str,
    })

df = pd.DataFrame(rows)

# Editable table with checkboxes
st.subheader(f"Candidates - {STATIONS[station]}")
st.caption(f"Time slot: {start_hour:02d}:00-{end_hour:02d}:00 (local Israel) | α = {alpha} | min {min_days} days")

edited = st.data_editor(
    df,
    column_config={
        " ": st.column_config.CheckboxColumn("Select", default=False),
        "#": st.column_config.NumberColumn("#", width="small"),
        "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=1, format="%.3f"),
        "Days": st.column_config.NumberColumn("Days", width="small"),
        "Plays": st.column_config.NumberColumn("Plays", width="small"),
        "Freq": st.column_config.ProgressColumn("Freq", min_value=0, max_value=1, format="%.2f"),
        "Graph": st.column_config.ProgressColumn("Graph", min_value=0, max_value=1, format="%.2f"),
        "Neighbors": st.column_config.TextColumn("Top Co-occurring", width="medium"),
    },
    use_container_width=True,
    hide_index=True,
    disabled=["#", "Score", "Artist", "Title", "Days", "Plays", "Freq", "Graph", "Neighbors"],
)

# Export selected
selected = edited[edited[" "]]
if len(selected) > 0:
    st.divider()
    st.subheader(f"Selected: {len(selected)} tracks")

    # Build export text
    export_lines = []
    for _, row in selected.iterrows():
        export_lines.append(f"{row['Title']} - {row['Artist']}")

    export_text = "\n".join(export_lines) + "\n"

    st.code(export_text, language=None)

    col1, col2 = st.columns([1, 3])
    with col1:
        st.download_button(
            "⬇ Export selected_tracks.txt",
            data=export_text,
            file_name="selected_tracks.txt",
            mime="text/plain",
            type="primary",
        )
    with col2:
        st.caption("Feed this file into `add-tracks.py` to add to Spotify via browser automation")
else:
    st.info("Check tracks above, then export to add them to your Spotify playlist.")
