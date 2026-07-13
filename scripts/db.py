#!/usr/bin/env python3
"""SQLite database for 1036 Playlist Dashboard."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "playlist.db"


class PlaylistDB:
    """SQLite-backed playlist history store."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                artist      TEXT NOT NULL,
                title       TEXT NOT NULL,
                text        TEXT,
                url         TEXT,
                shazam_key  TEXT,
                recognized_at TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_tracks_recognized_at ON tracks(recognized_at);
            CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
            CREATE INDEX IF NOT EXISTS idx_tracks_shazam_key ON tracks(shazam_key);
            CREATE INDEX IF NOT EXISTS idx_tracks_artist_title ON tracks(artist, title);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

    def insert_track(self, artist: str, title: str, text: str = "",
                     url: str = "", shazam_key: str = "",
                     recognized_at: str = "") -> int:
        """Insert a track. Returns the row ID."""
        cur = self.conn.execute(
            """
            INSERT INTO tracks (artist, title, text, url, shazam_key, recognized_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (artist, title, text, url, shazam_key, recognized_at),
        )
        self.conn.commit()
        return cur.lastrowid

    def track_exists(self, shazam_key: str, artist: str, title: str) -> bool:
        """Check if a track was already recorded (by shazam_key or artist+title)."""
        if shazam_key:
            cur = self.conn.execute(
                "SELECT 1 FROM tracks WHERE shazam_key = ? LIMIT 1",
                (shazam_key,),
            )
            if cur.fetchone():
                return True
        cur = self.conn.execute(
            "SELECT 1 FROM tracks WHERE LOWER(artist) = LOWER(?) AND LOWER(title) = LOWER(?) LIMIT 1",
            (artist.strip(), title.strip()),
        )
        return cur.fetchone() is not None

    def get_latest_track(self) -> dict[str, Any] | None:
        """Get the most recently recognized track."""
        cur = self.conn.execute(
            "SELECT * FROM tracks ORDER BY recognized_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_history(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """Get track history, newest first."""
        cur = self.conn.execute(
            "SELECT * FROM tracks ORDER BY recognized_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_hype_tracks(self, min_count: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        """Get most frequently played tracks."""
        cur = self.conn.execute(
            """
            SELECT artist, title, text, COUNT(*) as play_count,
                   MIN(recognized_at) as first_seen,
                   MAX(recognized_at) as last_seen
            FROM tracks
            GROUP BY LOWER(artist), LOWER(title)
            HAVING play_count >= ?
            ORDER BY play_count DESC
            LIMIT ?
            """,
            (min_count, limit),
        )
        return [dict(r) for r in cur.fetchall()]

    def get_scatter_data(self) -> list[dict[str, Any]]:
        """Get time-based data for scatterplot: tracks over hours/days."""
        cur = self.conn.execute(
            """
            SELECT artist, title, text, recognized_at,
                   strftime('%w', recognized_at) as day_of_week,
                   strftime('%H', recognized_at) as hour,
                   strftime('%Y-%m-%d', recognized_at) as date
            FROM tracks
            ORDER BY recognized_at ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics."""
        cur = self.conn.execute("SELECT COUNT(*) as total FROM tracks")
        total = cur.fetchone()["total"]

        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT LOWER(artist)) as artists FROM tracks"
        )
        artists = cur.fetchone()["artists"]

        cur = self.conn.execute(
            "SELECT COUNT(DISTINCT LOWER(artist) || '|' || LOWER(title)) as unique_tracks FROM tracks"
        )
        unique = cur.fetchone()["unique_tracks"]

        cur = self.conn.execute(
            "SELECT MIN(recognized_at) as first, MAX(recognized_at) as last FROM tracks"
        )
        row = cur.fetchone()

        return {
            "total_tracks": total,
            "unique_tracks": unique,
            "unique_artists": artists,
            "first_track_at": row["first"],
            "last_track_at": row["last"],
        }

    def get_track_count_by_date(self, days: int = 30) -> list[dict[str, Any]]:
        """Group tracks by date, limited to last N days."""
        cur = self.conn.execute(
            """
            SELECT strftime('%Y-%m-%d', recognized_at) as date,
                   COUNT(*) as count
            FROM tracks
            GROUP BY date
            ORDER BY date ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]

    def cleanup_old_tracks(self, days: int = 30) -> int:
        """Delete tracks older than N days. Returns count of deleted rows."""
        cutoff = (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
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
        """Get total number of tracks in DB."""
        cur = self.conn.execute("SELECT COUNT(*) as cnt FROM tracks")
        return cur.fetchone()["cnt"]

    def close(self) -> None:
        if self._conn:
            self.conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
