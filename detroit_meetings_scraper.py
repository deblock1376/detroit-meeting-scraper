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
import pdfplumber
import io

# --- HTTP (resilient session) ---
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import certifi

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
    # Disable SSL verification in CI environments (GitHub Actions)
    # This is acceptable for public meeting data where SSL issues are environmental
    is_ci = os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
    print(f"DEBUG: CI={os.environ.get('CI')}, GITHUB_ACTIONS={os.environ.get('GITHUB_ACTIONS')}, is_ci={is_ci}")
    if is_ci:
        print("DEBUG: Disabling SSL verification in CI environment")
        s.verify = False
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    else:
        print("DEBUG: Using certifi for SSL verification")
        # Use certifi's certificate bundle for local development
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
    source: str = "escribe-detroit"
    # Parsed document content
    agenda_text: Optional[str] = None
    minutes_text: Optional[str] = None
    agenda_items: Optional[List[dict]] = None
    votes: Optional[List[dict]] = None

# --- Utils ---

def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def get(url: str) -> str:
    r = SESSION.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text

def month_url(year: int, month: int) -> str:
    return f"{BASE}?Year={year}&Month={month}"

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
    # Common patterns in meeting agendas
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
            if item_text and len(item_text) > 10:  # Filter out too-short items
                items.append({
                    "number": item_num,
                    "text": item_text[:500]  # Limit length
                })

    return items

