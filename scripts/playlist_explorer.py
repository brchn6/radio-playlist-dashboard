#!/usr/bin/env python3
"""
Radio Playlist Explorer - Streamlit dashboard.
Shows hourly track periodicity, allows hour-range selection,
and builds playlists with Spotify/SoundCloud search links.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

# Add project scripts dir for supabase_db import
_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from supabase_db import SupabaseDB
from spotify_api import search_track, get_my_id, create_playlist, add_tracks_to_playlist

# --- Config ---
JINGLE_KEYWORDS = ["LukHash", "The Other Side"]  # artist/title substrings to drop
OUTLIER_THRESHOLD = 50  # total plays in period - likely station IDs

st.set_page_config(page_title="Radio Playlist Explorer", layout="wide", initial_sidebar_state="expanded")

# --- Data Loading ---
@st.cache_data(ttl=600, show_spinner="Loading tracks from database...")
def load_tracks(station: str, days: int) -> pd.DataFrame:
    """Load tracks from Postgres, convert to Israel time, drop jingles."""
    db = SupabaseDB()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    sql = """
        SELECT id, artist, title, recognized_at, bpm, musical_key
        FROM tracks
        WHERE station_slug = %s
          AND recognized_at >= %s
        ORDER BY recognized_at ASC
    """
    df = pd.read_sql(sql, db.conn, params=(station, cutoff))
    if df.empty:
        return df

    # Convert UTC to Israel time
    df["recognized_at"] = pd.to_datetime(df["recognized_at"], utc=True)
    df["hour"] = df["recognized_at"].dt.tz_convert("Asia/Jerusalem").dt.hour
    df["date"] = df["recognized_at"].dt.tz_convert("Asia/Jerusalem").dt.date
    df["day_name"] = df["recognized_at"].dt.tz_convert("Asia/Jerusalem").dt.day_name()
    df["weekday"] = df["recognized_at"].dt.tz_convert("Asia/Jerusalem").dt.weekday

    # Drop jingles: artist or title contains jingle keywords
    jingle_mask = df["artist"].str.contains("|".join(JINGLE_KEYWORDS), case=False, na=False) | \
                  df["title"].str.contains("|".join(JINGLE_KEYWORDS), case=False, na=False)
    df = df[~jingle_mask].copy()

    return df


def compute_track_stats(df: pd.DataFrame, min_hour: int, max_hour: int) -> pd.DataFrame:
    """Compute per-track stats for tracks played within the hour range."""
    # Filter to selected hours
    hour_mask = (df["hour"] >= min_hour) & (df["hour"] <= max_hour)
    filtered = df[hour_mask].copy()

    if filtered.empty:
        return pd.DataFrame()

    # Per-track stats
    stats = filtered.groupby(["artist", "title"]).agg(
        total_plays=("id", "count"),
        dates_played=("date", "nunique"),
        hours_played=("hour", lambda x: sorted(x.unique().tolist())),
        avg_bpm=("bpm", "mean"),
        first_seen=("recognized_at", "min"),
        last_seen=("recognized_at", "max"),
    ).reset_index()

    # Drop outliers (too many plays = likely station ID we missed)
    stats = stats[stats["total_plays"] <= OUTLIER_THRESHOLD]

    # Sort by total plays descending
    stats = stats.sort_values("total_plays", ascending=False).reset_index(drop=True)

    # Format hours for display
    stats["hours_str"] = stats["hours_played"].apply(
        lambda h: ", ".join(f"{x:02d}:00" for x in h) if h else ""
    )

    return stats


def build_heatmap_data(df: pd.DataFrame, top_n: int) -> tuple:
    """Build matrix for heatmap: top N tracks x hours, values = play count."""
    # First filter out outliers globally for heatmap
    artist_title = df["artist"] + " - " + df["title"]
    counts = artist_title.value_counts()
    counts = counts[counts <= OUTLIER_THRESHOLD]
    top_tracks = counts.head(top_n).index.tolist()

    # Filter df to top tracks
    df["track_label"] = df["artist"] + " - " + df["title"]
    df_top = df[df["track_label"].isin(top_tracks)].copy()

    # Pivot: tracks x hours
    matrix = df_top.groupby(["track_label", "hour"]).size().unstack(fill_value=0)

    # Ensure all 24 hours exist
    for h in range(24):
        if h not in matrix.columns:
            matrix[h] = 0
    matrix = matrix[sorted(matrix.columns)]

    # Reorder tracks by total plays (descending)
    matrix["_total"] = matrix.sum(axis=1)
    matrix = matrix.sort_values("_total", ascending=True)  # ascending for plotly (bottom=most)
    matrix = matrix.drop("_total", axis=1)

    return matrix


def make_spotify_url(artist: str, title: str) -> str:
    """Generate a Spotify search URL."""
    query = f"{artist} {title}"
    return f"https://open.spotify.com/search/{quote(query)}"


def make_soundcloud_url(artist: str, title: str) -> str:
    """Generate a SoundCloud search URL."""
    query = f"{artist} {title}"
    return f"https://soundcloud.com/search?q={quote(query)}"


# --- Main App ---
st.title("Radio Playlist Explorer")

# Sidebar
st.sidebar.header("Filters")

STATIONS = {
    "kol-hashfela": "קול השפלה 103.6FM",
    "galgalatz": "גלגלצ",
    "99fm": "99FM",
    "radio-tlv": "רדיו תל אביב 102FM",
    "kan-88": "כאן 88",
    "kan-bet": "כאן ב",
    "galil": "קול הגליל העליון",
    "radio-darom": "רדיו דרום 97FM",
}

station = st.sidebar.selectbox(
    "Station",
    list(STATIONS.keys()),
    format_func=lambda k: f"{STATIONS[k]} ({k})",
    index=0,
)

days_back = st.sidebar.slider("Days back", 7, 30, 21)

min_hour, max_hour = st.sidebar.slider(
    "Hour range (Israel time)",
    0, 23, (6, 23),
    help="Filter tracks to this hour range for the playlist builder"
)

top_n = st.sidebar.slider("Top tracks in heatmap", 10, 80, 40)

# Load
df = load_tracks(station, days_back)

if df.empty:
    st.warning("No tracks found for this station and period.")
    st.stop()

# --- Metrics ---
st.subheader("Overview")
col1, col2, col3, col4 = st.columns(4)

total = len(df)
unique = df.groupby(["artist", "title"]).ngroups
days_in_range = (df["date"].max() - df["date"].min()).days + 1
avg_per_day = total / max(days_in_range, 1)

col1.metric("Total plays", f"{total:,}")
col2.metric("Unique songs", f"{unique:,}")
col3.metric("Days", days_in_range)
col4.metric("Avg/day", f"{avg_per_day:.0f}")

# --- Hourly Periodicity Heatmap ---
st.subheader("Hourly Periodicity Heatmap")
st.caption("Which tracks play at which hours. Brighter = more plays. Top tracks by frequency shown.")

matrix = build_heatmap_data(df, top_n)

if matrix.empty:
    st.info("No data for heatmap with current filters.")
else:
    fig = go.Figure(data=go.Heatmap(
        z=matrix.values,
        x=[f"{h:02d}:00" for h in matrix.columns],
        y=matrix.index,
        colorscale="YlOrRd",
        colorbar=dict(title="Plays"),
        hovertemplate="Track: %{y}<br>Hour: %{x}<br>Plays: %{z}<extra></extra>",
    ))

    fig.update_layout(
        height=max(400, len(matrix) * 22 + 100),
        xaxis_title="Hour of Day (Israel time)",
        yaxis_title="",
        margin=dict(l=20, r=20, t=10, b=40),
        font=dict(size=13),
    )

    st.plotly_chart(fig, use_container_width=True)

# --- Hour Distribution ---
st.subheader("Hour Distribution")
st.caption("Total tracks played per hour across all days in the period.")

hour_counts = df.groupby("hour").size().reindex(range(24), fill_value=0)

fig_hours = go.Figure(data=go.Bar(
    x=[f"{h:02d}:00" for h in hour_counts.index],
    y=hour_counts.values,
    marker_color=["#ff6b6b" if min_hour <= h <= max_hour else "#95a5a6" for h in hour_counts.index],
    hovertemplate="Hour: %{x}<br>Tracks: %{y}<extra></extra>",
))

fig_hours.update_layout(
    height=300,
    xaxis_title="Hour of Day",
    yaxis_title="Total Tracks",
    margin=dict(l=20, r=20, t=10, b=40),
    font=dict(size=13),
    showlegend=False,
)

st.plotly_chart(fig_hours, use_container_width=True)

# --- Playlist Builder ---
st.subheader(f"Playlist Builder: {min_hour:02d}:00 - {max_hour:02d}:00")
st.caption("Tracks played during selected hours, sorted by frequency. Includes Spotify and SoundCloud links.")

playlist = compute_track_stats(df, min_hour, max_hour)

if playlist.empty:
    st.info("No tracks found for the selected hour range.")
else:
    # Add search URLs (raw, not markdown - LinkColumn handles display)
    playlist["Spotify"] = playlist.apply(
        lambda r: make_spotify_url(r["artist"], r["title"]), axis=1
    )
    playlist["SoundCloud"] = playlist.apply(
        lambda r: make_soundcloud_url(r["artist"], r["title"]), axis=1
    )

    # Format BPM
    playlist["bpm_display"] = playlist["avg_bpm"].apply(
        lambda x: f"{x:.0f}" if pd.notna(x) else "-"
    )

    # Display table with clickable links
    display_df = playlist[["artist", "title", "total_plays", "dates_played", "bpm_display", "hours_str", "Spotify", "SoundCloud"]].copy()
    display_df.columns = ["Artist", "Title", "Plays", "Days", "BPM", "Hours", "Spotify", "SoundCloud"]

    st.dataframe(
        display_df,
        use_container_width=True,
        height=min(600, len(display_df) * 35 + 40),
        hide_index=True,
        column_config={
            "Spotify": st.column_config.LinkColumn("Spotify", display_text="Open"),
            "SoundCloud": st.column_config.LinkColumn("SoundCloud", display_text="Open"),
        },
    )

    # Export
    st.subheader("Export Playlist")

    col_spotify, col_csv = st.columns(2)

    with col_spotify:
        st.markdown("**Create Spotify Playlist**")
        playlist_name = st.text_input(
            "Playlist name",
            value=f"{station} {min_hour:02d}:00-{max_hour:02d}:00 ({days_back}d)",
            key="spotify_pl_name",
        )

        if st.button("Create Playlist on Spotify", type="primary"):
            try:
                user_id = get_my_id()
                pl = create_playlist(user_id, playlist_name,
                    f"Radio playlist from {station} {min_hour:02d}:00-{max_hour:02d}:00, last {days_back} days")
                st.success(f"Created: [{pl['name']}]({pl['external_urls']['spotify']})")

                # Search and add tracks
                added = 0
                skipped = 0
                progress = st.progress(0, text="Searching Spotify...")
                track_uris = []
                for i, row in playlist.iterrows():
                    result = search_track(row["artist"], row["title"])
                    if result:
                        track_uris.append(result["uri"])
                        added += 1
                    else:
                        skipped += 1
                    progress.progress((i + 1) / len(playlist),
                        text=f"{i+1}/{len(playlist)} - added: {added}, skipped: {skipped}")

                if track_uris:
                    add_tracks_to_playlist(pl["id"], track_uris)
                progress.empty()
                st.success(f"Done! {added} tracks added, {skipped} not found on Spotify.")
                st.markdown(f"[Open in Spotify]({pl['external_urls']['spotify']})")
            except Exception as e:
                st.error(f"Spotify error: {e}")

    with col_csv:
        st.markdown("**Download CSV**")
        export_df = playlist[["artist", "title", "total_plays", "dates_played", "avg_bpm", "hours_str"]].copy()
        export_df["spotify_url"] = playlist.apply(
            lambda r: make_spotify_url(r["artist"], r["title"]), axis=1
        )
        export_df["soundcloud_url"] = playlist.apply(
            lambda r: make_soundcloud_url(r["artist"], r["title"]), axis=1
        )

        csv = export_df.to_csv(index=False)
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"playlist_{station}_{min_hour:02d}-{max_hour:02d}_{days_back}d.csv",
            mime="text/csv",
        )

    st.metric("Playlist length", f"{len(playlist)} unique songs")
