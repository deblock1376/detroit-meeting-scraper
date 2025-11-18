#!/usr/bin/env python3
"""
CivicClerk scraper
- Scrapes CivicClerk meeting management portals for government meetings.
- Emits JSON and ICS files.

Usage:
  python civicclerk_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data

  For other jurisdictions:
  python civicclerk_scraper.py \
    --api-base https://example.api.civicclerk.com/v1/ \
    --portal-base https://example.portal.civicclerk.com/ \
    --timezone America/New_York \
    --months-ahead 2 --months-behind 1 --year 2025 --outdir data
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
from urllib.parse import urljoin, urlparse

import pytz
from bs4 import BeautifulSoup
import pdfplumber
import io

# --- HTTP (resilient session) ---
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import certifi

# Default configuration (Macomb County, MI)
DEFAULT_API_BASE = "https://macombcomi.api.civicclerk.com/v1/"
DEFAULT_PORTAL_BASE = "https://macombcomi.portal.civicclerk.com/"
DEFAULT_TIMEZONE = "America/Detroit"

# Global configuration (will be set by main() from CLI arguments)
API_BASE = DEFAULT_API_BASE
PORTAL_BASE = DEFAULT_PORTAL_BASE
TZ = pytz.timezone(DEFAULT_TIMEZONE)
TIMEZONE_NAME = DEFAULT_TIMEZONE
SOURCE_ID = "civicclerk-macombcomi"  # Derived from API base URL

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
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
    # Use certifi for SSL verification
    s.verify = certifi.where()
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
    source: str = "civicclerk-macombcomi"
    # Additional files (agenda packets, notices, etc.)
    published_files: Optional[List[dict]] = None
    # Parsed document content
    agenda_text: Optional[str] = None
    minutes_text: Optional[str] = None
    agenda_items: Optional[List[dict]] = None
    votes: Optional[List[dict]] = None

# --- Utils ---

def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def get_json(url: str) -> dict:
    """Fetch JSON from CivicClerk API."""
    r = SESSION.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.json()

# --- Document downloading and parsing ---

def download_pdf(url: str) -> Optional[bytes]:
    """Download a PDF document and return its bytes."""
    try:
        r = SESSION.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if 'application/pdf' in r.headers.get('Content-Type', ''):
            return r.content
        return None
    except Exception as e:
        print(f"WARN: Failed to download PDF from {url}: {e}")
        return None

def extract_text_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """Extract text from PDF bytes."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text_parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            return "\n\n".join(text_parts)
    except Exception as e:
        print(f"WARN: Failed to extract text from PDF: {e}")
        return None

def parse_agenda_items(text: str) -> List[dict]:
    """Parse agenda items from text."""
    items = []
    if not text:
        return items

    # Look for numbered items like "1.", "2.", "I.", "II.", "A.", "B."
    patterns = [
        r'^\s*(\d+)\.\s+(.+?)(?=^\s*\d+\.|$)',  # 1. Item text
        r'^\s*([A-Z])\.\s+(.+?)(?=^\s*[A-Z]\.|$)',  # A. Item text
        r'^\s*([IVX]+)\.\s+(.+?)(?=^\s*[IVX]+\.|$)',  # I. Item text (Roman numerals)
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text, re.MULTILINE | re.DOTALL)
        for match in matches:
            item_num = match.group(1)
            item_text = clean(match.group(2))
            if item_text and len(item_text) > 10:
                items.append({
                    "number": item_num,
                    "text": item_text[:500]
                })

    return items

def parse_votes(text: str) -> List[dict]:
    """Parse voting records from minutes text."""
    votes = []
    if not text:
        return votes

    # Look for vote patterns like "YEAS: ...", "NAYS: ..."
    yeas_pattern = r'YEAS?:?\s*(.+?)(?=NAYS?:?|ABSENT|$)'
    nays_pattern = r'NAYS?:?\s*(.+?)(?=ABSENT|YEAS?:?|$)'

    yeas_matches = re.finditer(yeas_pattern, text, re.IGNORECASE | re.DOTALL)
    nays_matches = re.finditer(nays_pattern, text, re.IGNORECASE | re.DOTALL)

    for match in yeas_matches:
        yeas_text = clean(match.group(1))
        if yeas_text:
            names = [clean(n) for n in re.split(r',|and', yeas_text) if clean(n)]
            if names:
                votes.append({
                    "vote_type": "yea",
                    "voters": names[:20]
                })

    for match in nays_matches:
        nays_text = clean(match.group(1))
        if nays_text:
            names = [clean(n) for n in re.split(r',|and', nays_text) if clean(n)]
            if names:
                votes.append({
                    "vote_type": "nay",
                    "voters": names[:20]
                })

    return votes

# --- Parse CivicClerk API events ---

