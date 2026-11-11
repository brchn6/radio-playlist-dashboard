#!/usr/bin/env python3
"""ops_model.py - the operations picture as pure functions (no I/O).

One vocabulary shared by the writer and the readers:

- scripts/ops_monitor.py (the ONLY writer) turns raw inputs (proxy logs,
  journal, boot list, publish.log, saved tracks) into rows for the Supabase
  `system_events` table, using the event types below.
- scripts/generate_data.py reads those rows back from the DB and calls
  build_ops() to produce `uptime.json.ops` for the dashboard.
- scripts/ops_digest.py calls the same build_ops() and prints `digest`.

Event types written by ops_monitor (all bounded: started_at, and ended_at
unless still ongoing):

  host_down       source=host        the machine was off/rebooting
  listening_gap   source=<slug>      no audio sample reached recognition for >10 min
  saving_gap      source=<slug>      >=3 recognized songs in a row never reached the DB
  collector_down  source=collector   the collector process died until systemd restarted it
  publish_failed  source=publish     publish runs that failed to upload, until one succeeded
  ops_station_day source=<slug>      one row per station per local day: coverage marker +
                                     {"plays": N, "saved": M} in description (JSON)

Public text rule (Bar, 2026-11-11): titles, impacts and the digest are plain
Hebrew. No hostnames, IPs, file paths or raw error text ever leave this module;
the machine detail stays in the private `description` column.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

IL_TZ = ZoneInfo("Asia/Jerusalem")

INCIDENT_TYPES = ("host_down", "listening_gap", "saving_gap", "collector_down", "publish_failed")
DAY_TYPE = "ops_station_day"
OPS_EVENT_TYPES = INCIDENT_TYPES + (DAY_TYPE,)

STAGE_OF = {
    "host_down": "host",
    "listening_gap": "listening",
    "saving_gap": "saving",
    "collector_down": "collector",
    "publish_failed": "publishing",
}

# Order = the pipeline a song travels through.
STAGES = [
    ("listening", "האזנה",
     "האם שמענו כל תחנה ושלחנו דגימות לזיהוי. נמדד לפי דופק ההאזנה של כל תחנה: הפסקה של יותר מ-10 דקות נחשבת תקלה."),
    ("saving", "שמירה",
     "מכל השירים שזוהו, כמה נשמרו בבסיס הנתונים. נמדד בהשוואה בין השירים שזוהו לשירים שנשמרו."),
    ("collector", "רכיב איסוף",
     "התוכנה שאוספת את הזיהויים ושומרת אותם. נמדד לפי נפילות והפעלות מחדש."),
    ("publishing", "פרסום לאתר",
     "העלאת הנתונים המעודכנים לאתר. נמדד לפי ריצות פרסום שנכשלו ברצף."),
    ("host", "שרת",
     "המחשב שמריץ את כל המערכת. נמדד לפי הפעלות מחדש וזמן שבו היה כבוי."),
]

# Thresholds (one place). Day strips: any lost minute is a glitch worth seeing.
FAILED_MINUTES = 60          # a day/incident with >= 60 lost minutes is red
LISTEN_OK_PCT = 99.5         # station/stage listening % at or above -> green
LISTEN_PARTIAL_PCT = 95.0
SAVED_OK_PCT = 98.0          # share of recognized songs that were saved
SAVED_PARTIAL_PCT = 90.0
MAX_INCIDENTS = 20

RANK = {"unknown": -1, "ok": 0, "partial": 1, "failed": 2}


# ── small helpers ──────────────────────────────────────────────────────

def parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def worst(statuses: Iterable[str]) -> str:
    known = [s for s in statuses if s != "unknown"]
    if not known:
        return "unknown"
    return max(known, key=lambda s: RANK[s])


def union_seconds(intervals: Iterable[tuple[datetime, datetime]],
                  lo: datetime, hi: datetime) -> float:
    """Total seconds covered by the union of intervals, clipped to [lo, hi]."""
    clipped = sorted((max(a, lo), min(b, hi)) for a, b in intervals if b > lo and a < hi)
    total, cur_a, cur_b = 0.0, None, None
    for a, b in clipped:
        if b <= a:
            continue
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                total += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        total += (cur_b - cur_a).total_seconds()
    return total


def local_day_bounds(day: str, tz: ZoneInfo = IL_TZ) -> tuple[datetime, datetime]:
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=tz)
    end = (start + timedelta(days=1, hours=2)).replace(hour=0)  # DST-safe next midnight
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_days(now: datetime, n: int, tz: ZoneInfo = IL_TZ) -> list[str]:
    today = now.astimezone(tz).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def day_status_from_minutes(minutes: float) -> str:
    if minutes <= 0:
        return "ok"
    return "failed" if minutes >= FAILED_MINUTES else "partial"


def pct_status(pct: float | None, ok: float, partial: float) -> str:
    if pct is None:
        return "unknown"
    if pct >= ok:
        return "ok"
    return "partial" if pct >= partial else "failed"


def say_duration(seconds: float) -> str:
    """Plain Hebrew duration: 'כ-10 שניות', 'כ-12 דקות', 'כ-2 שעות ו-15 דקות'."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"כ-{max(1, seconds)} שניות"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return "כדקה" if minutes == 1 else f"כ-{minutes} דקות"
    hours, rest = divmod(minutes, 60)
    h = "שעה" if hours == 1 else ("שעתיים" if hours == 2 else f"{hours} שעות")
    text = f"כ{h}" if hours <= 2 else f"כ-{h}"
    return text + (f" ו-{rest} דקות" if rest else "")


