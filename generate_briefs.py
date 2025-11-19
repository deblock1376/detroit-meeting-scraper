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
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple


def parse_date(iso_datetime: str) -> datetime:
    """Parse ISO datetime string to datetime object."""
    return datetime.fromisoformat(iso_datetime.replace('Z', '+00:00'))


def format_date(dt: datetime) -> str:
    """Format datetime for display."""
    return dt.strftime("%A, %B %d, %Y")


def format_time(dt: datetime) -> str:
    """Format datetime for time display."""
    return dt.strftime("%I:%M %p").lstrip('0')


# High-interest topic detection
TOPIC_KEYWORDS = {
    'development': [
        'rezoning', 'rezone', 'zoning', 'development', 'construction',
        'building permit', 'land use', 'subdivision', 'site plan',
        'infrastructure', 'road', 'highway', 'sidewalk', 'paving',
        'drainage', 'sewer', 'water main', 'utility', 'demolition',
        'variance', 'special use', 'conditional use', 'master plan'
    ],
    'environment': [
        'environment', 'pollution', 'contamination', 'cleanup',
        'park', 'recreation', 'conservation', 'preservation',
        'water quality', 'air quality', 'stormwater', 'wetland',
        'wildlife', 'natural', 'green space', 'trail', 'landfill',
        'recycling', 'waste', 'compost', 'sustainability', 'climate'
    ]
}


def extract_dollar_amounts(text: str) -> List[Tuple[str, float]]:
    """Extract dollar amounts from text and return as (formatted_string, numeric_value) tuples."""
    # Match patterns like $1,000,000 or $1M or $1.5M or $500K
    patterns = [
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*million', 1_000_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*M\b', 1_000_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*thousand', 1_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)\s*K\b', 1_000),
        (r'\$\s*([\d,]+(?:\.\d+)?)', 1),
    ]

    amounts = []
    for pattern, multiplier in patterns:
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            num_str = match.group(1).replace(',', '')
            try:
                value = float(num_str) * multiplier
                amounts.append((match.group(0), value))
            except ValueError:
                continue

    return amounts


def detect_topic_keywords(text: str) -> List[str]:
    """Detect topic keywords in text and return matching categories."""
    text_lower = text.lower()
    categories = []

    for category, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in text_lower for keyword in keywords):
            categories.append(category)

    return categories


def analyze_agenda_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze an agenda item and add high-interest tags."""
    text = item.get('text', '')
    tags = []
    highlights = []

    # Detect topic categories
    categories = detect_topic_keywords(text)
    if 'development' in categories:
        tags.append('🏗️ Development')
    if 'environment' in categories:
        tags.append('🌲 Environment')

    # Detect dollar amounts
    amounts = extract_dollar_amounts(text)
    if amounts:
        # Find the largest amount
        max_amount = max(amounts, key=lambda x: x[1])
        if max_amount[1] >= 1_000_000:
            tags.append(f'💰 {max_amount[0]}')
            highlights.append(f"Large expenditure: {max_amount[0]}")
        elif max_amount[1] >= 100_000:
            tags.append(f'💵 {max_amount[0]}')

    return {
        **item,
        'tags': tags,
        'highlights': highlights,
        'categories': categories
    }


def analyze_vote(vote: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a vote and add interest indicators."""
    yea_count = len(vote.get('voters', []))
    vote_type = vote.get('vote_type', '')

    tags = []

    # This is simplified - in real data we'd need both yea and nay counts
    # For now, flag if it appears in the data (meaning it was noteworthy enough to record)
    if vote_type == 'yea':
        tags.append('✓ Approved')
    elif vote_type == 'nay':
        tags.append('✗ Opposed')

    return {
        **vote,
        'tags': tags
    }


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

    # Analyze full meeting text for topics
    full_text = (meeting.get('agenda_text', '') or '') + ' ' + (meeting.get('minutes_text', '') or '')
    meeting_topics = detect_topic_keywords(full_text)
    meeting_amounts = extract_dollar_amounts(full_text)

    # Show meeting-level topics if found
    meeting_tags = []
    if 'development' in meeting_topics:
        meeting_tags.append('🏗️ Development')
    if 'environment' in meeting_topics:
        meeting_tags.append('🌲 Environment')
    if meeting_amounts:
        max_amount = max(meeting_amounts, key=lambda x: x[1])
        if max_amount[1] >= 1_000_000:
            meeting_tags.append(f'💰 Large spending: {max_amount[0]}')

    if meeting_tags:
        brief.append("#### 🔔 High-Interest Topics")
        brief.append(f"{' • '.join(meeting_tags)}")
        brief.append("")

    # Analyze agenda items for high-interest topics
    agenda_items = meeting.get('agenda_items') or []
    analyzed_items = [analyze_agenda_item(item) for item in agenda_items]

    # High-interest items section (item-specific)
    high_interest_items = [item for item in analyzed_items if item.get('tags')]
    if high_interest_items:
        brief.append("#### 🔔 High-Interest Items")
        for item in high_interest_items[:5]:  # Show top 5
            text = item['text'].replace('\n', ' ').strip()
            if len(text) > 120:
                text = text[:117] + "..."
            tags_str = ' '.join(item['tags'])
            brief.append(f"- **{item['number']}.** {text}")
            brief.append(f"  {tags_str}")
        if len(high_interest_items) > 5:
            brief.append(f"\n*...and {len(high_interest_items) - 5} more flagged items*")
        brief.append("")

    # All agenda items
    if analyzed_items:
        brief.append("#### 📋 All Agenda Items")
        # Show up to 10 items
        for item in analyzed_items[:10]:
            # Clean up and truncate text
            text = item['text'].replace('\n', ' ').strip()
            if len(text) > 150:
                text = text[:147] + "..."
            # Add tags if present
            tags_str = f" `{'` `'.join(item['tags'])}`" if item.get('tags') else ""
            brief.append(f"{item['number']}. {text}{tags_str}")

        if len(analyzed_items) > 10:
            brief.append(f"\n*...and {len(analyzed_items) - 10} more items*")
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

    # Count high-interest items across all meetings
    total_high_interest = 0
    topic_counts = {'development': 0, 'environment': 0}
    for meeting in meetings:
        agenda_items = meeting.get('agenda_items') or []
        for item in agenda_items:
            analyzed = analyze_agenda_item(item)
            if analyzed.get('tags'):
                total_high_interest += 1
                for category in analyzed.get('categories', []):
                    topic_counts[category] = topic_counts.get(category, 0) + 1

    # Summary
    brief.append(f"## Summary")
    brief.append(f"**{len(meetings)} meeting(s) scheduled**")
    if total_high_interest > 0:
        topic_labels = []
        if topic_counts.get('development', 0) > 0:
            topic_labels.append(f"🏗️ {topic_counts['development']} Development")
        if topic_counts.get('environment', 0) > 0:
            topic_labels.append(f"🌲 {topic_counts['environment']} Environment")
        brief.append(f"**{total_high_interest} high-interest item(s):** {' • '.join(topic_labels) if topic_labels else 'See details below'}")
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
