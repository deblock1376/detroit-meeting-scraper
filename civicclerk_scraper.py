import json
from datetime import datetime, timedelta
import pytz
import os

def generate_newsletter(json_file, current_output, archive_dir):
    if not os.path.exists(json_file):
        print(f"Error: {json_file} not found.")
        return

    with open(json_file, 'r') as f:
        meetings = json.load(f)

    tz = pytz.timezone("America/Detroit")
    now = datetime.now(tz)
    current_year = now.year
    
    # Create filenames
    datestamp = now.strftime('%Y-%m-%d')
    archive_output = os.path.join(archive_dir, f"newsletter_{datestamp}.md")

    # --- Newsletter Logic (same as before) ---
    one_week_ago = now - timedelta(days=7)
    two_weeks_ahead = now + timedelta(days=14)
    past_week = [m for m in meetings if one_week_ago <= datetime.fromisoformat(m['start']) < now and datetime.fromisoformat(m['start']).year == current_year]
    upcoming = [m for m in meetings if now <= datetime.fromisoformat(m['start']) <= two_weeks_ahead and datetime.fromisoformat(m['start']).year == current_year]

    # Content generation
    content = f"# 🏛️ Macomb County Meeting Dispatch\n"
    content += f"**Edition:** {now.strftime('%B %d, %Y')} | *2026 Governance Update*\n\n---\n\n"
    
    content += "## 🗓️ The Week in Review\n"
    if not past_week:
        content += "No meetings were held in the past 7 days.\n"
    for m in past_week:
        m_dt = datetime.fromisoformat(m['start'])
        content += f"* **{m['body']}** ({m_dt.strftime('%b %d')}):\n"
        if m.get('minutes_url'): content += f"    * [📄 View Minutes]({m['minutes_url']})\n"
    
    content += "\n---\n\n## 📅 Upcoming Preview\n"
    if not upcoming:
        content += "No meetings scheduled for the next two weeks.\n"
    for m in upcoming:
        m_dt = datetime.fromisoformat(m['start'])
        content += f"### {m_dt.strftime('%A, %B %d')}\n"
        content += f"* **{m['body']}** ({m_dt.strftime('%I:%M %p')})\n"
        if m.get('location'): content += f"    * *Location:* {m['location']}\n"
        if m.get('agenda_url'): content += f"    * [📄 Meeting Agenda]({m['agenda_url']})\n"
        content += "\n"

    # --- Save Files ---
    os.makedirs(archive_dir, exist_ok=True)
    
    # Save current version (overwrites)
    with open(current_output, 'w', encoding='utf-8') as f:
        f.write(content)
        
    # Save archive version (new unique file)
    with open(archive_output, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"Newsletter generated: {current_output} and {archive_output}")

if __name__ == "__main__":
    generate_newsletter('data/macomb-meetings.json', 'briefs/newsletter.md', 'briefs/archive')
