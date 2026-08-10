#!/usr/bin/env python3
"""Add tracks to a Spotify playlist. Handles OAuth PKCE flow automatically."""
import hashlib, base64, secrets, urllib.parse, webbrowser, http.server, sys, json, os
from urllib.request import urlopen, Request
from urllib.parse import urlencode

# Credentials come from .env (gitignored) - never hardcode them.
def _load_env():
    env = {}
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
        for line in open(env_path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env

_env = _load_env()
CLIENT_ID = _env.get('SPOTIFY_CLIENT_ID')
CLIENT_SECRET = _env.get('SPOTIFY_CLIENT_SECRET')
if not CLIENT_ID or not CLIENT_SECRET:
    sys.exit('SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET missing from .env - see .env.example')
REDIRECT_PORT = 9000
from urllib.request import urlopen, Request
from urllib.parse import urlencode

REDIRECT_PORT = 9000
SCOPES = "playlist-modify-public playlist-modify-private"

PLAYLIST_ID = "1CJ3zksgMqEYTUXAsNToVW"

TRACK_IDS = [
    "spotify:track:27HgfYDO5pM4ZTyshecBFN",   # רונה
    "spotify:track:6rlXgQ761EmcIDnMWk7esr",     # מסובבת אותי
    "spotify:track:5gbxzSqABThINGDb7vIiwe",     # Edge of Desire - John Mayer
    "spotify:track:480j122Gpi252OIfy4SNzm",     # Yamore
    "spotify:track:6geg6XWPAxmwlVl8ZNYns0",     # On My Mind (Purple Disco Machine Remix)
    "spotify:track:1FUUe1VbjWakX9nFfd0Qki",     # Andalucia
    "spotify:track:5nPbKG04fhLkIAjcPFaZq7",     # I Adore You
    "spotify:track:7xANRiY9KEPylLrpAz2HpC",     # Sunrise (Adam Ten Remix)
    "spotify:track:6ztstiyZL6FXzh4aG46ZPD",     # Boogie Wonderland
    "spotify:track:1ot6jEe4w4hYnsOPjd3xKQ",     # I'm So Excited
    "spotify:track:3kMrazSvILsgcwtidZd1Qd",     # Bulletproof
    "spotify:track:3wlEt7tgbcyowpdlcnCwfz",     # Sauti
    "spotify:track:3sK8wGT43QFpWrvNQsrQya",       # DtMF - Bad Bunny
]

# PKCE helpers
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _sha256(s: str) -> str:
    return _b64url(hashlib.sha256(s.encode()).digest())

# Step 1: Get authorization URL
code_verifier = secrets.token_urlsafe(64)
code_challenge = _sha256(code_verifier)
state = secrets.token_urlsafe(16)

auth_params = {
    "client_id": CLIENT_ID,
    "response_type": "code",
    "redirect_uri": f"http://127.0.0.1:{REDIRECT_PORT}/",
    "scope": SCOPES,
    "code_challenge_method": "S256",
    "code_challenge": code_challenge,
    "state": state,
}
auth_url = f"https://accounts.spotify.com/authorize?{urlencode(auth_params)}"

print(f"Opening browser for Spotify authorization...")
print(f"If the browser doesn't open, visit:\n{auth_url}\n")
webbrowser.open(auth_url)

# Step 2: Catch the redirect in a tiny HTTP server
auth_code = None
received_state = None

class AuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code, received_state
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        received_state = qs.get("state", [None])[0]
        auth_code = qs.get("code", [None])[0]
        error = qs.get("error", [None])[0]

        if error:
            msg = f"Authorization error: {error}"
        elif auth_code and received_state == state:
            msg = "✅ Authorized! You can close this tab."
        else:
            msg = "❌ Authorization failed. Check the terminal."

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *_):
        pass  # suppress logs

server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), AuthHandler)
server.timeout = 120
server.handle_request()

if not auth_code or received_state != state:
    print("Authorization failed (state mismatch or no code).")
    sys.exit(1)

# Step 3: Exchange code for access token
token_data = urlencode({
    "grant_type": "authorization_code",
    "code": auth_code,
    "redirect_uri": f"http://127.0.0.1:{REDIRECT_PORT}/",
    "client_id": CLIENT_ID,
    "code_verifier": code_verifier,
}).encode()

# Try with client secret in header for server-side apps
auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
req = Request("https://accounts.spotify.com/api/token", data=token_data)
req.add_header("Authorization", f"Basic {auth_header}")
req.add_header("Content-Type", "application/x-www-form-urlencoded")

with urlopen(req) as resp:
    token_response = json.loads(resp.read())

access_token = token_response["access_token"]
print(f"Access token obtained. Adding {len(TRACK_IDS)} tracks...")

# Step 4: Add tracks to playlist
add_url = f"https://api.spotify.com/v1/playlists/{PLAYLIST_ID}/tracks"
add_data = json.dumps({"uris": TRACK_IDS}).encode()

req = Request(add_url, data=add_data, method="POST")
req.add_header("Authorization", f"Bearer {access_token}")
req.add_header("Content-Type", "application/json")

with urlopen(req) as resp:
    result = json.loads(resp.read())

snapshot_id = result.get("snapshot_id", "unknown")
print(f"✅ Done! Added {len(TRACK_IDS)} tracks to playlist.")
print(f"Snapshot ID: {snapshot_id}")
print(f"Playlist: https://open.spotify.com/playlist/{PLAYLIST_ID}")