def _description_json(row: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(row.get("description") or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


# ── incident text (public, plain Hebrew) ───────────────────────────────

def incident_text(event_type: str, station_name: str | None, seconds: float,
                  ongoing: bool, detail: dict[str, Any]) -> tuple[str, str]:
    dur = say_duration(seconds)
    if event_type == "host_down":
        title, impact = "השרת שמריץ את המערכת הופעל מחדש", f"כל התחנות לא הוקלטו {dur}"
    elif event_type == "listening_gap":
        title, impact = f"ההאזנה ל{station_name} נקטעה", f"{dur} בלי האזנה לתחנה"
        if detail.get("cause") == "stream":
            impact += " (השידור של התחנה לא היה זמין לנו)"
        elif detail.get("cause") == "after_reboot":
            impact += " (התחנה חזרה לאט אחרי הפעלה מחדש של השרת)"
    elif event_type == "saving_gap":
        n = detail.get("missed")
        songs = f"{n} שירים" if isinstance(n, int) and n > 0 else "שירים"
        title, impact = f"שירים ב{station_name} זוהו ולא נשמרו", f"{songs} לא נשמרו במשך {dur}"
    elif event_type == "collector_down":
        title, impact = "רכיב האיסוף נפל והופעל מחדש אוטומטית", f"{dur} בלי שמירת שירים"
    elif event_type == "publish_failed":
        n = detail.get("failed_runs")
        tries = "ניסיון עדכון אחד נכשל" if n == 1 else (
            f"{n} ניסיונות עדכון נכשלו" if isinstance(n, int) and n > 1 else "ניסיונות עדכון נכשלו")
        title, impact = "עדכון הנתונים באתר נכשל", f"{tries}, העדכון הבא הצליח אחרי {dur}"
        if ongoing:
            impact = f"{tries} ועדיין לא הצליח, {dur}"
            return title, impact
    else:
        title, impact = "תקלה", dur
    if ongoing:
        impact += ", ועדיין נמשך"
    return title, impact


# ── the builder ────────────────────────────────────────────────────────

def build_ops(rows: list[dict[str, Any]], stations: list[dict[str, Any]],
              now: datetime, window_days: int = 7) -> dict[str, Any]:
    """Build `uptime.json.ops` from system_events rows. Pure: no I/O.

    rows: system_events dicts (event_type, source, started_at, ended_at,
    description); non-ops types are ignored. stations: [{slug, name}].

    Honesty rules encoded here:
    - The window is the 7 local days shown in the strips. A day with no
      ops_station_day row was not observed: it is `unknown` (gray) and is left
      out of every percentage, instead of silently counting as 100%.
    - Percentages are per station first. A single dark station drags the
      listening stage down and turns its day red; it is never hidden by the
      other seven still playing (the old any-station metric did exactly that).
    """
    now = now.astimezone(timezone.utc)
    slugs = [s["slug"] for s in stations]
    names = {s["slug"]: s.get("name") or s["slug"] for s in stations}
    days = local_days(now, window_days)
    win_lo = local_day_bounds(days[0])[0]

    incidents_raw: list[dict[str, Any]] = []
    day_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        et = r.get("event_type")
        start = parse_ts(r.get("started_at"))
        if start is None or et not in OPS_EVENT_TYPES:
            continue
        end = parse_ts(r.get("ended_at"))
        if et == DAY_TYPE:
            day = start.astimezone(IL_TZ).strftime("%Y-%m-%d")
            day_rows[(r.get("source") or "", day)] = _description_json(r)
            continue
        if (end or now) <= win_lo or start > now:
            continue
        incidents_raw.append({"type": et, "source": r.get("source") or "", "start": start,
                              "end": end, "detail": _description_json(r)})

    covered_days = [d for d in days if any((s, d) in day_rows for s in slugs)]
    # Publishing is observed only on days with dated publish runs.
    publish_days = [d for d in covered_days
                    if any(int(day_rows.get((s, d), {}).get("publish_runs", 0) or 0) > 0 for s in slugs)]

    def spans(day_list: list[str]) -> list[tuple[datetime, datetime]]:
        return [(max(local_day_bounds(d)[0], win_lo), min(local_day_bounds(d)[1], now))
                for d in day_list]

    def intervals(et: str, source: str | None = None) -> list[tuple[datetime, datetime]]:
        return [(i["start"], i["end"] or now) for i in incidents_raw
                if i["type"] == et and (source is None or i["source"] == source)]

    host_iv = intervals("host_down")

    def lost_in(iv: list[tuple[datetime, datetime]], lo: datetime, hi: datetime) -> float:
        return union_seconds(iv, lo, hi)

    def pct_from_lost(iv: list[tuple[datetime, datetime]],
                      day_list: list[str] | None = None) -> float | None:
        sp = spans(covered_days if day_list is None else day_list)
        total = sum((b - a).total_seconds() for a, b in sp if b > a)
        if total <= 0:
            return None
        lost = sum(lost_in(iv, a, b) for a, b in sp)
        return round(max(0.0, 100.0 * (1 - lost / total)), 1)

    # ── per station ──
    station_out: dict[str, Any] = {}
    listen_iv = {s: host_iv + intervals("listening_gap", s) for s in slugs}
    for s in slugs:
        listening_pct = pct_from_lost(listen_iv[s])
        plays = sum(int(day_rows.get((s, d), {}).get("plays", 0) or 0) for d in covered_days)
        saved = sum(int(day_rows.get((s, d), {}).get("saved", 0) or 0) for d in covered_days)
        saved_pct = round(100.0 * saved / plays, 1) if plays else None
        status = worst([pct_status(listening_pct, LISTEN_OK_PCT, LISTEN_PARTIAL_PCT),
                        pct_status(saved_pct, SAVED_OK_PCT, SAVED_PARTIAL_PCT)])
        station_out[s] = {"listening_pct": listening_pct, "saved_pct": saved_pct,
                          "plays": plays, "saved": saved, "status": status}

    # ── stages ──
    def strip(minutes_for_day, extra_status=None, day_list=None) -> list[dict[str, Any]]:
        out = []
        observed = covered_days if day_list is None else day_list
        for d in days:
            if d not in observed:
                out.append({"date": d, "status": "unknown", "minutes_lost": None})
                continue
            lo, hi = local_day_bounds(d)
            hi = min(hi, now)
            seconds = minutes_for_day(lo, hi)
            # Any lost second is a visible glitch; show at least 1 minute for it.
            minutes = max(1, int(round(seconds / 60))) if seconds > 0 else 0
            status = day_status_from_minutes(minutes)
            if extra_status is not None:
                status = worst([status, extra_status(d)])
            out.append({"date": d, "status": status, "minutes_lost": minutes})
        return out

    def day_saved_status(d: str) -> str:
        statuses = []
        for s in slugs:
            row = day_rows.get((s, d))
            if row and row.get("plays"):
                pct = 100.0 * int(row.get("saved", 0)) / int(row["plays"])
                statuses.append(pct_status(pct, SAVED_OK_PCT, SAVED_PARTIAL_PCT))
        return worst(statuses) if statuses else "ok"

    listen_pcts = [v["listening_pct"] for v in station_out.values() if v["listening_pct"] is not None]
    total_plays = sum(v["plays"] for v in station_out.values())
    total_saved = sum(v["saved"] for v in station_out.values())
    stage_pct = {
        "listening": round(sum(listen_pcts) / len(listen_pcts), 1) if listen_pcts else None,
        "saving": round(100.0 * total_saved / total_plays, 1) if total_plays else None,
        "collector": pct_from_lost(intervals("collector_down")),
        "publishing": pct_from_lost(intervals("publish_failed"), publish_days),
        "host": pct_from_lost(host_iv),
    }
    strips = {
        # Worst single station that day, so one dark station is never averaged away.
        "listening": strip(lambda lo, hi: max((lost_in(listen_iv[s], lo, hi) for s in slugs), default=0)),
        "saving": strip(lambda lo, hi: max((lost_in(intervals("saving_gap", s), lo, hi) for s in slugs),
                                           default=0), day_saved_status),
        "collector": strip(lambda lo, hi: lost_in(intervals("collector_down"), lo, hi)),
        "publishing": strip(lambda lo, hi: lost_in(intervals("publish_failed"), lo, hi),
                            day_list=publish_days),
        "host": strip(lambda lo, hi: lost_in(host_iv, lo, hi)),
    }
    stage_status = {
        "listening": worst(pct_status(v["listening_pct"], LISTEN_OK_PCT, LISTEN_PARTIAL_PCT)
                           for v in station_out.values()),
        "saving": worst(pct_status(v["saved_pct"], SAVED_OK_PCT, SAVED_PARTIAL_PCT)
                        for v in station_out.values()),
        "collector": pct_status(stage_pct["collector"], LISTEN_OK_PCT, LISTEN_PARTIAL_PCT),
        "publishing": pct_status(stage_pct["publishing"], LISTEN_OK_PCT, LISTEN_PARTIAL_PCT),
        "host": pct_status(stage_pct["host"], LISTEN_OK_PCT, LISTEN_PARTIAL_PCT),
    }
    # An ongoing incident is never green, whatever the percentage says.
    for i in incidents_raw:
        if i["end"] is None:
            st = STAGE_OF[i["type"]]
            stage_status[st] = worst([stage_status[st], "partial"])
    stages_out = [{"id": sid, "label": label, "status": stage_status[sid],
                   "uptime_pct": stage_pct[sid], "tip": tip, "days": strips[sid]}
                  for sid, label, tip in STAGES]

    # ── incidents ──
    incidents = []
    for i in sorted(incidents_raw, key=lambda x: x["start"], reverse=True)[:MAX_INCIDENTS]:
        end = i["end"]
        seconds = ((end or now) - i["start"]).total_seconds()
        if i["type"] in ("host_down", "collector_down"):
            affected = list(slugs)
        elif i["type"] == "publish_failed":
            affected = []
        else:
            affected = [i["source"]] if i["source"] in names else []
        title, impact = incident_text(i["type"], names.get(i["source"]), seconds,
                                      end is None, i["detail"])
        incidents.append({
            "started_at": iso(i["start"]), "ended_at": iso(end),
            "minutes": int(round(seconds / 60)), "stage": STAGE_OF[i["type"]],
            "stations": affected,
            "severity": "failed" if seconds >= FAILED_MINUTES * 60 else "partial",
            "title": title, "impact": impact,
            "status": "ongoing" if end is None else "recovered",
        })

    overall = worst(stage_status.values())
    n = len(incidents_raw)
    ongoing = sum(1 for i in incidents_raw if i["end"] is None)
    if not covered_days:
        headline = "אין עדיין נתוני תפעול לתקופה הזו"
    elif n == 0:
        headline = f"אין תקלות ב-{window_days} הימים האחרונים"
    elif ongoing:
        headline = f"{n} תקלות ב-{window_days} הימים האחרונים, {ongoing} עדיין פתוחות"
    else:
        headline = f"{n} תקלות ב-{window_days} הימים האחרונים, כולן חלפו"

    ops = {
        "generated_at": iso(now), "window_days": window_days,
        "overall": overall, "headline": headline,
        "stages": stages_out, "stations": station_out, "incidents": incidents,
    }
    ops["digest"] = build_digest(ops, names, now)
    return ops


STATUS_WORD = {"ok": "תקין", "partial": "חלקי", "failed": "תקלה", "unknown": "לא ידוע"}


def build_digest(ops: dict[str, Any], names: dict[str, str], now: datetime) -> str:
    """Plain-text daily summary (Hebrew). The same text ops_digest.py prints."""
    local_now = now.astimezone(IL_TZ)
    lines = [f"סיכום תפעול יומי, {local_now.strftime('%d.%m.%Y')}",
             f"מצב כללי: {STATUS_WORD[ops['overall']]}. {ops['headline']}."]
    since = now - timedelta(hours=24)
    recent = [i for i in ops["incidents"]
              if (parse_ts(i["ended_at"]) or now) >= since]
    if recent:
        lines.append(f"ב-24 השעות האחרונות ({len(recent)}):")
        for i in recent:
            t = parse_ts(i["started_at"]).astimezone(IL_TZ).strftime("%d.%m %H:%M")
            lines.append(f"* {t} {i['title']}: {i['impact']}")
    else:
        lines.append("ב-24 השעות האחרונות: אין תקלות.")
    parts = []
    for st in ops["stages"]:
        pct = "אין נתונים" if st["uptime_pct"] is None else f"{st['uptime_pct']}%"
        parts.append(f"{st['label']} {pct}")
    lines.append(f"{ops['window_days']} ימים: " + ", ".join(parts) + ".")
    attention = []
    for slug, v in ops["stations"].items():
        if v["status"] in ("partial", "failed"):
            bits = []
            if v["listening_pct"] is not None and v["listening_pct"] < LISTEN_OK_PCT:
                bits.append(f"האזנה {v['listening_pct']}%")
            if v["saved_pct"] is not None and v["saved_pct"] < SAVED_OK_PCT:
                bits.append(f"נשמרו {v['saved_pct']}% מהשירים שזוהו")
            attention.append(f"{names.get(slug, slug)} ({', '.join(bits) or STATUS_WORD[v['status']]})")
    if attention:
        lines.append("דורש תשומת לב: " + "; ".join(attention) + ".")
    return "\n".join(lines)
