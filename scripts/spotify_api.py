#!/usr/bin/env python3
"""
Spotify API integration service — standalone HTTP server.

Handles OAuth authentication, track search/resolution, and playlist creation.
Run standalone::

    python scripts/spotify_api.py

Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the environment
or `.env` file. The service listens on port 9900 (must match a registered
Redirect URI in your Spotify Developer app).

Usage:
    GET  /auth            — Start OAuth flow (redirect to Spotify)
    GET  /callback?code=… — OAuth callback (exchange code for tokens)
    GET  /token           — Check authentication status
    POST /create-playlist — Create a playlist with resolved track URIs
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import urllib.parse
import webbrowser as wb
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx

PORT = 9900
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Credentials ──────────────────────────────────────────────────────────
def _load_env() -> None:
    """Load .env file if SPOTIFY_CLIENT_ID not already set."""
    if os.environ.get("SPOTIFY_CLIENT_ID"):
        return
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_env()

CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", f"http://127.0.0.1:{PORT}/")

if not CLIENT_ID or not CLIENT_SECRET:
    print(
        "❌ SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set in .env\n"
        "   See https://developer.spotify.com/dashboard",
        file=sys.stderr,
    )
    sys.exit(1)

# ── Spotify API constants ────────────────────────────────────────────────
SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API = "https://api.spotify.com/v1"

# In-memory token store (single user — fine for a personal dashboard)
_tokens: dict[str, str | None] = {"access": None, "refresh": None}


# ── HTTP helpers ─────────────────────────────────────────────────────────
async def _exchange_code(code: str) -> dict[str, Any]:
    """Exchange authorisation code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()


async def _refresh_access() -> str | None:
    """Refresh the access token using the stored refresh token."""
    rt = _tokens.get("refresh")
    if not rt:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            _tokens["access"] = None
            _tokens["refresh"] = None
            return None
        data = r.json()
        _tokens["access"] = data.get("access_token")
        if data.get("refresh_token"):
            _tokens["refresh"] = data["refresh_token"]
        return _tokens["access"]


