# Macomb County Meeting Briefs

Daily summaries of Macomb County government meetings, automatically generated from official meeting data.

## About

These briefs provide easy-to-read summaries of local government meetings, including:
- Key agenda items being discussed
- Voting results and outcomes
- Links to full meeting documents
- Meeting times and locations

Briefs are generated automatically from meeting data scraped from the [Macomb County CivicClerk portal](https://macombcomi.portal.civicclerk.com/).

## Latest Briefs

### January 2026
- [January 14, 2026](brief-2026-01-14.md)

### December 2025
- [December 18, 2025](brief-2025-12-18.md)
- [December 17, 2025](brief-2025-12-17.md)
- [December 16, 2025](brief-2025-12-16.md)
- [December 11, 2025](brief-2025-12-11.md)
- [December 10, 2025](brief-2025-12-10.md)
- [December 4, 2025](brief-2025-12-04.md)
- [December 3, 2025](brief-2025-12-03.md)
- [December 2, 2025](brief-2025-12-02.md)

### November 2025
- [November 26, 2025](brief-2025-11-26.md)
- [November 25, 2025](brief-2025-11-25.md)
- [November 20, 2025](brief-2025-11-20.md)
- [November 18, 2025](brief-2025-11-18.md)
- [November 13, 2025](brief-2025-11-13.md)
- [November 12, 2025](brief-2025-11-12.md)
- [November 6, 2025](brief-2025-11-06.md)
- [November 5, 2025](brief-2025-11-05.md)
- [November 4, 2025](brief-2025-11-04.md)

### October 2025
- [October 30, 2025](brief-2025-10-30.md)
- [October 29, 2025](brief-2025-10-29.md)
- [October 28, 2025](brief-2025-10-28.md)
- [October 27, 2025](brief-2025-10-27.md)
- [October 23, 2025](brief-2025-10-23.md)
- [October 22, 2025](brief-2025-10-22.md)
- [October 21, 2025](brief-2025-10-21.md)
- [October 20, 2025](brief-2025-10-20.md)

---

## Data Sources

- **Meeting Data**: [Macomb County CivicClerk](https://macombcomi.portal.civicclerk.com/)
- **Scraper Source Code**: [GitHub Repository](https://github.com/deblock1376/detroit-meeting-scraper)
- **Raw Data Files**: [JSON](../data/macomb-meetings.json) • [ICS Calendar](../data/macomb-meetings.ics)

## How It Works

1. **Daily Scraping**: GitHub Actions runs daily to fetch meeting data from the county's CivicClerk portal
2. **Document Parsing**: PDFs are downloaded and parsed to extract agenda items and voting records
3. **Brief Generation**: Markdown briefs are automatically generated highlighting key information
4. **Publication**: Briefs are committed to GitHub and published via GitHub Pages

## Subscribe

To get notifications of new meetings:
1. Import the [ICS calendar file](../data/macomb-meetings.ics) into your calendar app
2. Watch this repository on GitHub for updates
3. Check back daily for new briefs

---

*Last updated: 2025-11-19*
*Briefs are generated automatically from official meeting data*
