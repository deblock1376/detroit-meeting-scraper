import json
from datetime import datetime, timedelta
import pytz
import os

def generate_newsletter(json_file, output_file):
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.")
        return

    with open(json_file, 'r') as f:
        meetings = json.load(f)

    # Use the scraper's timezone
    tz = pytz.timezone("America/Detroit")
    now = datetime.now(tz)
    current_year = now.year
    
    # Define window for "Review" and "Preview"
    one_week_ago = now - timedelta(days=7)
    two_weeks_ahead = now + timedelta(days=14)

    past_week = []
    upcoming = []

    for m in meetings:
        m_start = datetime.fromisoformat(m['start'])
        
        # Filter: Only include 2026 meetings
        if m_start.year != current_year:
            continue

        if one_week_ago <= m_start < now:
            past_week.append(m)
        elif now <= m_start <= two_weeks_ahead:
            upcoming.append(m)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# 🏛️ Macomb County Meeting Dispatch\n")
        f.write(f"**Edition:** {now.strftime('%B %d, %Y')} | *2026 Governance Update*\n\n---\n\n")

        f.write("## 🗓️ The Week in Review\n")
        if not past_week:
            f.write("No meetings were held in the past 7 days.\n")
        for m in past_week:
            m_dt = datetime.fromisoformat(m['start'])
            f.write(f"* **{m['body']}** ({m_dt.strftime('%b %d')}):\n")
            if m.get('minutes_url'):
                f.write(f"    * [📄 View Minutes]({m['minutes_url']})\n")
        
        f.write("\n---\n\n## 📅 Upcoming Preview\n")
        if not upcoming:
            f.write("No meetings scheduled for the next two weeks.\n")
        for m in upcoming:
            m_dt = datetime.fromisoformat(m['start'])
            f.write(f"### {m_dt.strftime('%A, %B %d')}\n")
            f.write(f"* **{m['body']}** ({m_dt.strftime('%I:%M %p')})\n")
            if m.get('location'):
                f.write(f"    * *Location:* {m['location']}\n")
            if m.get('agenda_url'):
                f.write(f"    * [📄 Meeting Agenda]({m['agenda_url']})\n")
            f.write("\n")

        f.write("---\n## 📁 Resources\n")
        f.write("* [Macomb CivicClerk Portal](https://macombcomi.portal.civicclerk.com/)\n")
        f.write("* [Board of Commissioners Site](https://bocmacomb.org/)\n")

if __name__ == "__main__":
    generate_newsletter('data/macomb-meetings.json', 'briefs/newsletter.md')
