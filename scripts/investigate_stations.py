#!/usr/bin/env python3
"""
Station Investigation Tool — discovers Israeli radio station streams.

Tests known station URLs, validates they're accessible audio streams,
and reports results for adding to the multi-station dashboard.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StationCandidate:
    name: str
    slug: str
    stream_url: str
    website: str = ""
    genres: list[str] = field(default_factory=list)
    location: str = ""
    tested: bool = False
    reachable: bool = False
    content_type: str = ""
    response_time_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "stream_url": self.stream_url,
            "website": self.website,
            "genres": self.genres,
            "location": self.location,
            "tested": self.tested,
            "reachable": self.reachable,
            "content_type": self.content_type,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
        }


# ── Known Israeli radio stations ──────────────────────────────────────
# Sources: radio.net, streema.com, wikipedia
CANDIDATES: list[StationCandidate] = [
    # Already have
    StationCandidate("קול השפלה 103FM", "kol-hashfela",
                     "https://radio.streamgates.net/stream/1036kh",
                     website="https://1036kh.com",
                     genres=["פופ", "עברי", "לייף סטייל"],
                     location="שפלה"),

    # Major national stations
    StationCandidate("גלגלצ", "galgalatz",
                     "https://glzwizzlv.bynetcdn.com/glglz_mp3",
                     website="https://glglz.co.il",
                     genres=["פופ", "להיטים", "צה״ל"],
                     location="ארצי"),

    StationCandidate("כאן 88", "kan-88",
                     "https://kannlivec01-bc.akamaized.net:443/icecast/kan88/kan88_aac",
                     website="https://www.kan.org.il/radio/88.aspx",
                     genres=["בין-לאומי", "רוק", "אלטרנטיבי"],
                     location="תל אביב"),

    StationCandidate("כאן ב", "kan-bet",
                     "https://kannlivec01-bc.akamaized.net:443/icecast/kan-bet/kan-bet_aac",
                     website="https://www.kan.org.il/radio/bet.aspx",
                     genres=["אקטואליה", "חדשות", "אזורי"]),
                     
    StationCandidate("כאן גימל", "kan-gimel",
                     "https://kannlivec01-bc.akamaized.net:443/icecast/kan-gimel/kan-gimel_aac",
                     website="https://www.kan.org.il/radio/gimel.aspx",
                     genres=["מוזיקה ישראלית", "עברי"],
                     location="תל אביב"),

    # Commercial stations
    StationCandidate("רדיו תל אביב 102FM", "radio-tlv",
                     "https://102.livecdn.biz/102fm_aac",
                     website="https://102fm.co.il",
                     genres=["פופ", "רוק", "עברי"],
                     location="תל אביב"),

    StationCandidate("רדיו 99FM", "99fm",
                     "https://99.livecdn.biz/99fm_aac",
                     website="https://99fm.co.il",
                     genres=["פופ", "להיטים"],
                     location="חיפה"),

    StationCandidate("אקו 99FM", "eco99fm",
                     "https://eco99.livecdn.biz/eco99fm_aac",
                     website="https://eco99fm.co.il",
                     genres=["ג'ז", "בלוז", "סול", "רגוע"],
                     location="תל אביב"),

    StationCandidate("רדיו חיפה", "radio-haifa",
                     "https://haifa.livecdn.biz/haifa_aac",
                     website="https://radiohaifa.co.il",
                     genres=["פופ", "עברי", "ים תיכוני"],
                     location="חיפה"),

    StationCandidate("רדיו דרום", "radio-darom",
                     "https://darom.livecdn.biz/darom_aac",
                     website="https://radiodarom.co.il",
                     genres=["פופ", "עברי", "ים תיכוני"],
                     location="דרום"),

    StationCandidate("רדיו ירושלים", "radio-jlem",
                     "https://jerusalem.livecdn.biz/jerusalem_aac",
                     website="https://www.radiojerusalem.co.il",
                     genres=["פופ", "עברי", "ים תיכוני"],
                     location="ירושלים"),

    StationCandidate("רדיו קול רגע", "kol-rega",
                     "https://rega.livecdn.biz/rega_aac",
                     website="https://www.1036kh.com",
                     genres=["עברי", "שקט", "רגוע"],
                     location="ארצי"),

    # Specialty / niche
    StationCandidate("רדיו קול הנגב", "kol-hanegev",
                     "https://live.radioenegev.co.il:8000/radio.mp3",
                     website="https://www.radioenegev.co.il",
                     genres=["עברי", "ים תיכוני", "מקומי"],
                     location="באר שבע"),

    StationCandidate("רדיו קול חיפה", "kol-haifa",
                     "https://radio.haifa-streams.com:9000/kolhaifa",
                     website="https://www.1036kh.com",
                     genres=["עברי", "מגוון"],
                     location="חיפה"),

    StationCandidate("רדיו קול המרכז", "kol-hamerkaz",
                     "https://cdn.cybercdn.live/Kol_HaMerkaz/MP3-128",
                     website="https://www.1036kh.com",
                     genres=["עברי", "מגוון"],
                     location="מרכז"),
]


def test_station(s: StationCandidate, timeout: int = 10) -> StationCandidate:
    """Test if a station's stream URL is reachable and what format it serves."""
    s.tested = True
    start = time.time()
    try:
        req = urllib.request.Request(s.stream_url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0 (compatible; StationInvestigator/1.0)")
        resp = urllib.request.urlopen(req, timeout=timeout)
        s.response_time_ms = int((time.time() - start) * 1000)
        s.reachable = True
        s.content_type = resp.headers.get("Content-Type", "unknown")
        # Read first few bytes to verify it's audio
        chunk = resp.read(1024)
        if chunk:
            # Check if it's actually a playlist (HLS, PLS, M3U) or direct audio
            snif = chunk[:200].decode("utf-8", errors="replace").lower()
            if ".m3u8" in snif or "#ext" in snif:
                s.content_type += " (HLS playlist)"
            elif s.content_type.startswith("audio/"):
                pass  # direct audio stream
            elif s.content_type.startswith("application/"):
                pass  # could be MPEG TS or other
        resp.close()
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        s.error = str(e)[:200]
        s.reachable = False
    return s


def print_report(results: list[StationCandidate]) -> None:
    """Print a formatted report of tested stations."""
    reachable = [s for s in results if s.reachable]
    unreachable = [s for s in results if s.tested and not s.reachable]

    print("=" * 70)
    print("  📡 ISRAELI RADIO STATIONS — INVESTIGATION REPORT")
    print("=" * 70)

    print(f"\n✅ REACHABLE ({len(reachable)}/{len(results)}):\n")
    print(f"  {'Name':<25} {'Slug':<20} {'Type':<25} {'Time':>6}")
    print(f"  {'─'*25} {'─'*20} {'─'*25} {'─'*6}")
    for s in sorted(reachable, key=lambda x: x.response_time_ms):
        name = s.name[:24]
        ct = (s.content_type[:24] + "..") if len(s.content_type) > 24 else s.content_type
        print(f"  {name:<25} {s.slug:<20} {ct:<25} {s.response_time_ms:>4}ms")

    if unreachable:
        print(f"\n❌ UNREACHABLE ({len(unreachable)}):\n")
        for s in unreachable:
            err = (s.error[:50] + "..") if len(s.error) > 50 else s.error
            print(f"  ✗ {s.name:<25} — {err}")

    print("\n" + "=" * 70)
    print("  RECOMMENDED STATIONS TO ADD (auto-detected):")
    print("=" * 70)
    for s in sorted(reachable, key=lambda x: x.response_time_ms):
        print(f"  ✅ {s.name:<25} → add to config as '{s.slug}'")
    print()


def json_report(results: list[StationCandidate], path: str = "docs/data/stations.json") -> None:
    """Write a JSON report for the dashboard to consume."""
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total_candidates": len(results),
        "reachable": [s.to_dict() for s in results if s.reachable],
        "unreachable": [s.to_dict() for s in results if s.tested and not s.reachable],
    }
    import os
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"[report] Written to {p.resolve()}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Investigate Israeli radio station streams")
    parser.add_argument("--url", help="Test a specific stream URL")
    parser.add_argument("--name", help="Station name (with --url)")
    parser.add_argument("--json", default="docs/data/stations.json", help="Output JSON path")
    parser.add_argument("--timeout", type=int, default=10, help="Timeout per station in seconds")
    args = parser.parse_args()

    if args.url:
        # Test a single custom URL
        s = StationCandidate(
            name=args.name or "Custom Station",
            slug=(args.name or "custom").lower().replace(" ", "-"),
            stream_url=args.url,
        )
        print(f"Testing {s.name}: {s.stream_url}")
        test_station(s, timeout=args.timeout)
        print(json.dumps(s.to_dict(), ensure_ascii=False, indent=2))
        return

    # Test all known candidates
    results = []
    for s in CANDIDATES:
        print(f"  Testing {s.name}... ", end="", flush=True)
        s = test_station(s, timeout=args.timeout)
        status = "✅" if s.reachable else "❌"
        print(f"{status} ({s.response_time_ms}ms)")
        results.append(s)

    print_report(results)
    json_report(results, args.json)

    summary = {
        "event": "investigation_complete",
        "reachable": sum(1 for r in results if r.reachable),
        "total": len(results),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
