#!/usr/bin/env bash
set -euo pipefail
python detroit_meetings_scraper.py --months-ahead 2 --months-behind 1 --year $(date +%Y) --outdir data
