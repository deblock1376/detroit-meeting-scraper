# 🏛️ Macomb County Meeting Scraper

[![Macomb Scraper Status](https://github.com/deblock1376/detroit-meeting-scraper/actions/workflows/scrape-macomb.yml/badge.svg)](https://github.com/deblock1376/detroit-meeting-scraper/actions/workflows/scrape-macomb.yml)
![Last Scraped](https://img.shields.io/github/last-commit/deblock1376/detroit-meeting-scraper?label=Last%20Update)

> **Current Newsletter:** [View the Latest Dispatch Here](./briefs/newsletter.md)  
> **Archives:** [Browse Past Meetings](./briefs/archive/)

# eScribe Meeting Scraper

Scrape city council & committee meetings from eScribe portals and emit **JSON** + **ICS**.

Originally built for Detroit, now supports any eScribe instance.

## Quick start

**Detroit (default):**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data
# Outputs:
# data/detroit-meetings.json
# data/detroit-meetings.ics
```

**Other cities:**
```bash
# Example: New York City (if they used eScribe)
python detroit_meetings_scraper.py \
  --base-url https://pub-nyc.escribemeetings.com/ \
  --timezone America/New_York \
  --months-ahead 2 --months-behind 1 --year 2025 --outdir data
# Outputs:
# data/nyc-meetings.json
# data/nyc-meetings.ics
```

### JSON shape

```json
[
  {
    "uid": "sha1@detroit-escribe",
    "title": "Regular Meeting",
    "body": "Public Health & Safety Standing Committee",
    "start": "2025-11-05T10:00:00-05:00",
    "end":   "2025-11-05T12:00:00-05:00",
    "all_day": false,
    "timezone": "America/Detroit",
    "location": "Coleman A. Young Municipal Center",
    "address": "",
    "virtual_link": "https://zoom.us/...",
    "agenda_url": "https://pub-detroitmi.escribemeetings.com/...Agenda",
    "minutes_url": "https://pub-detroitmi.escribemeetings.com/...Minutes",
    "detail_url": "https://pub-detroitmi.escribemeetings.com/Meeting?Id=1234",
    "source": "escribe-detroit"
  }
]
```

## Options

- `--base-url` (default = `https://pub-detroitmi.escribemeetings.com/`) - Base URL of the eScribe instance
- `--timezone` (default = `America/Detroit`) - Timezone for meeting times (e.g., `America/New_York`, `America/Los_Angeles`)
- `--year` (default = current year) - Base year to crawl
- `--months-ahead` (default = 2) - How many months ahead to crawl
- `--months-behind` (default = 1) - How many months behind to crawl
- `--outdir` (default = `data/`) - Output directory
- `--parse-documents` - Download and parse agenda/minutes PDFs (slower but extracts text and structured data)

The scraper calls the eScribe AJAX endpoint to fetch meeting data as JSON, then optionally downloads and parses PDF documents for full text extraction and structured data parsing (agenda items, vote records).

## Using with other cities

To adapt this scraper for another city:

1. **Find the eScribe URL**: Look for your city's meeting portal. Many use eScribe and have URLs like:
   - `https://pub-{cityname}.escribemeetings.com/`
   - Check your city's official website for "City Council", "Meetings", or "Agendas"

2. **Find the timezone**: Use the IANA timezone database name (e.g., `America/New_York`, `America/Chicago`, `America/Los_Angeles`)
   - See: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

3. **Run the scraper**:
   ```bash
   python detroit_meetings_scraper.py \
     --base-url https://pub-{cityname}.escribemeetings.com/ \
     --timezone America/{Region} \
     --months-ahead 2 --months-behind 1 --year 2025 --outdir data
   ```

## Scheduling (GitHub Actions)

This workflow runs daily at 7:05am ET and commits updated files to the repo.

```yaml
name: scrape-detroit
on:
  schedule:
    - cron: "5 12 * * *"   # 12:05 UTC = 7:05 ET (standard)
  workflow_dispatch: {}

jobs:
  scrape:
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install --upgrade pip && pip install -r requirements.txt
      - run: python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data
      - name: Commit results
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/*.json data/*.ics || true
          git commit -m "Update Detroit meetings" || echo "No changes"
          git push
```

## Notes & caveats

- Respectful crawl: the script sleeps between requests. If you see rate limiting, increase the pause.
- Markup changes: eScribe templates vary; the parser is defensive and falls back to per-meeting ICS where available.
- End time heuristic: default is `+2h` if end is not specified.

## License

MIT
