#!/usr/bin/env python3
"""
Generate daily briefs from meeting data

Creates markdown files with daily digests of local government meetings,
highlighting key agenda items and voting results.

Usage:
  python generate_briefs.py --input data/macomb-meetings.json --outdir briefs
  python generate_briefs.py --input data/detroit-meetings.json --outdir briefs
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any


def parse_date(iso_datetime: str) -> datetime:
    """Parse ISO datetime string to datetime object."""
    return datetime.fromisoformat(iso_datetime.replace('Z', '+00:00'))


def format_date(dt: datetime) -> str:
    """Format datetime for display."""
    return dt.strftime("%A, %B %d, %Y")


def format_time(dt: datetime) -> str:
    """Format datetime for time display."""
    return dt.strftime("%I:%M %p").lstrip('0')


def group_meetings_by_date(meetings: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group meetings by date."""
    grouped = defaultdict(list)
    for meeting in meetings:
        date_str = parse_date(meeting['start']).date().isoformat()
        grouped[date_str].append(meeting)
    return dict(grouped)


def generate_meeting_brief(meeting: Dict[str, Any]) -> str:
    """Generate brief content for a single meeting."""
    start_dt = parse_date(meeting['start'])
    end_dt = parse_date(meeting['end'])

    brief = []

    # Meeting header
    brief.append(f"### {meeting['body']}")
    brief.append(f"**Time:** {format_time(start_dt)} - {format_time(end_dt)}")

    if meeting.get('location'):
        brief.append(f"**Location:** {meeting['location']}")

    # Links
    links = []
    if meeting.get('agenda_url'):
        links.append(f"[Agenda]({meeting['agenda_url']})")
    if meeting.get('minutes_url'):
        links.append(f"[Minutes]({meeting['minutes_url']})")
    if meeting.get('detail_url'):
        links.append(f"[Details]({meeting['detail_url']})")
    if links:
        brief.append(f"**Documents:** {' • '.join(links)}")

    brief.append("")  # Blank line

    # Agenda items
    agenda_items = meeting.get('agenda_items', [])
    if agenda_items:
        brief.append("#### 📋 Key Agenda Items")
        # Show up to 10 items
        for item in agenda_items[:10]:
            # Clean up and truncate text
            text = item['text'].replace('\n', ' ').strip()
            if len(text) > 150:
                text = text[:147] + "..."
            brief.append(f"{item['number']}. {text}")

        if len(agenda_items) > 10:
            brief.append(f"\n*...and {len(agenda_items) - 10} more items*")
        brief.append("")

    # Voting results
    votes = meeting.get('votes', [])
    if votes:
        brief.append("#### 🗳️ Voting Results")

        # Group votes by type
        yea_votes = [v for v in votes if v['vote_type'] == 'yea']
        nay_votes = [v for v in votes if v['vote_type'] == 'nay']

        if yea_votes:
            brief.append(f"**Approved ({len(yea_votes)} vote(s))**")
            for vote in yea_votes[:3]:  # Show up to 3
                voters = ', '.join(vote['voters'][:5])
                if len(vote['voters']) > 5:
                    voters += f" +{len(vote['voters']) - 5} more"
                brief.append(f"- {voters}")

        if nay_votes:
            brief.append(f"\n**Opposed ({len(nay_votes)} vote(s))**")
            for vote in nay_votes[:3]:
                voters = ', '.join(vote['voters'][:5])
                if len(vote['voters']) > 5:
                    voters += f" +{len(vote['voters']) - 5} more"
                brief.append(f"- {voters}")

        brief.append("")

    # Summary stats
    stats = []
    if agenda_items:
        stats.append(f"{len(agenda_items)} agenda items")
    if votes:
        stats.append(f"{len(votes)} votes recorded")
    if meeting.get('agenda_text'):
        word_count = len(meeting['agenda_text'].split())
        stats.append(f"~{word_count:,} words in agenda")

    if stats:
        brief.append(f"*{' • '.join(stats)}*")
        brief.append("")

    return '\n'.join(brief)


def generate_daily_brief(date_str: str, meetings: List[Dict[str, Any]], source: str) -> str:
    """Generate a daily brief for all meetings on a given date."""
    date = datetime.fromisoformat(date_str)

    brief = []

    # Header
    brief.append(f"# Daily Meeting Brief: {format_date(date)}")
    brief.append(f"*Source: {source}*")
    brief.append("")
    brief.append("---")
    brief.append("")

    # Summary
    brief.append(f"## Summary")
    brief.append(f"**{len(meetings)} meeting(s) scheduled**")
    brief.append("")

    # List meetings
    for i, meeting in enumerate(meetings, 1):
        start_dt = parse_date(meeting['start'])
        brief.append(f"{i}. **{meeting['body']}** - {format_time(start_dt)}")
    brief.append("")
    brief.append("---")
    brief.append("")

    # Detailed meeting briefs
    brief.append("## Meeting Details")
    brief.append("")

    for meeting in meetings:
        brief.append(generate_meeting_brief(meeting))
        brief.append("---")
        brief.append("")

    # Footer
    brief.append("*This brief was automatically generated from official meeting data.*")
    brief.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}*")

    return '\n'.join(brief)


def main():
    parser = argparse.ArgumentParser(description="Generate daily meeting briefs")
    parser.add_argument("--input", required=True, help="Path to meetings JSON file")
    parser.add_argument("--outdir", default="briefs", help="Output directory for briefs")
    parser.add_argument("--date", help="Generate brief for specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    # Load meetings data
    with open(args.input, 'r', encoding='utf-8') as f:
        meetings = json.load(f)

    # Determine source name from input file
    source_name = Path(args.input).stem.replace('-meetings', '').title()

    # Group by date
    grouped = group_meetings_by_date(meetings)

    # Filter by date if specified
    if args.date:
        grouped = {args.date: grouped.get(args.date, [])}

    # Create output directory
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Generate briefs
    generated = 0
    for date_str, date_meetings in sorted(grouped.items()):
        if not date_meetings:
            continue

        # Sort meetings by start time
        date_meetings.sort(key=lambda m: m['start'])

        # Generate brief
        brief_content = generate_daily_brief(date_str, date_meetings, source_name)

        # Write to file
        filename = f"brief-{date_str}.md"
        filepath = outdir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(brief_content)

        print(f"Generated: {filepath}")
        generated += 1

    print(f"\n✅ Generated {generated} daily brief(s) in {outdir}/")
    print(f"   Total meetings: {len(meetings)}")
    print(f"   Date range: {min(grouped.keys())} to {max(grouped.keys())}")


if __name__ == "__main__":
    main()
