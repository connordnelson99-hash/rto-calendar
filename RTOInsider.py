from playwright.sync_api import sync_playwright
import json

results = []

RTO_KEYWORDS = {
    "PJM": ("PJM", "https://www.pjm.com/Home/Calendar.aspx"),
    "CAISO": ("CAISO", "https://www.caiso.com/meetings-events/calendar"),
    "MISO": ("MISO", "https://www.misoenergy.org/engage/tools/calendar"),
    "NYISO": ("NYISO", "https://www.nyiso.com/calendar"),
    "ERCOT": ("ERCOT", "https://www.ercot.com/calendar"),
    "SPP": ("SPP Markets +", "https://www.spp.org/events/"),
    "NEPOOL": ("NEPOOL", "https://nepool.com/calendar/"),
    "ISO-NE": ("ISO-NE", "https://www.iso-ne.com/calendar"),
    "NERC": ("NERC", "https://www.nerc.com/Pages/Calendar.aspx"),
    "FERC": ("FERC", "https://www.ferc.gov/news-events/events")
}

COMMITTEE_RTO_MAP = {
    "NYISO": [
        "Management Committee",
        "Business Issues Committee",
        "Operating Committee",
        "Installed Capacity",
        "Market Issues",
        "Price-Responsive Load",
        "Electric System Planning",
        "Transmission Planning Advisory",
        "Billing, Accounting & Credit Policy",
        "Load Forecasting",
        "Communication & Data Advisory",
        "System Operations Advisory"
    ],
    "ISO-NE": [
        "Participants Committee",
        "Markets Committee",
        "Reliability Committee",
        "Transmission Committee",
        "Planning Advisory Committee",
        "Distributed Generation Forecast Working Group",
        "Load Forecasting",
        "Electric System Planning"
    ],
    "PJM": [
        "Members Committee",
        "Markets and Reliability Committee",
        "Market Implementation Committee",
        "Planning Committee",
        "Risk Management Committee",
        "Transmission Expansion Advisory Committee",
        "Resource Adequacy"
        "Operating Committee"
    ],
    "MISO": [
        "Market Subcommittee",
        "Planning Advisory Committee",
        "Planning Subcommittee",
        "Reliability Subcommittee",
        "Resource Adequacy Subcommittee",
        "Interconnection Process Working Group",
        "Regional Expansion Criteria and Benefits Working Group"
    ],
    "SPP Markets +": [
        "Markets+ Participants Executive Committee",
        "Markets+ State Committee",
        "Markets and Operations Policy Committee",
        "REAL Team"
    ],
    "ERCOT": [
        "Technical Advisory Committee",
        "Reliability and Markets Committee",
        "Finance and Audit Committee",
        "HR and Governance Committee",
        "Technology and Security Committee"
    ]
}

def identify_rto(title):
    for keyword, (label, url) in RTO_KEYWORDS.items():
        if keyword.lower() in title.lower():
            return label, url
    return "Other", None

def match_committee_keywords(title, rto):
    title_lower = title.lower()
    valid_keywords = COMMITTEE_RTO_MAP.get(rto, [])
    matches = [kw for kw in valid_keywords if kw.lower() in title_lower]
    return "; ".join(sorted(set(matches))) if matches else None

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=100)  # Show browser for debugging
    page = browser.new_page()
    print("Navigating to calendar page...")
    page.goto("https://www.rtoinsider.com/events/")

    for month_index in range(3):  # Iterate through current + next 2 months
        print(f"\nLoading month {month_index + 1}...")
        page.wait_for_selector(".fc-daygrid-day")
        day_cells = page.query_selector_all(".fc-daygrid-day")
        print(f"Found {len(day_cells)} day cells.")

        for cell in day_cells:
            date_elem = cell.query_selector(".fc-daygrid-day-number")
            if not date_elem:
                continue

            date_str = date_elem.get_attribute("aria-label")
            if not date_str:
                continue

            events = cell.query_selector_all(".fc-event")
            for event in events:
                title_elem = event.query_selector(".fc-event-title")
                time_elem = event.query_selector(".fc-event-time")

                title = title_elem.inner_text().strip() if title_elem else None
                time = time_elem.inner_text().strip() if time_elem else None

                if title:
                    rto, rto_website = identify_rto(title)
                    committee_match = match_committee_keywords(title, rto)
                    print(f"  - {date_str}: {title} at {time} [{rto}]")
                    results.append({
                        "title": title,
                        "time": time,
                        "date": date_str,
                        "rto": rto,
                        "rto_website": rto_website,
                        "committee_match": committee_match
                    })

        # Click "Next month" button if available
        next_button = page.query_selector("button[title='Next month']")
        if next_button:
            print("Clicking to next month...")
            next_button.click()
            page.wait_for_timeout(2000)
        else:
            print("No 'Next month' button found. Ending early.")
            break

    browser.close()

# Remove duplicate events by title + date + time
seen = set()
unique_results = []
for r in results:
    key = (r["title"], r["date"], r["time"])
    if key not in seen:
        seen.add(key)
        unique_results.append(r)

with open("rto_events.json", "w") as f:
    json.dump(unique_results, f, indent=2)

print(f"\n✅ Extracted {len(unique_results)} unique events into rto_events.json")