def parse_event(event_data: dict, parse_documents: bool = False) -> Optional[Meeting]:
    """Convert CivicClerk API event data to a Meeting object."""
    try:
        event_id = event_data.get("id")
        if not event_id:
            return None

        # Parse event name and description
        event_name = clean(event_data.get("eventName", ""))
        event_desc = clean(event_data.get("eventDescription", ""))
        category_name = clean(event_data.get("categoryName", ""))

        # Use category as body, event name as title
        body = category_name or event_name
        title = "Meeting"

        # Parse dates (ISO 8601 format: "2025-12-11T15:00:00Z")
        start_str = event_data.get("eventDate", "")
        if not start_str:
            return None

        start_dt = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        if start_dt.tzinfo:
            start_dt = start_dt.astimezone(TZ)
        else:
            start_dt = TZ.localize(start_dt)

        # End time: use meeting end time if available, otherwise +2 hours
        end_str = event_data.get("meetingEndTime", "")
        if end_str and end_str != "1900-01-01T00:00:00Z":
            end_dt = dt.datetime.fromisoformat(end_str.replace('Z', '+00:00'))
            if end_dt.tzinfo:
                end_dt = end_dt.astimezone(TZ)
            else:
                end_dt = TZ.localize(end_dt)
        else:
            end_dt = start_dt + dt.timedelta(hours=2)

        # Location
        location_data = event_data.get("eventLocation", {})
        location_parts = []
        if location_data.get("address1"):
            location_parts.append(location_data["address1"])
        if location_data.get("city"):
            location_parts.append(location_data["city"])
        if location_data.get("state"):
            location_parts.append(location_data["state"])
        if location_data.get("zipCode"):
            location_parts.append(location_data["zipCode"])
        location = ", ".join(location_parts) if location_parts else ""

        # Detail URL (construct from event ID)
        detail_url = f"{PORTAL_BASE}event/{event_id}"

        # Process published files
        published_files_data = event_data.get("publishedFiles", [])
        published_files = []
        agenda_url = None
        minutes_url = None

        for file_data in published_files_data:
            file_type = file_data.get("type", "")
            file_name = file_data.get("name", "")
            file_url_rel = file_data.get("url", "")

            if file_url_rel:
                # Construct absolute URL
                file_url = urljoin(PORTAL_BASE, file_url_rel)

                published_files.append({
                    "type": file_type,
                    "name": file_name,
                    "url": file_url
                })

                # Set agenda_url and minutes_url for primary documents
                if file_type == "Agenda" and not agenda_url:
                    agenda_url = file_url
                elif file_type == "Minutes" and not minutes_url:
                    minutes_url = file_url

        # UID
        uid_src = f"{body}|{start_dt.astimezone(dt.timezone.utc).isoformat()}|{event_id}|{detail_url}"
        uid = hashlib.sha1(uid_src.encode()).hexdigest() + f"@{SOURCE_ID}"

        # Download and parse documents (only if requested)
        agenda_text = None
        agenda_items = None
        minutes_text = None
        votes = None

        if parse_documents:
            if agenda_url:
                print(f"  Downloading agenda for {body}...")
                pdf_bytes = download_pdf(agenda_url)
                if pdf_bytes:
                    agenda_text = extract_text_from_pdf(pdf_bytes)
                    if agenda_text:
                        agenda_items = parse_agenda_items(agenda_text)
                        print(f"    Found {len(agenda_items)} agenda items")

            if minutes_url:
                print(f"  Downloading minutes for {body}...")
                pdf_bytes = download_pdf(minutes_url)
                if pdf_bytes:
                    minutes_text = extract_text_from_pdf(pdf_bytes)
                    if minutes_text:
                        votes = parse_votes(minutes_text)
                        print(f"    Found {len(votes)} vote records")

        return Meeting(
            uid=uid,
            title=clean(title),
            body=clean(body),
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            all_day=False,
            timezone=TIMEZONE_NAME,
            location=clean(location),
            address=clean(event_desc),
            virtual_link=None,  # Could parse from externalMediaUrl if needed
            agenda_url=agenda_url,
            minutes_url=minutes_url,
            detail_url=detail_url,
            source=SOURCE_ID,
            published_files=published_files if published_files else None,
            agenda_text=agenda_text,
            minutes_text=minutes_text,
            agenda_items=agenda_items,
            votes=votes,
        )
    except Exception as e:
        print(f"WARN: Failed to parse event data: {e}")
        return None

# --- Fetch events from CivicClerk API ---

def fetch_events(start_date: dt.date, end_date: dt.date, parse_documents: bool = False) -> List[Meeting]:
    """Fetch events from CivicClerk API for a date range."""
    meetings = []

    # Build OData query
    # Filter by date range and order by date descending
    url = f"{API_BASE}Events?$orderby=eventDate desc&$top=100"

    print(f"Fetching events from {start_date} to {end_date}...")

    while url:
        try:
            data = get_json(url)
            events = data.get("value", [])

            for event_data in events:
                event_date_str = event_data.get("eventDate", "")
                if not event_date_str:
                    continue

                event_date = dt.datetime.fromisoformat(event_date_str.replace('Z', '+00:00')).date()

                # Filter by date range
                if event_date < start_date:
                    # Since we're ordering by date desc, we can stop here
                    url = None
                    break

                if event_date <= end_date:
                    mtg = parse_event(event_data, parse_documents=parse_documents)
                    if mtg:
                        meetings.append(mtg)

            # Check for next page
            if url:
                url = data.get("@odata.nextLink")

            if url:
                time.sleep(0.3)  # Rate limiting

        except Exception as e:
            print(f"WARN: Failed to fetch events: {e}")
            break

    return meetings

