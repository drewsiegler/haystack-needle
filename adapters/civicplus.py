"""CivicPlus AgendaCenter adapter: Saratoga, Campbell's commissions and Los Altos Hills' committees.

AgendaCenter is part of each city's CivicPlus website. All three hosts serve the
same robots.txt: it disallows /Search and /RSS.aspx but not /AgendaCenter/, and
its Crawl-delay of 20 applies only to Siteimprove's crawlers, so the usual one
request a second applies.

- /AgendaCenter/Search/?term=&CIDs=all&startDate=MM/DD/YYYY&endDate=MM/DD/YYYY
  lists every agenda dated in that window, on one page, grouped by category
  (the body). Each row has the meeting date, the agenda's title and ID, and when
  it was posted, or "Amended" and when, once the PDF was replaced.
- /AgendaCenter/ViewFile/Agenda/_MMDDYYYY-NNNN is the agenda PDF. There is no
  item list: Saratoga's agenda is a few pages that name each staff report and
  attachment, and Campbell's is the whole packet, hundreds of pages.

So each meeting has one item, the agenda, with its PDF as the attachment, and
the matcher reads the PDF's pages. A match deep in a packet scores low, as in
any long attachment. The posted or amended time tells the run when to read the
PDF again.

Los Gatos is left out: its AgendaCenter has nothing after October 2024, and the
Town Council is on Municode Meetings.
"""

from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {
    client: Source("civicplus", client, name)
    for client, name in [
        ("saratoga", "Saratoga"),
        ("campbell", "Campbell"),
        ("losaltoshills", "Los Altos Hills"),
    ]
}
HOSTS = {
    "saratoga": "www.saratoga.ca.us",
    "campbell": "www.campbellca.gov",
    "losaltoshills": "www.losaltoshills.ca.gov",
}
# Categories read elsewhere: Los Altos Hills posts its council agendas here too,
# but the CivicClerk adapter reads them item by item.
SKIP_CATEGORIES = {"losaltoshills": {"15"}}

_CATEGORY = re.compile(r'aria-controls="category-panel-(\d+)"[^>]*>(.*?)</h2>', re.S)
_ROW = re.compile(r'<tr id="row[^"]*" class="catAgendaRow">(.*?)</tr>', re.S)
_HEADING = re.compile(r'<h3 class="noMargin" id="h4(\d{8})-(\d+)">(.*?)</h3>', re.S)
_STAMP = re.compile(r"(Posted|Amended)\s+([A-Z][a-z]{2} \d{1,2}, \d{4} \d{1,2}:\d{2} [AP]M)")
_TAGS = re.compile(r"<[^>]*>")


def _base(client: str) -> str:
    return f"https://{HOSTS[client]}/AgendaCenter"


def calendar_url(client: str) -> str:
    """The city's AgendaCenter, for people checking on a problem."""
    return _base(client)


def search_url(client: str, start: date, end: date) -> str:
    return (f"{_base(client)}/Search/?term=&CIDs=all&startDate={start:%m/%d/%Y}"
            f"&endDate={end:%m/%d/%Y}&dateRange=&dateSelector=")


def agenda_url(client: str, stamp: str, agenda_id: str) -> str:
    """The agenda PDF. stamp is the meeting date as MMDDYYYY."""
    return f"{_base(client)}/ViewFile/Agenda/_{stamp}-{agenda_id}"


def category_url(client: str, category_id: str, name: str, stamp: str, agenda_id: str) -> str:
    """The body's agenda list, opened at this agenda's row."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")
    return f"{_base(client)}/{slug}-{category_id}/?#_{stamp}-{agenda_id}"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", fragment)).split())


def _stamp_time(heading: str) -> datetime | None:
    """When the agenda was posted or last amended, in UTC. The list gives Pacific time."""
    found = _STAMP.search(_text(heading))
    if not found:
        return None
    local = datetime.strptime(found.group(2), "%b %d, %Y %I:%M %p").replace(tzinfo=PACIFIC)
    return local.astimezone(timezone.utc)


def parse_rows(page: str) -> list[dict]:
    """Every agenda row on an AgendaCenter list or search page, with its category."""
    if "chkCategoryID" not in page and "catAgendaRow" not in page:
        raise ValueError("the page didn't look like an AgendaCenter agenda list")
    categories = list(_CATEGORY.finditer(page))
    rows = []
    for i, category in enumerate(categories):
        end = categories[i + 1].start() if i + 1 < len(categories) else len(page)
        for row in _ROW.finditer(page, category.end(), end):
            heading = _HEADING.search(row.group(1))
            if not heading:
                continue
            stamp, agenda_id = heading.group(1), heading.group(2)
            link = re.search(rf'href="/AgendaCenter/ViewFile/Agenda/_{stamp}-{agenda_id}"[^>]*>(.*?)</a>',
                             row.group(1), re.S)
            rows.append({
                "category_id": category.group(1),
                "category": _text(category.group(2)),
                "id": agenda_id,
                "stamp": stamp,
                "date": datetime.strptime(stamp, "%m%d%Y").date(),
                "title": re.sub(r"\s*\(PDF\)$", "", _text(link.group(1))) if link else "",
                "modified": _stamp_time(heading.group(3)),
            })
    return rows


def fetch_rows(fetcher: Fetcher, client: str, start: date, end: date) -> list[dict]:
    """Every agenda dated start to end, inclusive."""
    page = fetcher.get(search_url(client, start, end)).decode("utf-8", errors="replace")
    return parse_rows(page)


def build_meeting(client: str, row: dict) -> Meeting:
    """A meeting with one item, its agenda, whose PDF is the attachment that gets read."""
    source = SOURCES[client]
    url = agenda_url(client, row["stamp"], row["id"])
    title = row["title"] or f"{row['category']} agenda"
    return Meeting(
        body=Body(source, row["category_id"], row["category"]),
        id=row["id"],
        date=row["date"],
        agenda_url=url,
        url=category_url(client, row["category_id"], row["category"], row["stamp"], row["id"]),
        modified=row["modified"],
        items=[Item(
            id=row["id"],
            title=title,
            attachments=[Attachment(id=row["id"], name="Agenda", url=url, modified=row["modified"])],
            modified=row["modified"],
        )],
    )


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: the agenda PDF is the item's attachment."""
    return ""


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead for each city.

    with_text and cache are unused: one search page lists a city's agendas, and
    the PDFs are read as attachments.

    Returns the meetings and, for any city that failed, what went wrong, so one
    city being down doesn't stop the others.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        skip = SKIP_CATEGORIES.get(client, set())
        try:
            for row in fetch_rows(fetcher, client, start, end):
                # The search already limits dates; checking again keeps a stray
                # row out even if the site ignores the dates.
                if row["category_id"] not in skip and start <= row["date"] <= end:
                    meetings.append(build_meeting(client, row))
        except (httpx.HTTPError, ValueError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"CivicPlus {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