def parse_votes(text: str) -> List[dict]:
    """Parse voting records from minutes text."""
    votes = []
    if not text:
        return votes

    # Look for vote patterns like "YEAS: ...", "NAYS: ...", "Motion carried", etc.
    # Pattern: "YEAS: Council Member ..., Council Member ..."
    yeas_pattern = r'YEAS?:?\s*(.+?)(?=NAYS?:?|ABSENT|$)'
    nays_pattern = r'NAYS?:?\s*(.+?)(?=ABSENT|YEAS?:?|$)'

    yeas_matches = re.finditer(yeas_pattern, text, re.IGNORECASE | re.DOTALL)
    nays_matches = re.finditer(nays_pattern, text, re.IGNORECASE | re.DOTALL)

    for match in yeas_matches:
        yeas_text = clean(match.group(1))
        if yeas_text:
            # Extract names (simple approach - split by commas)
            names = [clean(n) for n in re.split(r',|and', yeas_text) if clean(n)]
            if names:
                votes.append({
                    "vote_type": "yea",
                    "voters": names[:20]  # Limit number of names
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

# --- Scrape month pages ---

def parse_ajax_meeting(meeting_data: dict, parse_documents: bool = False) -> Optional[Meeting]:
    """Convert AJAX JSON meeting data directly to a Meeting object."""
    try:
        meeting_id = meeting_data.get("ID")
        if not meeting_id:
            return None

        # Parse meeting name and body
        meeting_name = clean(meeting_data.get("MeetingName", ""))
        meeting_type = clean(meeting_data.get("MeetingType", ""))

        # Use meeting type as body if available, otherwise use meeting name
        if meeting_type and meeting_type != meeting_name:
            body = meeting_type
            title = "Meeting"
        else:
            body = meeting_name
            title = "Meeting"

        # Parse dates (format: "2025/10/09 10:00:00")
        start_str = meeting_data.get("StartDate", "")
        end_str = meeting_data.get("EndDate", "")

        if not start_str:
            return None

        # Parse datetime from format "2025/10/09 10:00:00"
        start_dt = dt.datetime.strptime(start_str, "%Y/%m/%d %H:%M:%S")
        start_dt = TZ.localize(start_dt)

        if end_str:
            end_dt = dt.datetime.strptime(end_str, "%Y/%m/%d %H:%M:%S")
            end_dt = TZ.localize(end_dt)
        else:
            end_dt = start_dt + dt.timedelta(hours=2)

        # Location
        location = clean(meeting_data.get("Location", ""))
        description = clean(meeting_data.get("Description", ""))

        # Detail URL
        detail_url = meeting_data.get("Url", "")

        # Extract agenda and minutes URLs from MeetingDocumentLink
        agenda_url = None
        minutes_url = None
        docs = meeting_data.get("MeetingDocumentLink", [])
        for doc in docs:
            doc_type = doc.get("Type", "")
            doc_url = doc.get("Url", "")
            if not doc_url:
                continue
            # Make absolute URL if relative
            if not doc_url.startswith("http"):
                doc_url = urljoin(BASE, doc_url)

            if "Agenda" in doc_type and not agenda_url:
                agenda_url = doc_url
            elif "Minutes" in doc_type and not minutes_url:
                minutes_url = doc_url

        # UID
        uid_src = f"{body}|{start_dt.astimezone(dt.timezone.utc).isoformat()}|{meeting_id}|{detail_url}"
        uid = hashlib.sha1(uid_src.encode()).hexdigest() + "@detroit-escribe"

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
            timezone="America/Detroit",
            location=clean(location),
            address=clean(description) if description != location else "",
            virtual_link=None,  # Not available in AJAX response
            agenda_url=agenda_url,
            minutes_url=minutes_url,
            detail_url=detail_url,
            agenda_text=agenda_text,
            minutes_text=minutes_text,
            agenda_items=agenda_items,
            votes=votes,
        )
    except Exception as e:
        print(f"WARN: Failed to parse AJAX meeting data: {e}")
        return None


def parse_month(url: str, debug_dir: Optional[str] = None, parse_documents: bool = False) -> List[Meeting]:
    """Return a list of Meeting objects from AJAX endpoint for a month view."""
    # Extract year and month from URL (format: ?Year=2025&Month=11)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    year = int(qs.get("Year", [dt.date.today().year])[0])
    month = int(qs.get("Month", [dt.date.today().month])[0])

    # Calculate start and end dates for the month
    start_date = dt.date(year, month, 1)
    # Get last day of month
    if month == 12:
        end_date = dt.date(year, 12, 31)
    else:
        end_date = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

    # Call the AJAX endpoint that actually returns meeting data
    ajax_url = f"{BASE}MeetingsCalendarView.aspx/GetCalendarMeetings?Year={year}&Month={month}"
    payload = {
        "calendarStartDate": start_date.isoformat(),
        "calendarEndDate": end_date.isoformat()
    }

    try:
        r = SESSION.post(
            ajax_url,
            json=payload,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=30
        )
        r.raise_for_status()
        data = r.json()

        # Save JSON snapshot for diagnostics
        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            name = f"ajax_{year}_{month:02d}.json"
            with open(os.path.join(debug_dir, name), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        # Convert JSON data directly to Meeting objects
        meetings = []
        for meeting_data in data.get("d", []):
            mtg = parse_ajax_meeting(meeting_data, parse_documents=parse_documents)
            if mtg:
                meetings.append(mtg)

        return meetings
    except Exception as e:
        print(f"WARN: Failed to fetch AJAX endpoint for {year}-{month}: {e}")
        return []

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

def crawl(year: int, months_ahead: int, months_behind: int, pause: float = 0.6, parse_documents: bool = False) -> List[Meeting]:
    today = dt.date.today()
    target_months = set()
    for d in range(-months_behind, months_ahead + 1):
        month = today.month + d
        y_offset = (month - 1) // 12
        m_norm = ((month - 1) % 12) + 1
        target_months.add((year + y_offset, m_norm))

    items: List[Meeting] = []
    for y, m in sorted(target_months):
        url = month_url(y, m)
        try:
            meetings = parse_month(url, debug_dir="data/debug", parse_documents=parse_documents)
            print(f"Month {y}-{m:02d}: found {len(meetings)} meetings  @ {url}")
            items.extend(meetings)
            time.sleep(pause)
        except Exception as e:
            print(f"WARN month {y}-{m}: {e}")

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
    ap.add_argument("--parse-documents", action="store_true", help="Download and parse agenda/minutes PDFs (slower but extracts text and structured data)")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    os.makedirs(os.path.join(args.outdir, "debug"), exist_ok=True)

    meetings = crawl(args.year, args.months_ahead, args.months_behind, parse_documents=args.parse_documents)

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
