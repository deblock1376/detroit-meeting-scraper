# Detroit eScribe Scraper

Scrape Detroit City Council & committee meetings from the public eScribe portal and emit **JSON** + **ICS**.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year 2025 --outdir data
# Outputs:
# data/detroit-meetings.json
# data/detroit-meetings.ics
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

- `--year` (default = current year)
- `--months-ahead` (default = 2)
- `--months-behind` (default = 1)
- `--outdir` (default = `data/`)

The scraper fetches month views, then visits each meeting detail page. It attempts to parse a per-meeting `.ics` (if present) for authoritative times, then enriches with agenda/minutes/virtual links from HTML.

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