async def _spotify_get(path: str) -> dict[str, Any] | list[Any] | None:
    """Authenticated GET to Spotify API with auto-refresh."""
    token = _tokens.get("access")
    if not token:
        token = await _refresh_access()
    if not token:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SPOTIFY_API}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code == 401:
            token = await _refresh_access()
            if not token:
                return None
            r = await client.get(
                f"{SPOTIFY_API}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if r.status_code != 200:
            return None
        return r.json()


async def _spotify_post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Authenticated POST to Spotify API with auto-refresh."""
    token = _tokens.get("access")
    if not token:
        token = await _refresh_access()
    if not token:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SPOTIFY_API}{path}",
            json=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if r.status_code == 401:
            token = await _refresh_access()
            if not token:
                return None
            r = await client.post(
                f"{SPOTIFY_API}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        if r.status_code not in (200, 201):
            return None
        return r.json()


# ── Track resolution ─────────────────────────────────────────────────────
async def _resolve_track(artist: str, title: str) -> str | None:
    """Search Spotify for a track, return its URI, or None."""
    query = urllib.parse.quote(f"artist:{artist} track:{title}")
    data = await _spotify_get(f"/search?q={query}&type=track&limit=3")
    if not data or not isinstance(data, dict):
        return None
    tracks = data.get("tracks", {}).get("items", [])
    if not tracks:
        # Broader fallback: just artist + title
        query = urllib.parse.quote(f"{artist} {title}")
        data = await _spotify_get(f"/search?q={query}&type=track&limit=3")
        if not data or not isinstance(data, dict):
            return None
        tracks = data.get("tracks", {}).get("items", [])
    for t in tracks:
        uri = t.get("uri")
        if uri:
            return uri
    return None


# ── HTTP server ──────────────────────────────────────────────────────────
class SpotifyHandler(http.server.BaseHTTPRequestHandler):
    """Simple HTTP request handler for the Spotify API service."""

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"  [{self.log_date_time_string()}] {fmt % args}", file=sys.stderr)

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: int = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", url)
        self.end_headers()

    # ── Routes ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = urllib.parse.parse_qs(parsed.query)

        # Spotify redirects back to the registered URI with ?code=...
        # The registered URI is http://127.0.0.1:9900/ (root), so the
        # callback arrives at path=/ with a "code" query parameter.
        if (path == "" or path == "/") and query.get("code"):
            self._handle_callback(query)
        elif path == "/auth":
            self._handle_auth()
        elif path == "/token":
            self._handle_token()
        elif path == "/test":
            self._send_json({"status": "ok", "client_id": CLIENT_ID[:8] + "..."})
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/create-playlist":
            self._handle_create_playlist()
        else:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        """CORS preflight."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET /auth ───────────────────────────────────────────────────────
    def _handle_auth(self) -> None:
        params = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": "playlist-modify-private playlist-modify-public",
            "show_dialog": "true",
        })
        auth_url = f"{SPOTIFY_AUTH_URL}?{params}"
        print("  🔐 Redirecting to Spotify OAuth…", file=sys.stderr)
        self._redirect(auth_url)

    # ── GET /callback ───────────────────────────────────────────────────
    def _handle_callback(self, query: dict[str, list[str]]) -> None:
        code = query.get("code", [None])[0]
        error = query.get("error", [None])[0]
        if error:
            self._send_html(
                f"<h2>❌ Spotify auth error: {error}</h2>"
                "<p>You can close this window and try again.</p>"
            )
            return
        if not code:
            self._send_html(
                "<h2>❌ No authorisation code received.</h2>"
                "<p>You can close this window and try again.</p>"
            )
            return

        import asyncio

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(_exchange_code(code))
            loop.close()
        except Exception as exc:
            self._send_html(
                f"<h2>❌ Token exchange failed</h2><pre>{exc}</pre>"
            )
            return

        _tokens["access"] = data.get("access_token")
        _tokens["refresh"] = data.get("refresh_token")
        print(f"  ✅ Spotify authenticated (expires in {data.get('expires_in', '?')}s)", file=sys.stderr)
        self._send_html(
            "<h2>✅ Spotify connected!</h2>"
            "<p>You can close this window and return to the dashboard.</p>"
            "<script>window.close()</script>"
        )

    # ── GET /token ──────────────────────────────────────────────────────
    def _handle_token(self) -> None:
        if _tokens.get("access"):
            self._send_json({"authenticated": True})
        else:
            self._send_json({"authenticated": False})

    # ── POST /create-playlist ───────────────────────────────────────────
    def _handle_create_playlist(self) -> None:
        content_len = int(self.headers.get("Content-Length", 0))
        if not content_len:
            self._send_json({"error": "empty body"}, HTTPStatus.BAD_REQUEST)
            return
        body = json.loads(self.rfile.read(content_len))
        name = body.get("name", "Radio Playlist")
        description = body.get("description", "")
        public = body.get("public", False)
        tracks: list[dict[str, str]] = body.get("tracks", [])

        if not tracks:
            self._send_json({"error": "no tracks provided"}, HTTPStatus.BAD_REQUEST)
            return

        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 1. Resolve each track to a Spotify URI
        uris: list[str] = []
        failed: list[dict[str, str]] = []
        for t in tracks:
            uri = loop.run_until_complete(_resolve_track(t.get("artist", ""), t.get("title", "")))
            if uri:
                uris.append(uri)
            else:
                failed.append(t)

        if not uris:
            loop.close()
            self._send_json(
                {"error": "none of the tracks could be found on Spotify", "failed": failed},
                HTTPStatus.NOT_FOUND,
            )
            return

        # 2. Get current user ID
        me = loop.run_until_complete(_spotify_get("/me"))
        if not me or not isinstance(me, dict):
            loop.close()
            self._send_json({"error": "not authenticated"}, HTTPStatus.UNAUTHORIZED)
            return
        user_id = me.get("id", "")

        # 3. Create playlist
        playlist = loop.run_until_complete(
            _spotify_post(f"/users/{user_id}/playlists", {
                "name": name,
                "description": description,
                "public": public,
            })
        )
        if not playlist:
            loop.close()
            self._send_json({"error": "failed to create playlist"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        playlist_id = playlist.get("id")
        playlist_url = f"https://open.spotify.com/playlist/{playlist_id}"

        # 4. Add tracks in batches of 100
        for i in range(0, len(uris), 100):
            batch = uris[i : i + 100]
            loop.run_until_complete(
                _spotify_post(f"/playlists/{playlist_id}/tracks", {"uris": batch})
            )

        loop.close()
        print(f"  ✅ Created playlist \"{name}\" with {len(uris)} tracks", file=sys.stderr)
        self._send_json({
            "success": True,
            "playlist_id": playlist_id,
            "playlist_url": playlist_url,
            "track_count": len(uris),
            "failed_count": len(failed),
            "failed": failed,
        })


# ── Run ──────────────────────────────────────────────────────────────────
def main() -> None:
    server = http.server.HTTPServer(("127.0.0.1", PORT), SpotifyHandler)
    print(f"🎵 Spotify API service running on http://127.0.0.1:{PORT}", file=sys.stderr)
    print(f"   Client ID: {CLIENT_ID[:12]}…", file=sys.stderr)
    print(f"   Open http://127.0.0.1:{PORT}/auth to authenticate", file=sys.stderr)
    print(file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)
        server.server_close()


if __name__ == "__main__":
    main()
