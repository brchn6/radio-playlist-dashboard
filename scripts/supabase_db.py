#!/usr/bin/env python3
"""
Supabase Postgres client — direct psycopg2 replacement for PlaylistDB.

Mirrors the PlaylistDB interface exactly so updater.py and generate_data.py
can swap from SQLite to Supabase with minimal changes.

Design:
- Direct Postgres connection (psycopg2), NOT the REST client — lower latency,
  full SQL, server-side cursors.
- Every query is wrapped to never raise: failure logs and returns empty/null.
- The caller (updater.py) maintains a retry queue for writes that fail.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Station registry (mirrors STATIONS_CONFIG in db.py) ────────────────
# Kept here so SupabaseDB is self-contained; the stations table in Supabase
# has the same data but we read from code for speed.
STATIONS_CONFIG: list[dict[str, Any]] = [
    {"slug": "kol-hashfela", "name": "קול השפלה 103.6FM",  "stream_url": "https://radio.streamgates.net/stream/1036kh", "website": "https://1036kh.com",   "proxy_port": 8761, "color": "#6ae3c1"},
    {"slug": "galgalatz",    "name": "גלגלצ",             "stream_url": "https://glzwizzlv.bynetcdn.com/glglz_mp3", "website": "https://glglz.co.il",       "proxy_port": 8762, "color": "#e36a6a"},
    {"slug": "99fm",         "name": "99FM",              "stream_url": "https://eco01.livecdn.biz/ecolive/99fm_aac/icecast.audio", "website": "https://99fm.co.il",                "proxy_port": 8763, "color": "#6ab8e3"},
    {"slug": "radio-tlv",    "name": "רדיו תל אביב 102FM", "stream_url": "https://cdn88.mediacast.co.il/102-tlv-live/102fm_aac/icecast.audio", "website": "https://102fm.co.il",            "proxy_port": 8764, "color": "#e3c86a"},
    {"slug": "kan-88",       "name": "כאן 88",            "stream_url": "https://27953.live.streamtheworld.com/KAN_88.mp3", "website": "https://www.kan.org.il/radio/88.aspx", "proxy_port": 8765, "color": "#c86ae3"},
    {"slug": "kan-bet",      "name": "כאן ב",             "stream_url": "https://27913.live.streamtheworld.com/KAN_BET.mp3", "website": "https://www.kan.org.il/radio/bet.aspx", "proxy_port": 8766, "color": "#e38a6a"},
    {"slug": "galil",        "name": "קול הגליל העליון",   "stream_url": "https://radio.streamgates.net/stream/galil", "website": "",    "proxy_port": 8767, "color": "#a06ae3"},
    {"slug": "radio-darom",   "name": "רדיו דרום 97FM",     "stream_url": "https://cdn.cybercdn.live/Darom_97FM/Live/icecast.audio", "website": "https://www.radiodarom.co.il/", "proxy_port": 8768, "color": "#e36ac8"},
]

STATIONS_BY_SLUG = {s["slug"]: s for s in STATIONS_CONFIG}
STATIONS_BY_PORT = {s["proxy_port"]: s for s in STATIONS_CONFIG}


# ── .env loader (lightweight, no external deps) ────────────────────────

def _load_env() -> dict[str, str]:
    env_path = PROJECT_ROOT / ".env"
    env_vars: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text("utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip().strip("'\"").strip()
    return env_vars


# ── Connection ─────────────────────────────────────────────────────────

class SupabaseDB:
    """Supabase Postgres client — direct psycopg2, mirror of PlaylistDB interface.

    All methods are best-effort: failures log and return empty/null so
    collection never stops. The caller (updater.py) provides a retry queue
    for writes that fail.
    """

    def __init__(self) -> None:
        self._conn: Any = None
        self._connected = False

    # ── connection management ──────────────────────────────────────────

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self._connect()
        return self._conn

    def _connect(self) -> None:
        try:
            import psycopg2  # noqa: F811
        except ImportError:
            print("[supabase_db] psycopg2 not installed — cannot connect", flush=True)
            self._connected = False
            return

        env = _load_env()
        password = env.get("SUPABASE_DB_PASSWORD", "")
        if not password:
            print("[supabase_db] SUPABASE_DB_PASSWORD not found in .env", flush=True)
            self._connected = False
            return

        try:
            self._conn = psycopg2.connect(
                host="db.ktewdeaegtukbosrgxmw.supabase.co",
                port=5432,
                dbname="postgres",
                user="postgres",
                password=password,
                connect_timeout=5,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            self._conn.autocommit = True
            self._connected = True
        except Exception as exc:
            print(f"[supabase_db] connect failed: {exc}", flush=True)
            self._connected = False
            self._conn = None

    def connected(self) -> bool:
        if self._conn is None or self._conn.closed:
            self._connect()
        return self._connected

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._connected = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── helpers ────────────────────────────────────────────────────────

    def _query(self, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
        """Execute a SELECT and return rows as dicts. Never raises."""
        if not self.connected():
            return []
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params or [])
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = []
                for row in cur.fetchall():
                    d = dict(zip(cols, row))
                    # Convert datetime/date objects to ISO strings (for JSON serialization)
                    for k, v in d.items():
                        if isinstance(v, (datetime,)):
                            d[k] = v.strftime("%Y-%m-%dT%H:%M:%SZ")
                    rows.append(d)
                return rows
        except Exception as exc:
            print(f"[supabase_db] query failed: {exc}", flush=True)
            return []

    def _execute(self, sql: str, params: list[Any] | None = None) -> int | None:
        """Execute a write statement. Returns rowcount or None on failure."""
        if not self.connected():
            return None
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params or [])
                self.conn.commit()
                return cur.rowcount
        except Exception as exc:
            print(f"[supabase_db] execute failed: {exc}", flush=True)
            self._conn.rollback()
            return None

    # ── Stations ───────────────────────────────────────────────────────

    def get_stations(self) -> list[dict[str, Any]]:
        """Return stations list — reads from STATIONS_CONFIG (code), not DB."""
        return self._query(
            "SELECT id, slug, name, stream_url, proxy_port, color, website, enabled "
            "FROM stations ORDER BY id"
        ) or [
            {"id": i + 1, **s, "enabled": True, "website": s.get("website", "")}
            for i, s in enumerate(STATIONS_CONFIG)
        ]

    # ── Tracks ─────────────────────────────────────────────────────────

    def insert_track(
        self, station_id: int, artist: str, title: str,
        text: str = "", url: str = "", shazam_key: str = "",
        recognized_at: str = "", isrc: str = "",
        bpm: float | None = None,
        musical_key: str | None = None,
        station_slug: str = "",
    ) -> bool:
        """Insert one track into Supabase Postgres. Returns True on success."""
        slug = station_slug or STATIONS_BY_SLUG.get(
            next((s["slug"] for s in STATIONS_CONFIG if s.get("id") == station_id), ""), {}
        ).get("slug", "")

        if not slug:
            for s in STATIONS_CONFIG:
                if s.get("id") == station_id:
                    slug = s["slug"]
                    break

        sql = """
            INSERT INTO tracks
                (station_id, station_slug, artist, title, text, url,
                 shazam_key, isrc, bpm, musical_key, recognized_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (station_id, shazam_key, recognized_at) DO NOTHING
        """
        rc = self._execute(sql, [
            station_id, slug, artist, title, text or None,
            url or None, shazam_key or None, isrc or None,
            bpm, musical_key, recognized_at,
        ])
        return rc is not None

    def track_exists(
        self, station_id: int, shazam_key: str,
        artist: str, title: str,
        within_minutes: int | None = None,
    ) -> bool:
        """Has this song been recorded for this station within the window?"""
        if within_minutes is None:
            rows = self._query(
                """SELECT 1 FROM tracks
                   WHERE station_id = %s
                     AND (
                       (shazam_key IS NOT NULL AND shazam_key = %s)
                       OR (LOWER(artist) = LOWER(%s) AND LOWER(title) = LOWER(%s))
                     )
                   LIMIT 1""",
                [station_id, shazam_key, artist.strip(), title.strip()],
            )
            return len(rows) > 0

        rows = self._query(
            """SELECT 1 FROM tracks
               WHERE station_id = %s
                 AND recognized_at >= NOW() - INTERVAL '%s minutes'
                 AND (
                   (shazam_key IS NOT NULL AND shazam_key = %s)
                   OR (LOWER(artist) = LOWER(%s) AND LOWER(title) = LOWER(%s))
                 )
               LIMIT 1""",
            [station_id, int(within_minutes), shazam_key, artist.strip(), title.strip()],
        )
        return len(rows) > 0

    def last_played_at(
        self, station_id: int, shazam_key: str,
        artist: str, title: str,
    ) -> datetime | None:
        """When this song was last recorded for this station, or None."""
        rows = self._query(
            """SELECT MAX(recognized_at) AS last FROM tracks
               WHERE station_id = %s
                 AND (
                   (shazam_key IS NOT NULL AND shazam_key = %s)
                   OR (LOWER(artist) = LOWER(%s) AND LOWER(title) = LOWER(%s))
                 )""",
            [station_id, shazam_key, artist.strip(), title.strip()],
        )
        if rows and rows[0].get("last"):
            return rows[0]["last"]
        return None

    def get_latest_track(self, station_id: int | None = None) -> dict[str, Any] | None:
        """Most recently recognized track, optionally by station."""
        sql = """SELECT t.*, s.slug as station_slug, s.name as station_name,
                        s.color as station_color
                 FROM tracks t
                 JOIN stations s ON s.id = t.station_id"""
        params: list[Any] = []
        if station_id:
            sql += " WHERE t.station_id = %s"
            params.append(station_id)
        sql += " ORDER BY t.recognized_at DESC LIMIT 1"
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def get_all_current_tracks(self) -> list[dict[str, Any]]:
        """Latest track for EACH station."""
        return self._query(
            """SELECT t.*, s.slug as station_slug, s.name as station_name,
                      s.color as station_color
               FROM tracks t
               JOIN stations s ON s.id = t.station_id
               WHERE t.id IN (
                   SELECT MAX(id) FROM tracks GROUP BY station_id
               )
               ORDER BY s.id ASC"""
        )

    def get_history(
        self, station_id: int | None = None,
        limit: int = 200, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Track history, newest first. Optionally filtered by station."""
        sql = """SELECT t.*, s.slug as station_slug, s.name as station_name,
                        s.color as station_color
                 FROM tracks t
                 JOIN stations s ON s.id = t.station_id"""
        params: list[Any] = []
        if station_id:
            sql += " WHERE t.station_id = %s"
            params.append(station_id)
        sql += " ORDER BY t.recognized_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        return self._query(sql, params)

    def get_hype_tracks(
        self, station_id: int | None = None,
        min_count: int = 1, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Most frequently played tracks, optionally by station."""
        if station_id:
            rows = self._query(
                """SELECT t.artist, t.title, t.text, COUNT(*) as play_count,
                          MIN(t.recognized_at) as first_seen,
                          MAX(t.recognized_at) as last_seen,
                          s.slug as station_slug, s.name as station_name,
                          s.color as station_color
                   FROM tracks t
                   JOIN stations s ON s.id = t.station_id
                   WHERE t.station_id = %s
                   GROUP BY LOWER(t.artist), LOWER(t.title), t.artist, t.title,
                            t.text, s.slug, s.name, s.color
                   HAVING COUNT(*) >= %s
                   ORDER BY play_count DESC LIMIT %s""",
                [station_id, min_count, limit],
            )
        else:
            rows = self._query(
                """SELECT t.artist, t.title, t.text, COUNT(*) as play_count,
                          MIN(t.recognized_at) as first_seen,
                          MAX(t.recognized_at) as last_seen
                   FROM tracks t
                   GROUP BY LOWER(t.artist), LOWER(t.title), t.artist, t.title, t.text
                   HAVING COUNT(*) >= %s
                   ORDER BY play_count DESC LIMIT %s""",
                [min_count, limit],
            )
        return rows

    def get_cross_station_tracks(
        self, min_stations: int = 2, limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Tracks that played on multiple stations."""
        return self._query(
            """SELECT t.artist, t.title, t.text,
                      COUNT(DISTINCT t.station_id) as station_count,
                      STRING_AGG(DISTINCT s.name, ', ') as station_names,
                      STRING_AGG(DISTINCT s.slug, ', ') as station_slugs,
                      COUNT(*) as total_plays,
                      MIN(t.recognized_at) as first_seen,
                      MAX(t.recognized_at) as last_seen
               FROM tracks t
               JOIN stations s ON s.id = t.station_id
               GROUP BY LOWER(t.artist), LOWER(t.title), t.artist, t.title, t.text
               HAVING COUNT(DISTINCT t.station_id) >= %s
               ORDER BY station_count DESC, total_plays DESC
               LIMIT %s""",
            [min_stations, limit],
        )

    def get_scatter_data(
        self, station_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Time-based data for scatterplot."""
        sql = """SELECT t.artist, t.title, t.text, t.recognized_at,
                        s.slug as station_slug, s.color as station_color,
                        EXTRACT(DOW FROM t.recognized_at) as day_of_week,
                        EXTRACT(HOUR FROM t.recognized_at) as hour,
                        TO_CHAR(t.recognized_at, 'YYYY-MM-DD') as date
                 FROM tracks t
                 JOIN stations s ON s.id = t.station_id"""
        params: list[Any] = []
        if station_id:
            sql += " WHERE t.station_id = %s"
            params.append(station_id)
        sql += " ORDER BY t.recognized_at ASC"
        return self._query(sql, params)

    def get_stats(self, station_id: int | None = None) -> dict[str, Any]:
        """Aggregate statistics, optionally by station."""
        if station_id:
            rows = self._query(
                """SELECT COUNT(*) as total,
                          COUNT(DISTINCT LOWER(artist)) as artists,
                          COUNT(DISTINCT LOWER(artist) || '|' || LOWER(title)) as unique_tracks,
                          MIN(recognized_at) as first_track,
                          MAX(recognized_at) as last_track
                   FROM tracks WHERE station_id = %s""",
                [station_id],
            )
        else:
            rows = self._query(
                """SELECT COUNT(*) as total,
                          COUNT(DISTINCT LOWER(artist)) as artists,
                          COUNT(DISTINCT LOWER(artist) || '|' || LOWER(title)) as unique_tracks,
                          MIN(recognized_at) as first_track,
                          MAX(recognized_at) as last_track
                   FROM tracks"""
            )
        if not rows:
            return {}
        r = rows[0]
        return {
            "total_tracks": r["total"],
            "unique_tracks": r["unique_tracks"],
            "unique_artists": r["artists"],
            "first_track_at": str(r["first_track"]) if r.get("first_track") else None,
            "last_track_at": str(r["last_track"]) if r.get("last_track") else None,
        }

    def get_track_count_by_date(
        self, station_id: int | None = None,
        days: int = 45,
    ) -> list[dict[str, Any]]:
        """Tracks grouped by date."""
        if station_id:
            return self._query(
                """SELECT TO_CHAR(recognized_at, 'YYYY-MM-DD') as date,
                          COUNT(*) as count
                   FROM tracks
                   WHERE station_id = %s
                   GROUP BY date ORDER BY date ASC""",
                [station_id],
            )
        return self._query(
            """SELECT TO_CHAR(recognized_at, 'YYYY-MM-DD') as date,
                      COUNT(*) as count
               FROM tracks
               GROUP BY date ORDER BY date ASC"""
        )

    # ── Non-music logging ──────────────────────────────────────────────

    def start_non_music_event(
        self, station_id: int, reason: str = "unknown",
    ) -> int | None:
        """Start a new non-music interval. Returns event ID."""
        self.end_non_music_event(station_id)
        rows = self._query(
            """INSERT INTO non_music_log (station_id, started_at, reason)
               VALUES (%s, NOW(), %s)
               RETURNING id""",
            [station_id, reason],
        )
        return rows[0]["id"] if rows else None

    def end_non_music_event(self, station_id: int) -> None:
        """Close the latest open non-music interval."""
        self._execute(
            """UPDATE non_music_log SET ended_at = NOW()
               WHERE station_id = %s AND ended_at IS NULL""",
            [station_id],
        )

    def get_open_non_music_event(
        self, station_id: int,
    ) -> dict[str, Any] | None:
        """Get the latest open (non-ended) non-music event."""
        rows = self._query(
            """SELECT * FROM non_music_log
               WHERE station_id = %s AND ended_at IS NULL
               ORDER BY started_at DESC LIMIT 1""",
            [station_id],
        )
        return rows[0] if rows else None

    def get_non_music_stats(
        self, station_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Aggregate non-music intervals per station."""
        if station_id:
            return self._query(
                """SELECT n.station_id, s.name as station_name, s.slug as station_slug,
                          COUNT(*) as event_count,
                          COALESCE(SUM(
                              CASE WHEN n.ended_at IS NOT NULL
                                   THEN EXTRACT(EPOCH FROM (n.ended_at - n.started_at))
                                   ELSE 0 END
                          ), 0) as total_seconds,
                          MAX(n.ended_at) as last_event_at
                   FROM non_music_log n
                   JOIN stations s ON s.id = n.station_id
                   WHERE n.station_id = %s
                   GROUP BY n.station_id, s.name, s.slug""",
                [station_id],
            )
        return self._query(
            """SELECT n.station_id, s.name as station_name, s.slug as station_slug,
                      COUNT(*) as event_count,
                      COALESCE(SUM(
                          CASE WHEN n.ended_at IS NOT NULL
                               THEN EXTRACT(EPOCH FROM (n.ended_at - n.started_at))
                               ELSE 0 END
                      ), 0) as total_seconds,
                      MAX(n.ended_at) as last_event_at
               FROM non_music_log n
               JOIN stations s ON s.id = n.station_id
               GROUP BY n.station_id, s.name, s.slug"""
        )

    def get_recent_non_music(
        self, station_id: int | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Recent non-music events."""
        sql = """SELECT n.*, s.name as station_name, s.slug as station_slug
                 FROM non_music_log n
                 JOIN stations s ON s.id = n.station_id"""
        params: list[Any] = []
        if station_id:
            sql += " WHERE n.station_id = %s"
            params.append(station_id)
        sql += " ORDER BY n.started_at DESC LIMIT %s"
        params.append(limit)
        return self._query(sql, params)

    def get_non_music_intervals(self, days: int = 7) -> list[dict[str, Any]]:
        """Talk/commercial intervals. Returns [] if table doesn't exist."""
        try:
            return self._query(
                """SELECT n.station_id, n.started_at, n.ended_at, n.reason,
                          s.slug as station_slug
                   FROM non_music_log n
                   JOIN stations s ON s.id = n.station_id
                   WHERE n.started_at >= NOW() - INTERVAL '%s days'
                   ORDER BY n.started_at ASC""",
                [days],
            )
        except Exception:
            return []

    # ── Maintenance ────────────────────────────────────────────────────

    def cleanup_old_tracks(self, days: int = 45) -> int:
        """Delete tracks older than N days."""
        rc = self._execute(
            "DELETE FROM tracks WHERE recognized_at < NOW() - INTERVAL '%s days'",
            [days],
        )
        return rc or 0

    def get_all_tracks_count(self) -> int:
        """Total number of tracks."""
        rows = self._query("SELECT COUNT(*) as cnt FROM tracks")
        return rows[0]["cnt"] if rows else 0
