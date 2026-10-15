"""CivicClerk adapter: Milpitas, Morgan Hill, Gilroy, Los Altos Hills and Los Altos.

Each city has an open OData API with no key, at https://{tenant}.api.civicclerk.com/v1/
(the hosts have no robots.txt, so the usual one request a second applies):

- /Events lists meetings; $filter and $orderby work on startDateTime. The server
  returns a page at a time (15 rows for Los Altos Hills) and ends it with an
  @odata.nextLink, which is followed. Times come back as local time labelled "Z".
- /Meetings/{agendaId} is a whole agenda in one request: items nested under their
  section headings, each with its description and attachments. An agenda that
  isn't published yet comes back with no items.
- /Meetings/GetMeetingFileStream(fileId=,plainText=false) is a posted agenda
  PDF, and /Meetings/GetAttachmentFile(fileId=) is an item's attachment. Those
  links stay good. The signed blob links in the attachment records last a week,
  so they're not used.

An item's description is the only text it has beyond its title, and it arrives
with the agenda, so item_text has nothing left to read. CivicClerk has no
matter history, so there's no matter_history: "this moved forward" comes from
past alerts.
"""

from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

API = "https://{client}.api.civicclerk.com/v1"
PORTAL = "https://{client}.portal.civicclerk.com"
DAYS_BACK = 2
DAYS_AHEAD = 30
MAX_PAGES = 20  # pages of events per client; a month is one or two
MAX_TITLE = 300  # a longer title is cut here and the whole of it goes in the item's text
AGENDA_FILES = ("Agenda", "Agenda Packet")  # published file types that mean the agenda was (re)posted
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {
    client: Source("civicclerk", client, name)
    for client, name in [
        ("milpitasca", "Milpitas"),
        ("morganhillca", "Morgan Hill"),
        ("gilroyca", "Gilroy"),
        ("losaltoshillsca", "Los Altos Hills"),
        ("losaltosca", "Los Altos"),
    ]
}

_BREAKS = re.compile(r"</?(?:br|p|div|li|ul|ol|tr|table|h[1-6])\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]*>")


def calendar_url(client: str) -> str:
    """The city's public meeting portal, for people checking on a problem."""
    return PORTAL.format(client=client) + "/"


def _base(client: str) -> str:
    return API.format(client=client)


def file_url(client: str, file_id: int) -> str:
    """A posted agenda, packet or notice, as a PDF."""
    return f"{_base(client)}/Meetings/GetMeetingFileStream(fileId={file_id},plainText=false)"


def attachment_url(client: str, attachment_id: int) -> str:
    return f"{_base(client)}/Meetings/GetAttachmentFile(fileId={attachment_id})"


def _odata_date(d: date) -> str:
    return f"{d.isoformat()}T00:00:00Z"


def _local(value: str) -> datetime:
    """CivicClerk's event times are local time with a "Z" on the end; drop the Z."""
    return datetime.fromisoformat(value.rstrip("Z"))


def _datetime(value: str | None) -> datetime | None:
    """A real UTC time, or None for the API's 1900 and 0001 "no date" values."""
    if not value or value.startswith(("1900", "0001")):
        return None
    return datetime.fromisoformat(value)


def _paragraphs(fragment: str | None) -> str:
    """Plain text from the HTML CivicClerk keeps item titles and descriptions in."""
    text = _TAGS.sub("", _BREAKS.sub("\n", fragment or ""))
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _line(fragment: str | None) -> str:
    return " ".join(_paragraphs(fragment).split())


def _next_page(client: str, page: dict) -> str | None:
    """The page's nextLink, if it points at this client's own API."""
    link = page.get("@odata.nextLink")
    if not link:
        return None
    parts = urlsplit(link)
    if parts.scheme != "https" or parts.hostname != f"{client}.api.civicclerk.com":
        raise ValueError(f"the next page of events is at an address outside {client}.api.civicclerk.com")
    return link


def fetch_events(fetcher: Fetcher, client: str, start: date, end: date) -> list[dict]:
    """Every event dated start to end, inclusive, oldest first."""
    rows: list[dict] = []
    page = fetcher.get_json(f"{_base(client)}/Events", {
        "$filter": f"startDateTime ge {_odata_date(start)} and startDateTime lt {_odata_date(end + timedelta(days=1))}",
        "$orderby": "startDateTime,id",
    })
    for _ in range(MAX_PAGES):
        rows += page["value"]
        link = _next_page(client, page)
        if not link:
            return rows
        page = fetcher.get_json(link)
    return rows


