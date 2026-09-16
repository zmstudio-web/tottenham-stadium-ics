#!/usr/bin/env python3
"""
Tottenham Hotspur Stadium event scraper -> iCalendar feed.

Builds an .ics file listing everything that fills the stadium and therefore
triggers matchday parking / road / public-transport restrictions around N17:

  * Tottenham Hotspur home fixtures (Premier League via the public
    premierleague.com API; other competitions via football-data.org if a
    free API token is provided)
  * Concerts and non-football events (NFL, boxing, etc.) scraped from
    tottenhamhotspurstadium.com
  * Anything you add by hand in manual_events.json

Standard library only. Run:  python3 scrape.py
Output:  tottenham_stadium_events.ics
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone, date

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    _SSL_CTX = ssl.create_default_context()

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
STADIUM_SITE = "https://www.tottenhamhotspurstadium.com"
OUT = os.path.join(os.path.dirname(__file__), "tottenham_stadium_events.ics")
MANUAL = os.path.join(os.path.dirname(__file__), "manual_events.json")

STADIUM_NAMES = {
    "tottenham hotspur stadium",
    "tottenham hotspur stadium, london",
}
LOCATION = "Tottenham Hotspur Stadium, 782 High Rd, London N17 0BX"

# Roads/parking are managed by Haringey's Event Day Parking Zone. Controls
# typically run from a few hours before to a couple of hours after.
RESTRICTION_NOTE = (
    "Haringey Event Day parking controls and road closures apply around the "
    "stadium (typically from ~2 hours before until ~1-2 hours after the "
    "event). Expect heavy crowding on the Victoria line and buses, and a "
    "resident-permit-only parking zone. Times are approximate - always check "
    "official sources before relying on them."
)


_WARNED_SSL = [False]


def _read_body(r) -> str:
    data = r.read()
    enc = (r.headers.get("Content-Encoding") or "").lower()
    if enc == "gzip":
        data = gzip.decompress(data)
    elif enc in ("deflate", "zlib"):
        data = zlib.decompress(data)
    return data.decode("utf-8", "replace")


def fetch(url: str, headers: dict | None = None, timeout: int = 30) -> str:
    hdrs = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=hdrs)
    for ctx in (_SSL_CTX, ssl._create_unverified_context()):  # noqa: SLF001
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return _read_body(r)
        except (ssl.SSLError, urllib.error.URLError) as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE" in str(reason):
                if not _WARNED_SSL[0]:
                    print("  (SSL verification failed - retrying unverified; "
                          "`pip3 install certifi` fixes this)", file=sys.stderr)
                    _WARNED_SSL[0] = True
                continue  # retry with unverified context
            raise
    return ""


# --------------------------------------------------------------------------
# 1. Stadium website: concerts + non-football events
# --------------------------------------------------------------------------

def scrape_stadium_events() -> list[dict]:
    events: list[dict] = []
    try:
        index = fetch(f"{STADIUM_SITE}/whats-on")
    except Exception as e:  # noqa: BLE001
        print(f"! could not load /whats-on: {e}", file=sys.stderr)
        return events

    slugs = sorted({
        m for m in re.findall(r'/events/(\d+/[a-z0-9-]+)', index)
    })
    print(f"  found {len(slugs)} event page(s) on stadium site")

    for slug in slugs:
        url = f"{STADIUM_SITE}/events/{slug}"
        try:
            page = html.unescape(fetch(url))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {slug}: {e}", file=sys.stderr)
            continue

        page = page.replace("Ÿ", "Y")  # site stylises "JAY-Z" as "JAŸ-Z"
        start = re.search(r'"EventStartDate":"([0-9T:.\-]+Z?)"', page)
        end = re.search(r'"EventEndDate":"([0-9T:.\-]+Z?)"', page)
        if not start:
            continue

        title = None
        for pat in (r'"EventTitle":"([^"]+)"', r'"MetaPageTitle":"([^"]+)"',
                    r'<title>([^<]+)</title>'):
            m = re.search(pat, page)
            if m and m.group(1).strip():
                title = re.sub(r'\s*[|\-]\s*Tottenham Hotspur Stadium.*$', '',
                               m.group(1)).strip()
                break
        title = title or slug.split("/")[-1].replace("-", " ").title()

        if re.search(r'\bnfl\b', title, re.I):
            # NFL "event" pages cover a multi-week window; individual game
            # days are maintained in manual_events.json instead.
            print(f"    · skipping '{title}' (use manual_events.json for game days)")
            continue

        nights_m = re.search(r'"EventNumberOfNights":"(\d+)', page)
        n_nights = int(nights_m.group(1)) if nights_m else 1
        s_dt = _parse(start.group(1))
        e_dt = _parse(end.group(1)) if end else s_dt + timedelta(hours=4)
        duration = max(e_dt - s_dt, timedelta(hours=3))

        # The structured EventStartDate/EndDate on the page often only cover the
        # FIRST night of a multi-night run (e.g. JAY-Z is Fri 4 + Sat 5 Sep but
        # the JSON says 1 night). Recover the real dates from the page text, and
        # fall back to consecutive days if the text can't be parsed.
        dates = _event_dates(page, s_dt, n_nights)

        eid = slug.split("/")[0]
        for d in dates:
            night_start = s_dt.replace(year=d.year, month=d.month, day=d.day)
            label = title if len(dates) == 1 else f"{title} (night {dates.index(d)+1})"
            events.append({
                "uid": f"stadium-{eid}-{d:%Y%m%d}",
                "summary": label,
                "start": night_start,
                "end": night_start + duration,
                "url": url,
                "category": "Concert/Event",
                "note": (f"Part of a {n_nights}-night run. " if n_nights > 1 else "")
                + "Scraped from tottenhamhotspurstadium.com - exact start time is "
                  "approximate.",
            })
            print(f"    - {d:%Y-%m-%d}  {label}")
    return events


_MONTHS = ("january february march april may june july august september "
           "october november december").split()


def _event_dates(page: str, start: datetime, n_nights: int) -> list[date]:
    """Best-effort list of the actual event dates, newest logic first."""
    year_m = re.search(r'"EventYear":"?(\d{4})', page)
    year = int(year_m.group(1)) if year_m else start.year
    found: set[date] = set()
    for dd, mon in re.findall(
        r'\b(\d{1,2})(?:st|nd|rd|th)?\s+'
        r'(January|February|March|April|May|June|July|August|September|'
        r'October|November|December)\b', page, re.I):
        try:
            m = _MONTHS.index(mon.lower()) + 1
            cand = date(year, m, int(dd))
        except ValueError:
            continue
        # keep dates within ~2 weeks of the announced start
        if abs((cand - start.date()).days) <= 14:
            found.add(cand)
    if len(found) >= max(n_nights, 1) and len(found) <= 6:
        return sorted(found)
    # fallback: consecutive nights from the announced start
    return [start.date() + timedelta(days=i) for i in range(max(n_nights, 1))]


def _parse(s: str) -> datetime:
    s = s.replace("Z", "").split(".")[0]
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# 2. Premier League fixtures (public premierleague.com API, no key)
# --------------------------------------------------------------------------

TOT_PL_ID = 21


def scrape_pl_fixtures() -> list[dict]:
    events: list[dict] = []
    base = ("https://footballapi.pulselive.com/football/fixtures"
            "?comps=1&teams=21&pageSize=40&sort=asc&statuses=U,L&altIds=true")
    headers = {
        "Origin": "https://www.premierleague.com",
        "Referer": "https://www.premierleague.com/",
    }
    page = 0
    while True:
        try:
            data = json.loads(fetch(f"{base}&page={page}", headers=headers))
        except Exception as e:  # noqa: BLE001
            print(f"! PL API failed: {e}", file=sys.stderr)
            break
        for f in data.get("content", []):
            ground = (f.get("ground") or {}).get("name", "")
            if ground.strip().lower() not in STADIUM_NAMES:
                continue
            millis = (f.get("kickoff") or {}).get("millis")
            if not millis:
                continue  # date not confirmed yet
            ko = datetime.fromtimestamp(millis / 1000, tz=timezone.utc)
            teams = [t["team"]["name"] for t in f.get("teams", [])]
            summary = " v ".join(teams) if teams else "Tottenham Hotspur (home)"
            events.append({
                "uid": f"plfix-{int(f.get('id', millis))}",
                "summary": f"\u26bd {summary}",
                "start": ko,
                "end": ko + timedelta(hours=2),
                "url": "https://www.tottenhamhotspur.com/fixtures-and-results/",
                "category": "Football",
                "note": "Premier League. " + RESTRICTION_NOTE,
            })
            print(f"    - {ko:%Y-%m-%d %H:%M}  {summary}  (Premier League)")
        info = data.get("pageInfo", {})
        page += 1
        if page >= info.get("numPages", 1):
            break
    return events


# --------------------------------------------------------------------------
# 3. Other competitions via football-data.org (optional free token)
# --------------------------------------------------------------------------

def scrape_football_data() -> list[dict]:
    token = os.environ.get("FOOTBALL_DATA_TOKEN", "").strip()
    if not token:
        print("  (set FOOTBALL_DATA_TOKEN to also pick up cup / European home games)")
        return []
    events: list[dict] = []
    today = date.today().isoformat()
    end = (date.today() + timedelta(days=365)).isoformat()
    url = (f"https://api.football-data.org/v4/teams/73/matches"
           f"?dateFrom={today}&dateTo={end}")
    try:
        data = json.loads(fetch(url, headers={"X-Auth-Token": token}))
    except Exception as e:  # noqa: BLE001
        print(f"! football-data.org failed: {e}", file=sys.stderr)
        return events
    for m in data.get("matches", []):
        if (m.get("homeTeam") or {}).get("id") != 73:
            continue
        if m.get("status") in ("FINISHED", "AWARDED"):
            continue
        ko = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        comp = (m.get("competition") or {}).get("name", "")
        if comp == "Premier League":
            continue  # already covered, avoids duplicates
        away = (m.get("awayTeam") or {}).get("name", "opponent")
        events.append({
            "uid": f"fd-{m['id']}",
            "summary": f"\u26bd Tottenham Hotspur v {away}",
            "start": ko,
            "end": ko + timedelta(hours=2),
            "url": "https://www.tottenhamhotspur.com/fixtures-and-results/",
            "category": "Football",
            "note": f"{comp}. " + RESTRICTION_NOTE,
        })
        print(f"    - {ko:%Y-%m-%d %H:%M}  v {away}  ({comp})")
    return events


# --------------------------------------------------------------------------
# 4. Manual additions / overrides
# --------------------------------------------------------------------------

def load_manual() -> list[dict]:
    if not os.path.exists(MANUAL):
        return []
    try:
        raw = json.load(open(MANUAL))
    except Exception as e:  # noqa: BLE001
        print(f"! manual_events.json invalid: {e}", file=sys.stderr)
        return []
    out = []
    for i, ev in enumerate(raw):
        try:
            s = datetime.fromisoformat(ev["start"])
            if s.tzinfo is None:
                s = s.replace(tzinfo=timezone.utc)
            e = ev.get("end")
            e = (datetime.fromisoformat(e).replace(tzinfo=s.tzinfo)
                 if e else s + timedelta(hours=3))
            out.append({
                "uid": ev.get("uid", f"manual-{i}-{s:%Y%m%d}"),
                "summary": ev["summary"],
                "start": s,
                "end": e,
                "url": ev.get("url", ""),
                "category": ev.get("category", "Concert/Event"),
                "note": ev.get("note", "Manually added entry. ") + " " + RESTRICTION_NOTE,
                "all_day": ev.get("all_day", False),
            })
            print(f"    - {s:%Y-%m-%d}  {ev['summary']}  (manual)")
        except Exception as e:  # noqa: BLE001
            print(f"! manual entry {i} skipped: {e}", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# iCalendar output
# --------------------------------------------------------------------------

def esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def fold(line: str) -> str:
    """Fold one logical line to <=75 octets per RFC 5545 (no pre-existing CRLF)."""
    out = []
    while len(line.encode("utf-8")) > 75:
        cut = 75
        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def to_ics(events: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//spurs-stadium-calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Tottenham Hotspur Stadium events",
        "X-WR-CALDESC:Home fixtures + concerts/events that trigger parking & "
        "transport restrictions around N17",
        "X-PUBLISHED-TTL:PT12H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    seen = set()
    for ev in sorted(events, key=lambda e: e["start"]):
        if ev["uid"] in seen:
            continue
        seen.add(ev["uid"])
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['uid']}@spurs-stadium-calendar")
        lines.append(f"DTSTAMP:{now}")
        if ev.get("all_day"):
            lines.append(f"DTSTART;VALUE=DATE:{ev['start']:%Y%m%d}")
            lines.append(f"DTEND;VALUE=DATE:{(ev['end'] + timedelta(days=1)):%Y%m%d}")
        else:
            lines.append(f"DTSTART:{ev['start'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}")
            lines.append(f"DTEND:{ev['end'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}")
        lines.append(f"SUMMARY:{esc(ev['summary'])}")
        lines.append(f"LOCATION:{esc(LOCATION)}")
        desc = ev.get("note", "")
        if ev.get("url"):
            desc += f"\n\n{ev['url']}"
            lines.append(f"URL:{ev['url']}")
        lines.append(f"DESCRIPTION:{esc(desc)}")
        lines.append(f"CATEGORIES:{esc(ev.get('category', 'Event'))}")
        # Informational only: show as "free" (not busy) and never alarm.
        lines.append("TRANSP:TRANSPARENT")
        lines.append("X-MICROSOFT-CDO-BUSYSTATUS:FREE")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main() -> int:
    print("Tottenham Hotspur Stadium -> iCal\n")
    events: list[dict] = []
    print("[1/4] Stadium website concerts & events")
    events += scrape_stadium_events()
    print("[2/4] Premier League home fixtures")
    events += scrape_pl_fixtures()
    print("[3/4] Other competitions (football-data.org)")
    events += scrape_football_data()
    print("[4/4] Manual entries")
    events += load_manual()

    if not events:
        print("\nNo events found - not overwriting existing feed.", file=sys.stderr)
        return 1

    ics = to_ics(events)
    with open(OUT, "w", newline="") as f:
        f.write(ics)
    print(f"\nWrote {len(set(e['uid'] for e in events))} events -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
