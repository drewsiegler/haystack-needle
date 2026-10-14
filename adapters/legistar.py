"""Legistar adapter: San José, Sunnyvale, Mountain View, Santa Clara, Cupertino and Valley Water.

Legistar's Web API is open, needs no key, and its host has no robots.txt:
https://webapi.legistar.com/v1/{client}/  (docs: https://webapi.legistar.com/Help)

- /events takes OData $filter, $orderby, $top and $skip, and returns at most
  1000 rows a request, so events are read page by page.
- /events/{id}/eventitems?AgendaNote=1&Attachments=1 gives each agenda item with
  its note, its MatterId and its attachments in one request. Minutes notes are
  left off: they're written after the meeting.
- A matter's text (often the staff recommendation) takes two requests:
  /matters/{id}/versions, then /matters/{id}/texts/{textId}.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

API = "https://webapi.legistar.com/v1/{client}"
PAGE_SIZE = 1000  # the most rows Legistar returns per request
DAYS_BACK = 2
DAYS_AHEAD = 30
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {
    client: Source("legistar", client, name)
    for client, name in [
        ("sanjose", "San José"),
        ("sunnyvaleca", "Sunnyvale"),
        ("mountainview", "Mountain View"),
        ("santaclara", "Santa Clara"),
        ("cupertino", "Cupertino"),
        ("scvwd", "Valley Water"),
    ]
}


def _base(client: str) -> str:
    return API.format(client=client)


def _odata_date(d: date) -> str:
    return f"datetime'{d.isoformat()}'"


def _date(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _clean(text: str | None) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("\r\n", "\n")).strip()


def fetch_events(fetcher: Fetcher, client: str, start: date, end: date,
                 page_size: int = PAGE_SIZE) -> list[dict]:
    """Every event dated start to end, inclusive, oldest first."""
    rows: list[dict] = []
    while True:
        page = fetcher.get_json(f"{_base(client)}/events", {
            "$filter": f"EventDate ge {_odata_date(start)} "
                       f"and EventDate lt {_odata_date(end + timedelta(days=1))}",
            "$orderby": "EventDate,EventId",
            "$top": page_size,
            "$skip": len(rows),
        })
        rows += page
        if len(page) < page_size:
            return rows


def fetch_event_items(fetcher: Fetcher, client: str, event_id: int) -> list[dict]:
    return fetcher.get_json(f"{_base(client)}/events/{event_id}/eventitems",
                            {"AgendaNote": 1, "MinutesNote": 0, "Attachments": 1})


def fetch_matter(fetcher: Fetcher, client: str, matter_id: int) -> dict:
    """A matter's record, including MatterIntroDate and MatterAgendaDate (first agenda date)."""
    return fetcher.get_json(f"{_base(client)}/matters/{matter_id}")


def fetch_matter_attachments(fetcher: Fetcher, client: str, matter_id: int) -> list[dict]:
    return fetcher.get_json(f"{_base(client)}/matters/{matter_id}/attachments")


def fetch_matter_text(fetcher: Fetcher, client: str, matter_id: int) -> str:
    """The latest version of a matter's text, or "" if it has none."""
    versions = fetcher.get_json(f"{_base(client)}/matters/{matter_id}/versions")
    if not versions:
        return ""
    latest = max(versions, key=lambda v: int(v["Value"]) if str(v["Value"]).isdigit() else 0)
    text = fetcher.get_json(f"{_base(client)}/matters/{matter_id}/texts/{latest['Key']}")
    return _clean(text.get("MatterTextPlain"))


def is_placeholder(event: dict) -> bool:
    """Calendar placeholders (Sunnyvale keeps one dated 2027-12-31) have a hidden agenda."""
    return event.get("EventAgendaStatusName") == "Hidden"


def parse_attachment(row: dict) -> Attachment:
    return Attachment(
        id=str(row["MatterAttachmentId"]),
        name=_clean(row.get("MatterAttachmentName")),
        url=row.get("MatterAttachmentHyperlink") or "",
        modified=_datetime(row.get("MatterAttachmentLastModifiedUtc")),
    )


def parse_meeting(client: str, event: dict, item_rows: list[dict],
                  matter_texts: dict[int, str] | None = None) -> Meeting:
    """Turn an event and its event items into a Meeting. Section headings without a title are dropped."""
    matter_texts = matter_texts or {}
    meeting = Meeting(
        body=Body(SOURCES[client], str(event["EventBodyId"]), _clean(event["EventBodyName"])),
        id=str(event["EventId"]),
        date=_date(event["EventDate"]),
        time=event.get("EventTime") or "",
        agenda_url=event.get("EventAgendaFile"),
        url=event.get("EventInSiteURL"),
        modified=_datetime(event.get("EventLastModifiedUtc")),
    )
    # Rows with no agenda sequence were added for the minutes; they go last.
    for row in sorted(item_rows, key=lambda r: (r.get("EventItemAgendaSequence") is None,
                                                r.get("EventItemAgendaSequence") or 0)):
        title = _clean(row.get("EventItemTitle"))
        if not title:
            continue
        matter_id = row.get("EventItemMatterId")
        text = "\n\n".join(t for t in (_clean(row.get("EventItemAgendaNote")),
                                       matter_texts.get(matter_id, "")) if t)
        meeting.items.append(Item(
            id=str(row["EventItemId"]),
            title=title,
            text=text,
            number=_clean(row.get("EventItemAgendaNumber")),
            matter_id=str(matter_id) if matter_id else None,
            matter_file=row.get("EventItemMatterFile"),
            attachments=[parse_attachment(a) for a in row.get("EventItemMatterAttachments") or []
                         if a.get("MatterAttachmentShowOnInternetPage", True)],
            modified=_datetime(row.get("EventItemLastModifiedUtc")),
        ))
    return meeting


def read_meeting(fetcher: Fetcher, client: str, event: dict, with_text: bool = True) -> Meeting:
    """Fetch an event's items (and, with_text, each matter's text) and build the Meeting."""
    rows = fetch_event_items(fetcher, client, event["EventId"])
    texts = {}
    if with_text:
        for matter_id in dict.fromkeys(r["EventItemMatterId"] for r in rows if r.get("EventItemMatterId")):
            texts[matter_id] = fetch_matter_text(fetcher, client, matter_id)
    return parse_meeting(client, event, rows, texts)


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from DAYS_BACK days ago to DAYS_AHEAD days ahead for each client.

    Returns the meetings and, for any client that failed, what went wrong, so one
    client being down doesn't stop the others.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=DAYS_BACK), today + timedelta(days=DAYS_AHEAD)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        try:
            for event in fetch_events(fetcher, client, start, end):
                # The filter already limits dates; checking again keeps a far-future
                # placeholder out even if the API ignores the filter.
                if is_placeholder(event) or not start <= _date(event["EventDate"]) <= end:
                    continue
                meetings.append(read_meeting(fetcher, client, event, with_text))
        except httpx.HTTPError as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"Legistar {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
