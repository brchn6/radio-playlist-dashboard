#!/usr/bin/env python3
"""
Supabase Postgres client — direct psycopg2 replacement for PlaylistDB.

Mirrors the PlaylistDB interface exactly so updater.py and generate_data.py
can swap from SQLite to Supabase with minimal changes.

Design:
- Direct Postgres connection (psycopg2), NOT the REST client — lower latency,
  full SQL, server-side cursors.
- Every query is wrapped to never raise: failure logs and returns empty/null.
- Startup failures are loud: a missing psycopg2 driver raises SystemExit (a
  fresh install must install requirements.txt first).
- The caller (updater.py) maintains a retry queue for writes that fail.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from env_config import get_env, db_host_from_url  # noqa: E402

# ── Station registry (canonical; read from code, not the DB) ────────────
# Kept here so SupabaseDB is self-contained; the stations table in Supabase
# has the same data but we read from code for speed.
STATIONS_CONFIG: list[dict[str, Any]] = [
    {"slug": "kol-hashfela", "name": "קול השפלה 103.6FM",  "stream_url": "https://radio.streamgates.net/stream/1036kh", "website": "https://1036kh.com",   "proxy_port": 8761, "color": "#6ae3c1"},
    {"slug": "galgalatz",    "name": "גלגלצ",             "stream_url": "https://glzwizzlv.bynetcdn.com/glglz_mp3", "website": "https://glglz.co.il",       "proxy_port": 8762, "color": "#e36a6a"},
    {"slug": "99fm",         "name": "99FM",              "stream_url": "https://eco01.livecdn.biz/ecolive/99fm_aac/icecast.audio", "website": "https://99fm.co.il",                "proxy_port": 8763, "color": "#6ab8e3", "referer": "https://99fm.co.il"},
    {"slug": "radio-tlv",    "name": "רדיו תל אביב 102FM", "stream_url": "https://cdn88.mediacast.co.il/102-tlv-live/102fm_aac/icecast.audio", "website": "https://102fm.co.il",            "proxy_port": 8764, "color": "#e3c86a", "referer": "https://102fm.co.il"},
    {"slug": "kan-88",       "name": "כאן 88",            "stream_url": "https://27953.live.streamtheworld.com/KAN_88.mp3", "website": "https://www.kan.org.il/radio/88.aspx", "proxy_port": 8765, "color": "#c86ae3"},
    {"slug": "kan-bet",      "name": "כאן ב",             "stream_url": "https://27913.live.streamtheworld.com/KAN_BET.mp3", "website": "https://www.kan.org.il/radio/bet.aspx", "proxy_port": 8766, "color": "#e38a6a"},
    {"slug": "galil",        "name": "קול הגליל העליון",   "stream_url": "https://radio.streamgates.net/stream/galil", "website": "",    "proxy_port": 8767, "color": "#a06ae3"},
    {"slug": "radio-darom",   "name": "רדיו דרום 97FM",     "stream_url": "https://cdn.cybercdn.live/Darom_97FM/Live/icecast.audio", "website": "https://www.radiodarom.co.il/", "proxy_port": 8768, "color": "#e36ac8"},
]

STATIONS_BY_SLUG = {s["slug"]: s for s in STATIONS_CONFIG}
STATIONS_BY_PORT = {s["proxy_port"]: s for s in STATIONS_CONFIG}


# ── .env ────────────────────────────────────────────────────────────────
# Parsing lives in env_config.py (the single project loader); see imports above.


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
            raise SystemExit(
                "[supabase_db] psycopg2 is not installed - add 'psycopg2-binary' "
                "to requirements.txt and re-run deploy/install.sh before "
                "starting the collector (every track write goes through it)"
            )

        password = get_env("SUPABASE_DB_PASSWORD") or ""
        if not password:
            print(
                "[supabase_db] SUPABASE_DB_PASSWORD not found in .env "
                "(see .env.example: Dashboard -> Project Settings -> Database)",
                flush=True,
            )
            self._connected = False
            return

        host = get_env("SUPABASE_DB_HOST") or db_host_from_url(
            get_env("SUPABASE_URL") or ""
        )
        if not host or host == "db.":
            print(
                "[supabase_db] cannot derive DB host: SUPABASE_URL missing in .env "
                "(or set SUPABASE_DB_HOST explicitly)",
                flush=True,
            )
            self._connected = False
            return

        try:
            self._conn = psycopg2.connect(
                host=host,
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
            self._seed_stations_if_empty()
        except Exception as exc:
            print(f"[supabase_db] connect failed: {exc}", flush=True)
            self._connected = False
            self._conn = None

    def connected(self) -> bool:
        if self._conn is None or self._conn.closed:
            self._connect()
        return self._connected

    def _seed_stations_if_empty(self) -> None:
        """Best-effort: if the stations table has no rows, insert STATIONS_CONFIG.

        The registry is read from code (get_stations()), so this is only to
        keep the SQL table populated for JOINs and anything that reads it
        directly on a fresh DB. Never raises.
        """
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM stations")
                if cur.fetchone()[0] == 0:
                    for s in STATIONS_CONFIG:
                        cur.execute(
                            """INSERT INTO stations
                                   (slug, name, stream_url, proxy_port, color, website, enabled)
                               VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                               ON CONFLICT (slug) DO NOTHING""",
                            [s["slug"], s["name"], s["stream_url"], s["proxy_port"],
                             s.get("color", "#6ae3c1"), s.get("website", "")],
                        )
        except Exception as exc:
            print(f"[supabase_db] stations seed skipped: {exc}", flush=True)

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
        """Return the station registry.

        Reads STATIONS_CONFIG (code) directly — the registry is code, not the
        DB. The positional id (1..N) is synthesized to line up with the
        schema's preserved ids (1-8), keeping tracks.station_id FKs aligned.
        """
        return [
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
        """Insert one track into Supabase Postgres. Returns True on success.

        station_slug is required from callers — there is no id->slug lookup:
        the registry is read from code and STATIONS_CONFIG has no 'id' keys,
        so any fallback would always miss and insert empty slugs.
        """
        # No fallback lookup here — see docstring.
        slug = station_slug

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

    def insert_tracks_bulk(self, rows: list[dict[str, Any]]) -> int:
        """Insert many tracks at once, skipping ones that already exist.

        Backfill path only: the collector keeps writing one row at a time via
        insert_track(). Same table, same natural key and same
        ON CONFLICT DO NOTHING, so a backfill can never duplicate a play and
        re-running is a no-op. Batched because a one-off backfill of tens of
        thousands of rows over the network would otherwise be one round trip
        per row. Returns the number of rows actually inserted.
        """
        if not rows or not self.connected():
            return 0
        try:
            from psycopg2.extras import execute_values
        except ImportError:
            print("[supabase_db] psycopg2.extras missing - bulk insert skipped", flush=True)
            return 0

        sql = """
            INSERT INTO tracks
                (station_id, station_slug, artist, title, text, url,
                 shazam_key, isrc, bpm, musical_key, recognized_at)
            VALUES %s
            ON CONFLICT (station_id, shazam_key, recognized_at) DO NOTHING
        """
        values = [
            (r["station_id"], r.get("station_slug"), r["artist"], r["title"],
             r.get("text") or None, r.get("url") or None,
             r.get("shazam_key") or None, r.get("isrc") or None,
             r.get("bpm"), r.get("musical_key"), r["recognized_at"])
            for r in rows
        ]
        inserted = 0
        try:
            with self.conn.cursor() as cur:
                for i in range(0, len(values), 1000):
                    batch = values[i:i + 1000]
                    # page_size == len(batch) forces ONE statement per call, so
                    # cur.rowcount is that batch's count. With pagination it
                    # would report only the final page's count.
                    execute_values(cur, sql, batch, page_size=len(batch))
                    if cur.rowcount and cur.rowcount > 0:
                        inserted += cur.rowcount
                self.conn.commit()
        except Exception as exc:
            print(f"[supabase_db] bulk insert failed: {exc}", flush=True)
            self._conn.rollback()
        return inserted

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
        sql += " ORDER BY t.recognized_at DESC, t.id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        return self._query(sql, params)

    def get_history_since(
        self, ts: str, station_id: int | None = None, limit: int = 100000,
    ) -> list[dict[str, Any]]:
        """Tracks recognized strictly after an ISO timestamp, newest first.

        This is the incremental read used by the local mirror: instead of
        pulling ALL tracks every cycle, the collector fetches only what has
        arrived since its last checkpoint (a handful of rows, ~KB, instead of
        ~15 MB). Ties on the boundary are safe: the mirror dedupes by id, so
        re-fetching the same second costs nothing.
        """
        sql = """SELECT t.*, s.slug as station_slug, s.name as station_name,
                        s.color as station_color
                 FROM tracks t
                 JOIN stations s ON s.id = t.station_id
                 WHERE t.recognized_at > %s"""
        params: list[Any] = [ts]
        if station_id:
            sql += " AND t.station_id = %s"
            params.append(station_id)
        sql += " ORDER BY t.recognized_at DESC, t.id DESC LIMIT %s"
        params.append(limit)
        return self._query(sql, params)

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
        """Tracks grouped by date, oldest first.

        When days is not None, counts only tracks from the last N days
        (recognized_at >= NOW() - INTERVAL 'N days').
        """
        sql = """SELECT TO_CHAR(recognized_at, 'YYYY-MM-DD') as date,
                          COUNT(*) as count
                   FROM tracks"""
        where: list[str] = []
        params: list[Any] = []
        if station_id:
            where.append("station_id = %s")
            params.append(station_id)
        if days is not None:
            where.append("recognized_at >= NOW() - INTERVAL '%s days'")
            params.append(days)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY date ORDER BY date ASC"
        return self._query(sql, params)

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

    # ── System Events (uptime tracking) ─────────────────────────────────

    def _ensure_system_events_table(self) -> None:
        """Create system_events table if it doesn't exist."""
        self._execute("""
            CREATE TABLE IF NOT EXISTS system_events (
                id SERIAL PRIMARY KEY,
                event_type TEXT NOT NULL,
                source TEXT,
                started_at TIMESTAMPTZ NOT NULL,
                ended_at TIMESTAMPTZ,
                duration_seconds INT,
                description TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    def record_system_event(
        self, event_type: str, source: str = "",
        description: str = "",
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> bool:
        """Insert a system event.

        Args:
            event_type: e.g. 'outage_start', 'outage_end', 'proxy_crash',
                        'collector_crash', 'restart', 'connection_issue'
            source: e.g. 'collector', 'galgalatz', 'kan-88', 'watchdog'
            description: Human-readable description of the event
            started_at: ISO timestamp (defaults to NOW() if omitted)
            ended_at: ISO timestamp (optional, for bounded events)

        Returns True on success.
        """
        self._ensure_system_events_table()
        if started_at:
            sql = """INSERT INTO system_events
                     (event_type, source, started_at, ended_at, description)
                     VALUES (%s, %s, %s, %s, %s)"""
            rc = self._execute(sql, [event_type, source or None,
                                     started_at, ended_at, description or None])
        else:
            sql = """INSERT INTO system_events
                     (event_type, source, started_at, ended_at, description)
                     VALUES (%s, %s, NOW(), %s, %s)"""
            rc = self._execute(sql, [event_type, source or None,
                                     ended_at, description or None])
        return rc is not None

    def get_recent_events(
        self, days: int = 7, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent system events, newest first.

        Args:
            days: how far back to look
            limit: max events to return

        Returns list of event dicts (empty list if table doesn't exist or
        query fails).
        """
        self._ensure_system_events_table()
        return self._query(
            """SELECT id, event_type, source, started_at, ended_at,
                      duration_seconds, description, created_at
               FROM system_events
               WHERE started_at >= NOW() - INTERVAL '%s days'
               ORDER BY started_at DESC
               LIMIT %s""",
            [days, limit],
        )

    def get_system_uptime(self, days: int = 7) -> dict[str, Any]:
        """Calculate uptime percentage over N days.

        Considers any event with event_type in ('outage_start', 'proxy_crash',
        'collector_crash', 'connection_issue') and a known ended_at as an
        outage interval. Returns dict with:
            - uptime_pct: float percentage
            - total_seconds: total seconds in period
            - outage_seconds: total seconds of known outages
            - outage_count: number of distinct outage events
        """
        self._ensure_system_events_table()
        total_seconds = days * 86400
        rows = self._query(
            """SELECT COALESCE(SUM(
                      CASE WHEN ended_at IS NOT NULL
                           THEN EXTRACT(EPOCH FROM (ended_at - started_at))
                           ELSE 0 END
                  ), 0) as outage_seconds,
                      COUNT(*) as outage_count
               FROM system_events
               WHERE started_at >= NOW() - INTERVAL '%s days'
                 AND event_type IN ('outage_start', 'proxy_crash',
                                    'collector_crash', 'connection_issue')""",
            [days],
        )
        outage_seconds = rows[0]["outage_seconds"] if rows else 0
        outage_count = rows[0]["outage_count"] if rows else 0
        uptime_seconds = max(0, total_seconds - int(outage_seconds))
        uptime_pct = round(uptime_seconds / total_seconds * 100, 1) if total_seconds > 0 else 100.0
        return {
            "uptime_pct": uptime_pct,
            "total_seconds": total_seconds,
            "uptime_seconds": uptime_seconds,
            "outage_seconds": int(outage_seconds),
            "outage_count": outage_count,
        }
