#!/usr/bin/env python3
"""
SQLite database for 1036 Playlist Dashboard — Multi-station.

Schema:
  stations  — registry of radio stations (slug, name, stream_url, proxy_port, color)
  tracks    — recognized songs with station_id FK
  meta      — key-value store
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "playlist.db"

# ── Station registry ───────────────────────────────────────────────────
STATIONS_CONFIG: list[dict[str, Any]] = [
    {"slug": "kol-hashfela", "name": "קול השפלה 103FM",  "stream_url": "https://radio.streamgates.net/stream/1036kh",   "proxy_port": 8761, "color": "#6ae3c1"},
    {"slug": "galgalatz",    "name": "גלגלצ",             "stream_url": "https://glzwizzlv.bynetcdn.com/glglz_mp3",       "proxy_port": 8762, "color": "#e36a6a"},
    {"slug": "99fm",         "name": "99FM",              "stream_url": "https://99.livecdn.biz/99fm_aac",                "proxy_port": 8763, "color": "#6ab8e3"},
    {"slug": "radio-tlv",    "name": "רדיו תל אביב 102FM", "stream_url": "https://102.livecdn.biz/102fm_aac",            "proxy_port": 8764, "color": "#e3c86a"},
    {"slug": "kan-88",       "name": "כאן 88",            "stream_url": "https://27953.live.streamtheworld.com/KAN_88.mp3", "proxy_port": 8765, "color": "#c86ae3"},
    {"slug": "kan-bet",      "name": "כאן ב",             "stream_url": "https://27953.live.streamtheworld.com/KAN_BET.mp3", "proxy_port": 8766, "color": "#e38a6a"},
    {"slug": "galil",        "name": "קול הגליל העליון",   "stream_url": "https://radio.streamgates.net/stream/galil",    "proxy_port": 8767, "color": "#a06ae3"},
]

STATIONS_BY_SLUG = {s["slug"]: s for s in STATIONS_CONFIG}
STATIONS_BY_PORT = {s["proxy_port"]: s for s in STATIONS_CONFIG}


class PlaylistDB:
    """SQLite-backed playlist history store for multiple stations."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
            self._seed_stations()
        return self._conn

    # ── Schema ────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        # Station table + tracks without station_id (for fresh DBs)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS stations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                slug        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                stream_url  TEXT NOT NULL,
                proxy_port  INTEGER NOT NULL UNIQUE,
                color       TEXT DEFAULT '#6ae3c1',
                enabled     INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tracks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                station_id    INTEGER REFERENCES stations(id),
                artist        TEXT NOT NULL,
                title         TEXT NOT NULL,
                text          TEXT,
                url           TEXT,
                shazam_key    TEXT,
                recognized_at TEXT NOT NULL,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_recognized_at ON tracks(recognized_at);
            CREATE INDEX IF NOT EXISTS idx_tracks_shazam_key ON tracks(shazam_key);
            CREATE INDEX IF NOT EXISTS idx_tracks_artist_title ON tracks(artist, title);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

        # Migration: add station_id + indexes if this is an old single-station DB
        cur = self.conn.execute("PRAGMA table_info(tracks)")
        cols = {r["name"] for r in cur.fetchall()}
        if "station_id" not in cols:
            self.conn.execute("ALTER TABLE tracks ADD COLUMN station_id INTEGER REFERENCES stations(id)")
        # Create indexes safely (IF NOT EXISTS on indexes requires separate ALTER TABLE check)
        existing_idx = {r["name"] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_tracks_station_id ON tracks(station_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_station_recog ON tracks(station_id, recognized_at)",
        ]:
            try:
                self.conn.execute(idx_sql)
            except sqlite3.OperationalError:
                pass
        # Existing tracks without station get station_id = 1 (kol-hashfela)
        self.conn.execute("UPDATE tracks SET station_id = 1 WHERE station_id IS NULL")
        self.conn.commit()

    def _seed_stations(self) -> None:
        """Ensure all configured stations exist in the DB."""
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM stations")
        if cur.fetchone()["cnt"] == 0:
            for s in STATIONS_CONFIG:
                self.conn.execute(
                    """INSERT INTO stations (slug, name, stream_url, proxy_port, color)
                       VALUES (?, ?, ?, ?, ?)""",
                    (s["slug"], s["name"], s["stream_url"], s["proxy_port"], s["color"]),
                )
            self.conn.commit()

    # ── Stations ───────────────────────────────────────────────────────

    def get_stations(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        """Get all stations."""
        sql = "SELECT * FROM stations"
        params: list[Any] = []
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id ASC"
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def get_station_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Get a single station by slug."""
        cur = self.conn.execute("SELECT * FROM stations WHERE slug = ?", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_station_by_port(self, port: int) -> dict[str, Any] | None:
        """Get a single station by proxy port."""
        cur = self.conn.execute("SELECT * FROM stations WHERE proxy_port = ?", (port,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_station_id_by_port(self, port: int) -> int | None:
        """Resolve station ID from proxy port."""
        cur = self.conn.execute("SELECT id FROM stations WHERE proxy_port = ?", (port,))
        row = cur.fetchone()
        return row["id"] if row else None

    # ── Tracks ─────────────────────────────────────────────────────────

    def insert_track(self, station_id: int, artist: str, title: str,
                     text: str = "", url: str = "", shazam_key: str = "",
                     recognized_at: str = "") -> int:
        """Insert a track for a specific station. Returns row ID."""
        cur = self.conn.execute(
            """INSERT INTO tracks (station_id, artist, title, text, url, shazam_key, recognized_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (station_id, artist, title, text, url, shazam_key, recognized_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def track_exists(self, station_id: int, shazam_key: str,
                     artist: str, title: str) -> bool:
        """Check if a track was already recorded for this station.
        Matches by shazam_key first, falls back to artist+title."""
        if shazam_key:
            cur = self.conn.execute(
                "SELECT 1 FROM tracks WHERE station_id = ? AND shazam_key = ? LIMIT 1",
                (station_id, shazam_key),
            )
            if cur.fetchone():
                return True
        cur = self.conn.execute(
            """SELECT 1 FROM tracks
               WHERE station_id = ? AND LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?)
               LIMIT 1""",
            (station_id, artist.strip(), title.strip()),
        )
        return cur.fetchone() is not None

    def get_latest_track(self, station_id: int | None = None
                         ) -> dict[str, Any] | None:
        """Get the most recently recognized track, optionally by station."""
        if station_id:
            cur = self.conn.execute(
                """SELECT t.*, s.slug as station_slug, s.name as station_name, s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   WHERE t.station_id = ?
                   ORDER BY t.recognized_at DESC LIMIT 1""",
                (station_id,),
            )
        else:
            cur = self.conn.execute(
                """SELECT t.*, s.slug as station_slug, s.name as station_name, s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   ORDER BY t.recognized_at DESC LIMIT 1""",
            )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_all_current_tracks(self) -> list[dict[str, Any]]:
        """Get the latest track for EACH station (for multi-station display)."""
        cur = self.conn.execute(
            """SELECT t.*, s.slug as station_slug, s.name as station_name, s.color as station_color
               FROM tracks t
               JOIN stations s ON s.id = t.station_id
               WHERE t.id IN (
                   SELECT MAX(id) FROM tracks GROUP BY station_id
               )
               ORDER BY s.id ASC"""
        )
        return [dict(r) for r in cur.fetchall()]

    def get_history(self, station_id: int | None = None,
                    limit: int = 200, offset: int = 0
                    ) -> list[dict[str, Any]]:
        """Get track history, newest first. Optionally filtered by station."""
        if station_id:
            cur = self.conn.execute(
                """SELECT t.*, s.slug as station_slug, s.name as station_name, s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   WHERE t.station_id = ?
                   ORDER BY t.recognized_at DESC LIMIT ? OFFSET ?""",
                (station_id, limit, offset),
            )
        else:
            cur = self.conn.execute(
                """SELECT t.*, s.slug as station_slug, s.name as station_name, s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   ORDER BY t.recognized_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        return [dict(r) for r in cur.fetchall()]

    def get_hype_tracks(self, station_id: int | None = None,
                        min_count: int = 1, limit: int = 50
                        ) -> list[dict[str, Any]]:
        """Most frequently played tracks, optionally by station."""
        if station_id:
            cur = self.conn.execute(
                """SELECT t.artist, t.title, t.text, COUNT(*) as play_count,
                          MIN(t.recognized_at) as first_seen,
                          MAX(t.recognized_at) as last_seen,
                          s.slug as station_slug, s.name as station_name, s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   WHERE t.station_id = ?
                   GROUP BY LOWER(t.artist), LOWER(t.title)
                   HAVING play_count >= ?
                   ORDER BY play_count DESC LIMIT ?""",
                (station_id, min_count, limit),
            )
        else:
            cur = self.conn.execute(
                """SELECT t.artist, t.title, t.text, COUNT(*) as play_count,
                          MIN(t.recognized_at) as first_seen,
                          MAX(t.recognized_at) as last_seen
                   FROM tracks t
                   GROUP BY LOWER(t.artist), LOWER(t.title)
                   HAVING play_count >= ?
                   ORDER BY play_count DESC LIMIT ?""",
                (min_count, limit),
            )
        return [dict(r) for r in cur.fetchall()]

    def get_cross_station_tracks(self, min_stations: int = 2, limit: int = 30
                                  ) -> list[dict[str, Any]]:
        """Tracks that played on multiple stations (correlation)."""
        cur = self.conn.execute(
            """SELECT t.artist, t.title, t.text,
                      COUNT(DISTINCT t.station_id) as station_count,
                      GROUP_CONCAT(DISTINCT s.name, ' | ') as station_names,
                      GROUP_CONCAT(DISTINCT s.slug, ',') as station_slugs,
                      COUNT(*) as total_plays,
                      MIN(t.recognized_at) as first_seen,
                      MAX(t.recognized_at) as last_seen
               FROM tracks t
               JOIN stations s ON s.id = t.station_id
               GROUP BY LOWER(t.artist), LOWER(t.title)
               HAVING station_count >= ?
               ORDER BY station_count DESC, total_plays DESC
               LIMIT ?""",
            (min_stations, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_scatter_data(self, station_id: int | None = None
                          ) -> list[dict[str, Any]]:
        """Time-based data for scatterplot."""
        if station_id:
            cur = self.conn.execute(
                """SELECT t.artist, t.title, t.text, t.recognized_at,
                          s.slug as station_slug, s.color as station_color,
                          strftime('%w', t.recognized_at) as day_of_week,
                          strftime('%H', t.recognized_at) as hour,
                          strftime('%Y-%m-%d', t.recognized_at) as date
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   WHERE t.station_id = ?
                   ORDER BY t.recognized_at ASC""",
                (station_id,),
            )
        else:
            cur = self.conn.execute(
                """SELECT t.artist, t.title, t.text, t.recognized_at,
                          s.slug as station_slug, s.color as station_color,
                          strftime('%w', t.recognized_at) as day_of_week,
                          strftime('%H', t.recognized_at) as hour,
                          strftime('%Y-%m-%d', t.recognized_at) as date
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   ORDER BY t.recognized_at ASC""",
            )
        return [dict(r) for r in cur.fetchall()]

    def get_stats(self, station_id: int | None = None) -> dict[str, Any]:
        """Aggregate statistics, optionally by station."""
        def _q(sql: str, params: list[Any] | None = None) -> Any:
            cur = self.conn.execute(sql, params or [])
            return cur.fetchone()

        if station_id:
            total = _q("SELECT COUNT(*) as c FROM tracks WHERE station_id = ?", [station_id])["c"]
            artists = _q("SELECT COUNT(DISTINCT LOWER(artist)) as c FROM tracks WHERE station_id = ?", [station_id])["c"]
            unique = _q("""SELECT COUNT(DISTINCT LOWER(artist) || '|' || LOWER(title)) as c
                          FROM tracks WHERE station_id = ?""", [station_id])["c"]
            row = _q("SELECT MIN(recognized_at) as first, MAX(recognized_at) as last FROM tracks WHERE station_id = ?", [station_id])
        else:
            total = _q("SELECT COUNT(*) as c FROM tracks")["c"]
            artists = _q("SELECT COUNT(DISTINCT LOWER(artist)) as c FROM tracks")["c"]
            unique = _q("SELECT COUNT(DISTINCT LOWER(artist) || '|' || LOWER(title)) as c FROM tracks")["c"]
            row = _q("SELECT MIN(recognized_at) as first, MAX(recognized_at) as last FROM tracks")

        return {
            "total_tracks": total,
            "unique_tracks": unique,
            "unique_artists": artists,
            "first_track_at": row["first"],
            "last_track_at": row["last"],
        }

    def get_track_count_by_date(self, station_id: int | None = None,
                                 days: int = 45) -> list[dict[str, Any]]:
        """Tracks grouped by date."""
        if station_id:
            cur = self.conn.execute(
                """SELECT strftime('%Y-%m-%d', recognized_at) as date,
                          COUNT(*) as count
                   FROM tracks
                   WHERE station_id = ?
                   GROUP BY date ORDER BY date ASC""",
                (station_id,),
            )
        else:
            cur = self.conn.execute(
                """SELECT strftime('%Y-%m-%d', recognized_at) as date,
                          COUNT(*) as count
                   FROM tracks
                   GROUP BY date ORDER BY date ASC"""
            )
        return [dict(r) for r in cur.fetchall()]

    def cleanup_old_tracks(self, days: int = 45) -> int:
        """Delete tracks older than N days across all stations."""
        cur = self.conn.execute(
            "DELETE FROM tracks WHERE recognized_at < datetime('now', ?)",
            (f'-{days} days',),
        )
        self.conn.commit()
        deleted = cur.rowcount
        if deleted:
            self.conn.execute("VACUUM")
        return deleted

    def get_all_tracks_count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM tracks")
        return cur.fetchone()["cnt"]

    # ── Lifecycle ──────────────────────────────────────────────────────

    def close(self) -> None:
        if self._conn:
            self.conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
