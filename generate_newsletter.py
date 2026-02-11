import json
from datetime import datetime, timedelta
import pytz

def generate_newsletter(json_file, output_file):
    with open(json_file, 'r') as f:
        meetings = json.load(f)

    # Use the scraper's timezone
    tz = pytz.timezone("America/Detroit")
    now = datetime.now(tz)
    
    # Define date ranges
    one_week_ago = now - timedelta(days=7)
    two_weeks_ahead = now + timedelta(days=14)

    past_week = []
    upcoming = []

    for m in meetings:
        meeting_date = datetime.fromisoformat(m['start'])
        if one_week_ago <= meeting_date < now:
            past_week.append(m)
        elif now <= meeting_date <= two_weeks_ahead:
            upcoming.append(m)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# 🏛️ Macomb County Meeting Dispatch\n")
        f.write(f"**Edition:** {now.strftime('%b %d, %Y')} | *Your weekly guide to local governance.*\n\n---\n\n")

        # Past Week Section
        f.write("## 🗓️ The Week in Review\n")
        if not past_week:
            f.write("No meetings were recorded in the past week.\n")
        for m in past_week:
            f.write(f"* **{m['body']} ({datetime.fromisoformat(m['start']).strftime('%b %d')}):**\n")
            if m.get('minutes_url'):
                f.write(f"    * [📄 View Minutes (PDF)]({m['minutes_url']})\n")
        
        f.write("\n---\n\n## 📅 Upcoming Preview\n")
        
        # Upcoming Section
        if not upcoming:
            f.write("No upcoming meetings scheduled for the next two weeks.\n")
        for m in upcoming:
            m_dt = datetime.fromisoformat(m['start'])
            f.write(f"### {m_dt.strftime('%A, %B %d')}\n")
            f.write(f"* **{m['body']} ({m_dt.strftime('%I:%M %p')})**\n")
            if m.get('location'):
                f.write(f"    * *Location:* {m['location']}\n")
            if m.get('agenda_url'):
                f.write(f"    * [📄 Meeting Agenda (PDF)]({m['agenda_url']})\n")
            f.write("\n")

        f.write("---\n## 📁 Resources\n")
        f.write("* [Official CivicClerk Events Portal](https://macombcomi.portal.civicclerk.com/)\n")

if __name__ == "__main__":
    generate_newsletter('data/macomb-meetings.json', 'briefs/newsletter.md')
