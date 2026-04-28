# RTO/ISO Document Scraper

Automated scraper for meeting documents across RTO/ISO organizations,
designed to feed into NHA's web calendar application.

## Project Structure

```
rto-docs/
├── README.md
├── run_scrapers.py                      # CLI runner
├── db/
│   ├── __init__.py
│   └── database.py                      # SQLite schema + query functions
├── scrapers/
│   ├── __init__.py
│   ├── base_scraper.py                  # Abstract base class
│   ├── pjm_scraper.py                   # PJM scraper (Chrome-validated)
│   └── caiso_scraper.py                 # CAISO scraper (Chrome-validated)
└── docs/                                # Downloaded documents land here
    └── {rto}/{committee}/{YYYY-MM}/{filename}
```

## Requirements

```bash
pip install playwright beautifulsoup4 lxml requests
playwright install chromium
```

## Usage

```bash
# Run all scrapers (PJM + CAISO)
python run_scrapers.py

# Run only PJM
python run_scrapers.py --rto PJM

# Scrape metadata only (no downloads)
python run_scrapers.py --rto PJM --no-download

# Adjust the time window
python run_scrapers.py --lookback 30 --lookahead 60

# Re-export JSON from existing database
python run_scrapers.py --export-only

# View database statistics
python run_scrapers.py --stats
```

## Output

The scraper produces `rto_events_with_docs.json`, extending the existing
`rto_events.json` format with a `documents` array per event:

```json
{
  "title": "Markets and Reliability Committee Meeting",
  "date": "2026-03-26",
  "time": "9:30 AM EPT",
  "rto": "PJM",
  "committee": "Markets and Reliability Committee",
  "source_url": "https://www.pjm.com/calendar",
  "materials_url": "https://www.pjm.com/forms/registration/...",
  "documents": [
    {
      "type": "agenda",
      "title": "MRC March 2026 Agenda",
      "url": "https://www.pjm.com/-/media/...",
      "local_path": "docs/pjm/mrc/2026-03/20260326-agenda.pdf"
    }
  ]
}
```

## Scraper Architecture (Chrome-Validated)

### PJM
- **Calendar**: FullCalendar.js grid with `.fc-event` DIVs
- **Workflow**: Click event → sidebar shows committee name + "View posted
  materials" GUID link → materials page has jQuery UI accordion sections
  → document table rows with `a[href*="/-/media/"]` links
- **Fallback**: Construct predictable URLs like
  `pjm.com/-/media/DotCom/committees-groups/committees/{slug}/{year}/{date}/{date}-agenda.pdf`

### CAISO
- **Meetings page**: `<button class="card">` elements with
  `data-event-id` and `data-event-docs-sort-json` attributes
- **Key insight**: Documents do NOT live on individual event pages.
  They live on **Stakeholder Center initiative pages** at
  `stakeholdercenter.caiso.com/StakeholderInitiatives/{slug}`
- **Workflow**: Meetings page → topic links → initiative page →
  `table.table-phase` rows → document links in column 2
- **Document URLs**: `stakeholdercenter.caiso.com/InitiativeDocuments/{name}.pdf`

## Scheduled Operation

```bash
# Run daily at 6 AM ET
0 6 * * * cd /path/to/rto-docs && python run_scrapers.py >> scrape.log 2>&1
```

## Adding New RTOs

1. Create a new scraper in `scrapers/` inheriting from `BaseRTOScraper`
2. Implement `rto_name`, `scrape_meetings()`, and `scrape_meeting_documents()`
3. Register it in `SCRAPER_REGISTRY` in `run_scrapers.py`
