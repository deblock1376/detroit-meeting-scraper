#!/usr/bin/env python3
"""
Detroit eScribe scraper
- Scrapes the public Detroit City Council eScribe instance for meetings.
- Emits JSON and ICS files.

Usage:
  python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data
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
from bs4 import BeautifulSoup

# --- HTTP (resilient session) ---
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = "https://pub-detroitmi.escribemeetings.com/"
TZ = pytz.timezone("America/Detroit")

HEADERS = {
    # Use a real-browser UA (some hosts throttle default UA strings)
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}

def _session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=4, connect=4, read=4, backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=["GET", "HEAD", "OPTIONS"]
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s

SESSION = _session()

# --- Data model ---

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

# --- Utils ---

def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def get(url: str) -> str:
    r = SESSION.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text

def month_url(year: int, month: int) -> str:
    return f"{BASE}?Year={year}&Month={month}"

# --- Scrape month pages ---

def parse_month(url: str, debug_dir: Optional[str] = None) -> List[str]:
    """Return a list of absolute meeting detail URLs from a month view."""
    html = get(url)

    # Save HTML snapshot for diagnostics
    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        name = re.sub(r"[^0-9A-Za-z]+", "_", url)
        with open(os.path.join(debug_dir, f"{name[:120]}.html"), "w", encoding="utf-8") as f:
            f.write(html)

    soup = BeautifulSoup(html, "html.parser")
    detail_links: List[str] = []

    # CSS selector sweep (handles Meeting?Id=..., Meeting.aspx?..., etc.)
    for a in soup.select("a[href*='Meeting?Id'], a[href*='Meeting?'], a[href*='Meeting.aspx?']"):
        href = a.get("href") or ""
        if "Meeting" in href:
            detail_links.append(urljoin(BASE, href))

    # Generic sweep in case template differs
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ("Meeting?Id=" in href) or ("Meeting.aspx" in href and "Id=" in href):
            detail_links.append(urljoin(BASE, href))

    # Unique + stable order
    return sorted(set(detail_links))

# --- Parse detail page (and per-meeting ICS if present) ---

from icalendar import Calendar, Event  # after docstring so flake8 doesn't complain

def try_parse_ics_from_detail(soup: BeautifulSoup) -> Optional[dict]:
    """Fetch and parse the per-meeting .ics if an 'Add to Calendar' link exists."""
    for a in soup.select("a[href$='.ics'], a[href*='AddToCalendar'], a[href*='.ics']"):
        href = a.get("href", "")
        if not href:
            continue
        try:
            ics_url = urljoin(BASE, href)
            r = SESSION.get(ics_url, headers=HEADERS, timeout=30, allow_redirects=True)
            if r.status_code == 200 and "BEGIN:VCALENDAR" in r.text:
                cal = Calendar.from_ical(r.content)
                for comp in cal.walk():
                    if comp.name == "VEVENT":
                        start = comp.get("dtstart").dt
                        end = comp.get("dtend").dt if comp.get("dtend") else None
                        summary = str(comp.get("summary") or "")
                        loc = str(comp.get("location") or "")
                        return {"start": start, "end": end, "summary": summary, "location": loc}
        except Exception:
            continue
    return None

def extract_meeting_id(url: str) -> Optional[str]:
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

    # If the title looks like the body (common in eScribe), adjust
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
    def first_link(label_regex: str) -> Optional[str]:
        for link in soup.select("a[href]"):
            if re.search(label_regex, link.get_text(strip=True), re.I):
                return urljoin(BASE, link["href"])
        return None

    agenda_url = first_link(r"agenda")
    minutes_url = first_link(r"minutes")

    virtual_link = None
    for link in soup.select("a[href]"):
        href = (link.get("href") or "").lower()
        if any(k in href for k in ["zoom.us", "teams.microsoft", "youtube.com", "facebook.com/live", "livestream"]):
            virtual_link = link.get("href")
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

# --- Fallback seeding from the City Clerk list ---

def seed_from_city_agendas_page() -> List[str]:
    """City Clerk 'Agendas & Documents' page often links to eScribe Meeting.aspx pages."""
    url = "https://detroitmi.gov/government/city-clerk/city-council-agendas-documents"
    try:
        html = get(url)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "escribemeetings.com" in href and "Meeting" in href:
            links.append(href if href.startswith("http") else urljoin(url, href))
    return sorted(set(links))

# --- ICS output ---

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

# --- Crawl orchestration ---

def crawl(year: int, months_ahead: int, months_behind: int, pause: float = 0.6) -> List[Meeting]:
    today = dt.date.today()
    target_months = set()
    for d in range(-months_behind, months_ahead + 1):
        month = today.month + d
        y_offset = (month - 1) // 12
        m_norm = ((month - 1) % 12) + 1
        target_months.add((year + y_offset, m_norm))

    details = set()
    for y, m in sorted(target_months):
        url = month_url(y, m)
        try:
            found = parse_month(url, debug_dir="data/debug")
            print(f"Month {y}-{m:02d}: found {len(found)} detail links  @ {url}")
            for du in found:
                details.add(du)
            time.sleep(pause)
        except Exception as e:
            print(f"WARN month {y}-{m}: {e}")

    # Fallback if month views were empty (e.g., JS/Ajax)
    if not details:
        seeds = seed_from_city_agendas_page()
        print(f"Fallback seeding from City Clerk page: {len(seeds)} links")
        for du in seeds:
            details.add(du)

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
    print(f"Total meetings parsed: {len(unique)}")
    return unique

# --- CLI ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=dt.date.today().year, help="Base year to crawl (defaults to current year)")
    ap.add_argument("--months-ahead", type=int, default=2, help="How many months ahead to crawl")
    ap.add_argument("--months-behind", type=int, default=1, help="How many months behind to crawl")
    ap.add_argument("--outdir", type=str, default="data", help="Output directory")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(os.path.join(args.outdir, "debug"), exist_ok=True)

    meetings = crawl(args.year, args.months_ahead, args.months_behind)

    json_path = os.path.join(args.outdir, "detroit-meetings.json")
    ics_path = os.path.join(args.outdir, "detroit-meetings.ics")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in meetings], f, indent=2, ensure_ascii=False)

    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(to_ics(meetings))

    print(f"Wrote {json_path} and {ics_path}")
    print(f"Meetings scraped: {len(meetings)}")

if __name__ == "__main__":
    main()