def fetch_agenda(fetcher: Fetcher, client: str, agenda_id: int) -> dict:
    return fetcher.get_json(f"{_base(client)}/Meetings/{agenda_id}")


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: the item's description came with its agenda."""
    return ""


def is_listed(event: dict) -> bool:
    return not event.get("isDeleted") and event.get("isPublished") == "Published"


def _published(event: dict, *types: str) -> list[dict]:
    return [f for f in event.get("publishedFiles") or [] if f.get("type") in types and f.get("fileId")]


def posted_at(event: dict) -> datetime | None:
    """When the agenda was last posted (or reposted), from its published files."""
    times = [_datetime(f.get("publishOn")) for f in _published(event, *AGENDA_FILES)]
    return max((t for t in times if t), default=None)


def parse_attachment(client: str, row: dict) -> Attachment | None:
    """A readable attachment, or None for a link, a draft, a deleted file or a Word file."""
    if (row.get("isLink") or row.get("isDeleted") or not row.get("isPublished", True)
            or "pdf" not in (row.get("contentType") or "").lower()):
        return None
    return Attachment(
        id=str(row["id"]),
        name=_line(row.get("fileName")) or _line(row.get("mediaFileName")),
        url=attachment_url(client, row["id"]),
        modified=_datetime(row.get("createdOn")),
    )


def parse_items(client: str, rows: list[dict], modified: datetime | None,
                parents: tuple[str, ...] = ()) -> list[Item]:
    """The agenda's items in order. Section headings are left out; their numbers become
    part of the number of the items under them (6 and A make 6.A)."""
    items: list[Item] = []
    for row in sorted(rows, key=lambda r: r.get("sortOrder") or 0):
        outline = _line(row.get("agendaObjectItemOutlineNumber")).strip(".")
        trail = parents + ((outline,) if outline else ())
        if not row.get("isSection"):
            title = _line(row.get("agendaObjectItemName"))
            text = _paragraphs(row.get("agendaObjectItemDescription"))
            if len(title) > MAX_TITLE:
                text = "\n\n".join(t for t in (title, text) if t)
                title = title[:MAX_TITLE].rsplit(" ", 1)[0] + "…"
            if title:
                file_number = _line(row.get("agendaObjectItemNumber"))
                attachments = (parse_attachment(client, a) for a in row.get("attachmentsList") or [])
                items.append(Item(
                    id=str(row["id"]),
                    title=title,
                    text=text,
                    number=".".join(trail),
                    matter_id=file_number or None,
                    matter_file=file_number or None,
                    attachments=[a for a in attachments if a],
                    # The item's own modifiedDate is the time of the request, so the
                    # agenda's posting time stands in for it.
                    modified=modified,
                ))
        items += parse_items(client, row.get("childItems") or [], modified, trail)
    return items


def parse_meeting(client: str, event: dict, agenda: dict | None) -> Meeting:
    """Turn an event and its agenda into a Meeting. An unpublished agenda has no items."""
    start = _local(event["startDateTime"])
    agendas = sorted(_published(event, "Agenda"), key=lambda f: f.get("publishOn") or "")
    meeting = Meeting(
        body=Body(SOURCES[client], str(event["eventCategoryId"]),
                  _line(event.get("categoryName")) or _line(event.get("eventName"))),
        id=str(event["id"]),
        date=start.date(),
        time=start.strftime("%I:%M %p").lstrip("0"),
        agenda_url=file_url(client, agendas[-1]["fileId"]) if agendas else None,
        url=f"{PORTAL.format(client=client)}/event/{event['id']}/overview",
        modified=posted_at(event),
    )
    if agenda and agenda.get("agendaIsPublish"):
        meeting.items = parse_items(client, agenda.get("items") or [], meeting.modified)
    return meeting


def read_meeting(fetcher: Fetcher, client: str, event: dict) -> Meeting:
    """Fetch an event's agenda, if it has one, and build the Meeting."""
    agenda = fetch_agenda(fetcher, client, event["agendaId"]) if event.get("agendaId") else None
    return parse_meeting(client, event, agenda)


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead for each client.

    with_text and cache are unused: an agenda arrives with its items' text, and
    the API is fast enough to read everything again each run.

    Returns the meetings and, for any client that failed, what went wrong, so one
    client being down doesn't stop the others.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        try:
            for event in fetch_events(fetcher, client, start, end):
                # The filter already limits dates; checking again keeps a stray
                # event out even if the API ignores the filter.
                if is_listed(event) and start <= _local(event["startDateTime"]).date() <= end:
                    meetings.append(read_meeting(fetcher, client, event))
        except (httpx.HTTPError, ValueError, KeyError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"CivicClerk {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
