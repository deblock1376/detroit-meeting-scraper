#!/usr/bin/env python3
"""
CivicClerk scraper
- Scrapes CivicClerk meeting management portals for government meetings.
- Emits JSON and ICS files.
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

# Global configuration
API_BASE = DEFAULT_API_BASE
PORTAL_BASE = DEFAULT_PORTAL_BASE
TZ = pytz.timezone(DEFAULT_TIMEZONE)
TIMEZONE_NAME = DEFAULT_TIMEZONE
SOURCE_ID = "civicclerk-macombcomi"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
}

def _session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=4, connect=4, read=4, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.verify = certifi.where()
    return s

SESSION = _session()

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
    published_files: Optional[List[dict]] = None
    agenda_text: Optional[str] = None
    minutes_text: Optional[str] = None
    agenda_items: Optional[List[dict]] = None
    votes: Optional[List[dict]] = None

def clean(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def get_json(url: str) -> dict:
    r = SESSION.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()

def download_pdf(url: str) -> Optional[bytes]:
    try:
        r = SESSION.get(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "*/*"}, timeout=30)
        r.raise_for_status()
        if 'application/pdf' in r.headers.get('Content-Type', '') or r.content[:4] == b'%PDF':
            return r.content
        return None
    except Exception as e:
        print(f"WARN: Failed to download PDF: {e}")
        return None

def extract_text_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n\n".join(page.extract_text() for page in pdf.pages if page.extract_text())
    except Exception as e:
        print(f"WARN: Failed to extract text: {e}")
        return None

def parse_event(event_data: dict, parse_documents: bool = False) -> Optional[Meeting]:
    try:
        event_id = event_data.get("id")
        if not event_id: return None

        event_name = clean(event_data.get("eventName", ""))
        category_name = clean(event_data.get("categoryName", ""))
        body = category_name or event_name
        
        start_str = event_data.get("eventDate", "")
        start_dt = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(TZ)
        end_dt = start_dt + dt.timedelta(hours=2)

        location_data = event_data.get("eventLocation", {})
        location = ", ".join(filter(None, [location_data.get("address1"), location_data.get("city"), location_data.get("state")]))
        detail_url = f"{PORTAL_BASE}event/{event_id}"

        published_files = []
        agenda_url, minutes_url = None, None
        for f in event_data.get("publishedFiles", []):
            f_url = urljoin(PORTAL_BASE, f.get("url", ""))
            d_url = f"{API_BASE}Meetings/GetMeetingFileStream(fileId={f.get('fileId')},plainText=false)" if f.get("fileId") else f_url
            file_info = {"type": f.get("type"), "name": f.get("name"), "url": f_url, "download_url": d_url}
            published_files.append(file_info)
            if f.get("type") == "Agenda" and not agenda_url: agenda_url = d_url
            if f.get("type") == "Minutes" and not minutes_url: minutes_url = d_url

        uid = hashlib.sha1(f"{body}|{start_dt.isoformat()}|{event_id}".encode()).hexdigest() + f"@{SOURCE_ID}"

        return Meeting(
            uid=uid, title="Meeting", body=body, start=start_dt.isoformat(), end=end_dt.isoformat(),
            all_day=False, timezone=TIMEZONE_NAME, location=location, address=clean(event_data.get("eventDescription")),
            virtual_link=None, agenda_url=agenda_url, minutes_url=minutes_url, detail_url=detail_url,
            source=SOURCE_ID, published_files=published_files or None
        )
    except Exception as e:
        print(f"WARN: Failed to parse event: {e}")
        return None

def fetch_events(start_date: dt.date, end_date: dt.date, parse_documents: bool = False) -> List[Meeting]:
    meetings = []
    # Increased top to 1000 for better coverage
    url = f"{API_BASE}Events?$orderby=eventDate desc&$top=1000"
    print(f"Fetching events from {start_date} to {end_date}...")

    while url:
        try:
            data = get_json(url)
            events = data.get("value", [])
            if not events: break

            for event_data in events:
                event_date_str = event_data.get("eventDate", "")
                if not event_date_str: continue
                event_date = dt.datetime.fromisoformat(event_date_str.replace('Z', '+00:00')).date()

                if start_date <= event_date <= end_date:
                    mtg = parse_event(event_data, parse_documents)
                    if mtg: meetings.append(mtg)
                
                # Buffer of 7 days to account for potential API sorting inconsistencies
                elif event_date < (start_date - dt.timedelta(days=7)):
                    url = None
                    break

            if url:
                url = data.get("@odata.nextLink")
                if url: time.sleep(0.5)
        except Exception as e:
            print(f"WARN: Fetch failed: {e}")
            break
    return meetings

def crawl(year: int, months_ahead: int, months_behind: int, parse_documents: bool = False) -> List[Meeting]:
    # Logic fix: Use the year argument to set the anchor point
    today = dt.date.today()
    anchor_date = dt.date(year, today.month, today.day) if year != today.year else today
    
    start_date = anchor_date - dt.timedelta(days=months_behind * 30)
    end_date = anchor_date + dt.timedelta(days=months_ahead * 30)

    meetings = fetch_events(start_date, end_date, parse_documents=parse_documents)
    
    seen = set()
    unique = []
    for it in meetings:
        key = (it.body.lower(), it.start)
        if key not in seen:
            seen.add(key)
            unique.append(it)
    unique.sort(key=lambda x: x.start)
    print(f"Total meetings parsed: {len(unique)}")
    return unique

# --- CLI and Main ---

def main():
    global API_BASE, PORTAL_BASE, TZ, TIMEZONE_NAME, SOURCE_ID
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-base", default=DEFAULT_API_BASE)
    ap.add_argument("--portal-base", default=DEFAULT_PORTAL_BASE)
    ap.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    ap.add_argument("--year", type=int, default=dt.date.today().year)
    ap.add_argument("--months-ahead", type=int, default=2)
    ap.add_argument("--months-behind", type=int, default=1)
    ap.add_argument("--outdir", default="data")
    ap.add_argument("--parse-documents", action="store_true")
    args = ap.parse_args()

    API_BASE = args.api_base if args.api_base.endswith('/') else args.api_base + '/'
    PORTAL_BASE = args.portal_base if args.portal_base.endswith('/') else args.portal_base + '/'
    TZ = pytz.timezone(args.timezone)
    TIMEZONE_NAME = args.timezone
    
    os.makedirs(args.outdir, exist_ok=True)
    meetings = crawl(args.year, args.months_ahead, args.months_behind, args.parse_documents)

    json_path = os.path.join(args.outdir, "macomb-meetings.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in meetings], f, indent=2, ensure_ascii=False)
    print(f"Wrote {json_path}. Meetings scraped: {len(meetings)}")

if __name__ == "__main__":
    main()
