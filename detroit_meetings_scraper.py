#!/usr/bin/env python3
"""
Detroit eScribe scraper
- Scrapes the public Detroit City Council eScribe instance for meetings.
- Emits JSON and ICS files.

Usage:
  python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data

Notes:
  - This is a best-effort parser; eScribe HTML can vary. We also try to parse any per-meeting ICS links if present
    to get authoritative start/end times.
  - Timezone is America/Detroit (handles DST).
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import pytz
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar, Event

BASE = "https://pub-detroitmi.escribemeetings.com/"
TZ = pytz.timezone("America/Detroit")
HEADERS = {"User-Agent": "DetroitMeetingsScraper/1.0 (+https://example.com)"}

@dataclass
class Meeting:
    uid: str
    title: str
    body: str
    start: str
    end: str
    all_day: bool
    timezone: str
    location: str
    address: str
    virtual_link: Optional[str]
    agenda_url: Optional[str]
    minutes_url: Optional[str]
    detail_url: str
    source: str = "escribe-detroit"

def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def month_url(year: int, month: int) -> str:
    return f"{BASE}?Year={year}&Month={month}"

def parse_month(url: str) -> List[str]:
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    detail_links = []
    # Broad selector to catch typical eScribe patterns
    for a in soup.select("a[href*='Meeting?Id'], a[href*='Meeting?']"):
        href = a.get("href") or ""
        if "Meeting" in href:
            detail_links.append(urljoin(BASE, href))
    # Also capture links within event tiles/cards that point to details
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "Meeting?Id=" in href:
            detail_links.append(urljoin(BASE, href))
    # unique + stable order
    return sorted(set(detail_links))

def try_parse_ics_from_detail(soup: BeautifulSoup) -> Optional[dict]:
    # Look for Add to Calendar link (.ics)
    for a in soup.select("a[href$='.ics'], a[href*='AddToCalendar'], a[href*='.ics']"):
        href = a.get("href", "")
        if not href:
            continue
        try:
            ics_url = urljoin(BASE, href)
            r = requests.get(ics_url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and "BEGIN:VCALENDAR" in r.text:
                cal = Calendar.from_ical(r.content)
                for comp in cal.walk():
                    if comp.name == "VEVENT":
                        start = comp.get("dtstart").dt
                        end = comp.get("dtend").dt if comp.get("dtend") else None
                        summary = str(comp.get("summary") or "")
                        loc = str(comp.get("location") or "")
                        return {
                            "start": start,
                            "end": end,
                            "summary": summary,
                            "location": loc,
                        }
        except Exception:
            continue
    return None

def extract_meeting_id(url: str) -> Optional[str]:
    # Attempt to extract numeric ID from query string
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        mid = qs.get("Id") or qs.get("id") or []
        return str(mid[0]) if mid else None
    except Exception:
        return None

def parse_detail(url: str) -> Optional[Meeting]:
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")

    # Title candidates
    title_el = soup.select_one("h1") or soup.select_one(".meeting-title") or soup.select_one(".page-title")
    raw_title = clean(title_el.get_text() if title_el else "")
    body = ""

    # Breadcrumb/body name
    crumb = soup.select_one(".breadcrumb, nav.breadcrumb")
    if crumb:
        parts = [clean(x) for x in re.split(r"[›>/]", crumb.get_text()) if clean(x)]
        if len(parts) >= 2:
            body = parts[-2]
        elif parts:
            body = parts[-1]

    # If the title looks like the body (COMMON IN ESCRIBE), adjust
    if not body and re.search(r"(COMMITTEE|COMMISSION|COUNCIL)", raw_title.upper()):
        body = raw_title
        title = "Meeting"
    else:
        title = raw_title or "Meeting"

    # Extract datetime (try ICS first)
    ics_info = try_parse_ics_from_detail(soup)
    start_dt = None
    end_dt = None
    location = ""

    if ics_info:
        start_dt = ics_info.get("start")
        end_dt = ics_info.get("end")
        if isinstance(start_dt, dt.datetime) and start_dt.tzinfo is None:
            start_dt = TZ.localize(start_dt)
        if isinstance(end_dt, dt.datetime) and end_dt and end_dt.tzinfo is None:
            end_dt = TZ.localize(end_dt)
        if ics_info.get("location"):
            location = clean(ics_info["location"])

    # Fallback to page text
    if not start_dt:
        text = soup.get_text(" ")
        m = re.search(r"(\w{3,9}\s+\d{1,2},\s+\d{4})[, ]+\s*(\d{1,2}:\d{2}\s*(AM|PM)?)", text, re.I)
        if m:
            date_str, time_str = m.group(1), m.group(2)
            start_dt = TZ.localize(dt.datetime.strptime(f"{date_str} {time_str}", "%B %d, %Y %I:%M %p"))
        else:
            m2 = re.search(r"(\d{4}-\d{2}-\d{2})[ T]+(\d{2}:\d{2})", text)
            if m2:
                start_dt = TZ.localize(dt.datetime.strptime(f"{m2.group(1)} {m2.group(2)}", "%Y-%m-%d %H:%M"))
    if not start_dt:
        return None

    if not end_dt:
        end_dt = start_dt + dt.timedelta(hours=2)

    # Agenda/minutes links
    def first_link(label_regex):
        for link in soup.select("a[href]"):
            if re.search(label_regex, link.get_text(strip=True), re.I):
                return urljoin(BASE, link["href"])
        return None

    agenda_url = first_link(r"agenda")
    minutes_url = first_link(r"minutes")

    virtual_link = None
    for link in soup.select("a[href]"):
        href = link.get("href", "")
        if any(k in href.lower() for k in ["zoom.us", "teams.microsoft", "youtube.com", "facebook.com/live", "livestream"]):
            virtual_link = href
            break

    if not location:
        loc_el = soup.find(lambda tag: tag.name in ["p", "div", "li"] and re.search(r"Location", tag.get_text(), re.I))
        if loc_el:
            location = clean(re.sub(r"(?i)Location[:\s]*", "", loc_el.get_text()))

    full_title = f"{body}: {title}" if body else title

    # UID: include meeting ID if available for stability
    mid = extract_meeting_id(url) or ""
    uid_src = f"{full_title}|{start_dt.astimezone(dt.timezone.utc).isoformat()}|{mid}|{url}"
    uid = hashlib.sha1(uid_src.encode()).hexdigest() + "@detroit-escribe"

    return Meeting(
        uid=uid,
        title=clean(title),
        body=clean(body),
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        all_day=False,
        timezone="America/Detroit",
        location=clean(location),
        address="",
        virtual_link=virtual_link,
        agenda_url=agenda_url,
        minutes_url=minutes_url,
        detail_url=url,
    )

def to_ics(items: List[Meeting]) -> str:
    cal = Calendar()
    cal.add("prodid", "-//Detroit eScribe Scraper//EN")
    cal.add("version", "2.0")
    now_utc = dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)

    for it in items:
        event = Event()
        event.add("uid", it.uid)
        start = dt.datetime.fromisoformat(it.start)
        end = dt.datetime.fromisoformat(it.end)
        # Ensure TZ-aware
        if start.tzinfo is None:
            start = TZ.localize(start)
        if end.tzinfo is None:
            end = TZ.localize(end)
        event.add("dtstart", start)
        event.add("dtend", end)
        summary = f"{it.body}: {it.title}" if it.body else it.title
        event.add("summary", summary)
        if it.location:
            event.add("location", it.location)
        desc_lines = []
        if it.agenda_url: desc_lines.append(f"Agenda: {it.agenda_url}")
        if it.minutes_url: desc_lines.append(f"Minutes: {it.minutes_url}")
        if it.virtual_link: desc_lines.append(f"Virtual: {it.virtual_link}")
        desc_lines.append(f"Details: {it.detail_url}")
        event.add("description", "\\n".join(desc_lines))
        event.add("url", it.detail_url)
        event.add("dtstamp", now_utc)
        event.add("status", "CONFIRMED")
        cal.add_component(event)
    return cal.to_ical().decode("utf-8")

def crawl(year: int, months_ahead: int, months_behind: int, pause: float = 0.6) -> List[Meeting]:
    today = dt.date.today()
    target_months = set()
    for d in range(-months_behind, months_ahead + 1):
        # Calculate month offset
        y = year if d >= 0 else year
        month = today.month + d
        y_offset = (month - 1) // 12
        m_norm = ((month - 1) % 12) + 1
        target_months.add((year + y_offset, m_norm))
    details = set()
    for y, m in sorted(target_months):
        url = month_url(y, m)
        try:
            for du in parse_month(url):
                details.add(du)
            time.sleep(pause)
        except Exception as e:
            print(f"WARN month {y}-{m}: {e}")

    items: List[Meeting] = []
    for du in sorted(details):
        try:
            mtg = parse_detail(du)
            if mtg:
                items.append(mtg)
        except Exception as e:
            print(f"WARN detail {du}: {e}")
        time.sleep(pause)

    # de-dupe by (title, body, start)
    seen = set()
    unique: List[Meeting] = []
    for it in items:
        key = (it.title.lower(), it.body.lower(), it.start)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda x: x.start)
    return unique

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=dt.date.today().year, help="Base year to crawl (defaults to current year)")
    ap.add_argument("--months-ahead", type=int, default=2, help="How many months ahead to crawl")
    ap.add_argument("--months-behind", type=int, default=1, help="How many months behind to crawl")
    ap.add_argument("--outdir", type=str, default="data", help="Output directory")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    meetings = crawl(args.year, args.months_ahead, args.months_behind)

    json_path = os.path.join(args.outdir, "detroit-meetings.json")
    ics_path = os.path.join(args.outdir, "detroit-meetings.ics")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in meetings], f, indent=2, ensure_ascii=False)

    from icalendar import Calendar
    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(to_ics(meetings))

    print(f"Wrote {json_path} and {ics_path}")
    print(f"Meetings scraped: {len(meetings)}")

if __name__ == "__main__":
    main()