# --- ICS output ---

from icalendar import Calendar, Event

def to_ics(items: List[Meeting]) -> str:
    cal = Calendar()
    cal.add("prodid", "-//CivicClerk Scraper//EN")
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

def crawl(year: int, months_ahead: int, months_behind: int, parse_documents: bool = False) -> List[Meeting]:
    """Crawl CivicClerk for meetings within a date range."""
    today = dt.date.today()
    start_date = today - dt.timedelta(days=months_behind * 30)
    end_date = today + dt.timedelta(days=months_ahead * 30)

    meetings = fetch_events(start_date, end_date, parse_documents=parse_documents)

    # De-dupe by (title, body, start)
    seen = set()
    unique: List[Meeting] = []
    for it in meetings:
        key = (it.title.lower(), it.body.lower(), it.start)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda x: x.start)
    print(f"Total meetings parsed: {len(unique)}")
    return unique

# --- CLI ---

def derive_source_id(api_base: str) -> str:
    """Derive a source identifier from the API base URL."""
    try:
        parsed = urlparse(api_base)
        hostname = parsed.netloc or parsed.path
        # Extract subdomain (e.g., "macombcomi" from "macombcomi.api.civicclerk.com")
        parts = hostname.split('.')
        if len(parts) >= 2:
            subdomain = parts[0]
            return f"civicclerk-{subdomain}"
        return "civicclerk-meetings"
    except Exception:
        return "civicclerk-meetings"

def main():
    global API_BASE, PORTAL_BASE, TZ, TIMEZONE_NAME, SOURCE_ID

    ap = argparse.ArgumentParser(description="Scrape CivicClerk meeting calendar and generate JSON/ICS files")
    ap.add_argument("--api-base", type=str, default=DEFAULT_API_BASE,
                    help=f"API base URL of the CivicClerk instance (default: {DEFAULT_API_BASE})")
    ap.add_argument("--portal-base", type=str, default=DEFAULT_PORTAL_BASE,
                    help=f"Portal base URL for document downloads (default: {DEFAULT_PORTAL_BASE})")
    ap.add_argument("--timezone", type=str, default=DEFAULT_TIMEZONE,
                    help=f"Timezone for meeting times (default: {DEFAULT_TIMEZONE})")
    ap.add_argument("--year", type=int, default=dt.date.today().year,
                    help="Base year to crawl (defaults to current year)")
    ap.add_argument("--months-ahead", type=int, default=2,
                    help="How many months ahead to crawl")
    ap.add_argument("--months-behind", type=int, default=1,
                    help="How many months behind to crawl")
    ap.add_argument("--outdir", type=str, default="data",
                    help="Output directory")
    ap.add_argument("--parse-documents", action="store_true",
                    help="Download and parse agenda/minutes PDFs (slower but extracts text and structured data)")
    args = ap.parse_args()

    # Set global configuration from arguments
    API_BASE = args.api_base
    if not API_BASE.endswith('/'):
        API_BASE += '/'

    PORTAL_BASE = args.portal_base
    if not PORTAL_BASE.endswith('/'):
        PORTAL_BASE += '/'

    try:
        TZ = pytz.timezone(args.timezone)
        TIMEZONE_NAME = args.timezone
    except pytz.exceptions.UnknownTimeZoneError:
        print(f"ERROR: Unknown timezone '{args.timezone}'")
        print(f"Use a valid timezone name like 'America/New_York' or 'America/Detroit'")
        print(f"See https://en.wikipedia.org/wiki/List_of_tz_database_time_zones")
        return 1

    SOURCE_ID = derive_source_id(API_BASE)

    print(f"Configuration:")
    print(f"  API Base: {API_BASE}")
    print(f"  Portal Base: {PORTAL_BASE}")
    print(f"  Timezone: {TIMEZONE_NAME}")
    print(f"  Source ID: {SOURCE_ID}")
    print(f"  Date range: {args.year}, {args.months_behind} months behind to {args.months_ahead} months ahead")
    print()

    os.makedirs(args.outdir, exist_ok=True)

    meetings = crawl(args.year, args.months_ahead, args.months_behind, parse_documents=args.parse_documents)

    # Generate output filenames based on source ID
    if SOURCE_ID == "civicclerk-macombcomi":
        filename_base = "macomb-meetings"
    else:
        filename_base = SOURCE_ID.replace("civicclerk-", "") + "-meetings"

    json_path = os.path.join(args.outdir, f"{filename_base}.json")
    ics_path = os.path.join(args.outdir, f"{filename_base}.ics")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in meetings], f, indent=2, ensure_ascii=False)

    with open(ics_path, "w", encoding="utf-8") as f:
        f.write(to_ics(meetings))

    print(f"Wrote {json_path} and {ics_path}")
    print(f"Meetings scraped: {len(meetings)}")

if __name__ == "__main__":
    main()
