"""eScribe adapter: the Campbell City Council.

Campbell moved its council agendas to eScribe in October 2025; its commissions
stay on AgendaCenter (the CivicPlus adapter). The portal host has no robots.txt,
but requests to it are 10 seconds apart (CRAWL_DELAYS in needle/fetch.py)
because of the bot check on its files, below.

- POST /MeetingsCalendarView.aspx/GetCalendarMeetings with the JSON body
  {"calendarStartDate": "YYYY-MM-DD", "calendarEndDate": "YYYY-MM-DD"} lists the
  meetings in that window as {"d": [...]}. It's the call the portal's own
  calendar makes. Each meeting has a GUID, its type, a local start time and its
  documents. A meeting shows up only once its agenda is posted, and nothing says
  when that was.
- /Meeting.aspx?Id={GUID}&Agenda=Agenda&lang=English is the agenda as HTML, item
  by item: number, title, description, recommended action and attachments. A
  revised agenda (Agenda=Merged) replaces it; an addendum (Agenda=Addendum) lists
  only the items it adds or changes. Item numbers like 1087 stay the same from
  one version to the next.
- /FileStream.ashx?DocumentId=N is an attachment or a posted PDF. A replaced file
  gets a new DocumentId, so a file is read once. These downloads sit behind a
  Cloudflare bot check: on Oct 14, 2026 it began refusing them after 15 files
  at one a second, while the calendar and agenda pages still loaded. If it
  comes back, Fetcher leaves the host alone for the rest of the run (see
  docs/sources.md).

An item's text arrives with its agenda, so item_text has nothing left to read,
and there's no matter history: "this moved forward" comes from past alerts.
"""

from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
MAX_TITLE = 300  # a longer title is cut here and the whole of it goes in the item's text
# The text comes with the agenda, so match it fresh every run instead of reusing
# last run's snippets: a revised agenda keeps its item numbers.
TEXT_TTL = timedelta(0)
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {"campbell": Source("escribe", "campbell", "Campbell")}
HOSTS = {"campbell": "pub-campbell.escribemeetings.com"}

_GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_ITEM = re.compile(r'<div class="AgendaItem AgendaItem(\d+)\b')
_COUNTER = re.compile(r'class="AgendaItemCounter"[^>]*>(.*?)</div>', re.S)
_TITLE = re.compile(r'class="AgendaItemTitle"[^>]*>(.*?)</div>', re.S)
_MOTION_LABEL = re.compile(r'class="MotionLabel"[^>]*>(.*?)</div>', re.S)
_ATTACHMENT = re.compile(r'<a class="Link"[^>]*?href="FileStream\.ashx\?DocumentId=(\d+)"[^>]*>(.*?)</a>', re.S)
_DOCUMENT = re.compile(r"DocumentId=(\d+)")
_BREAKS = re.compile(r"</?(?:br|p|div|li|ul|ol|tr|table|h[1-6])\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]*>")


def _base(client: str) -> str:
    return f"https://{HOSTS[client]}"


def calendar_url(client: str) -> str:
    """The council's meeting portal, for people checking on a problem."""
    return _base(client) + "/"


def agenda_page_url(client: str, meeting_id: str, kind: str = "Agenda") -> str:
    """The agenda as HTML. kind is Agenda, Merged (revised) or Addendum."""
    return f"{_base(client)}/Meeting.aspx?Id={meeting_id}&Agenda={kind}&lang=English"


def file_url(client: str, document_id: str) -> str:
    return f"{_base(client)}/FileStream.ashx?DocumentId={document_id}"


def _paragraphs(fragment: str | None) -> str:
    text = _TAGS.sub("", _BREAKS.sub("\n", fragment or ""))
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _line(fragment: str | None) -> str:
    return " ".join(_paragraphs(fragment).split())


def _start(row: dict) -> datetime:
    """eScribe's start times are local, like "2026/10/06 19:00:30"."""
    return datetime.strptime(row["StartDate"], "%Y/%m/%d %H:%M:%S")


def _documents(row: dict, kind: str, fmt: str) -> list[dict]:
    return [d for d in row.get("MeetingDocumentLink") or [] if d.get("Type") == kind and d.get("Format") == fmt]


def body_name(row: dict) -> str:
    """Regular, special, study and executive sessions are all the City Council."""
    name = _line(row.get("MeetingType")) or _line(row.get("MeetingName"))
    return "City Council" if name.startswith("City Council") else name


def agenda_kinds(row: dict) -> list[str]:
    """The HTML agendas to read, in order: the revised agenda alone if there is
    one, otherwise the agenda and any addendum."""
    if not row.get("HasAgenda"):
        return []
    if _documents(row, "Merged", "HTML"):
        return ["Merged"]
    return [kind for kind in ("Agenda", "Addendum") if _documents(row, kind, "HTML")]


def _after(part: str, marker: str) -> str:
    """The HTML after the tag that holds marker, or "" if there's none."""
    at = part.find(marker)
    return part[part.find(">", at) + 1:] if at >= 0 else ""


def _item_text(part: str) -> str:
    """An item's description and its recommended action ("Recommended Action: That...")."""
    texts = []
    description = part.find('class="AgendaItemDescription')
    motions = part.find('class="AgendaItemMotions')
    if description >= 0:
        stop = motions if motions > description else len(part)
        texts.append(_paragraphs(_after(part[:stop], 'class="AgendaItemDescription')))
    for motion in part[motions:].split('class="AgendaItemMotion"')[1:] if motions >= 0 else []:
        label = _MOTION_LABEL.search(motion)
        label = _line(label.group(1)) if label else ""
        text = _paragraphs(_after(motion, 'class="MotionText'))
        texts.append(f"{label}: {text}" if label and text else text)
    return "\n\n".join(t for t in texts if t)


def parse_items(client: str, page: str) -> list[Item]:
    """The agenda's items in order. A heading with numbered items under it (7 and
    7.1) is left out; a heading on its own, like an executive session's list of
    closed session matters, is kept."""
    if "AgendaItemContainer" not in page:
        raise ValueError("the page didn't look like an eScribe agenda")
    # The Details panel after the agenda repeats every attachment.
    end = page.find('<section class="Details')
    page = page[:end] if end >= 0 else page
    marks = list(_ITEM.finditer(page))
    rows = []
    for i, mark in enumerate(marks):
        # Everything up to the next item: sub-items come after their heading's own content.
        part = page[mark.start():marks[i + 1].start() if i + 1 < len(marks) else len(page)]
        counter, title = _COUNTER.search(part), _TITLE.search(part)
        rows.append((mark.group(1), _line(counter.group(1)).rstrip(".") if counter else "",
                     _line(title.group(1)) if title else "", part))
    items = []
    for i, (item_id, number, title, part) in enumerate(rows):
        following = rows[i + 1][1] if i + 1 < len(rows) else ""
        if not title or (number and following.startswith(number + ".")):
            continue
        text = _item_text(part)
        if len(title) > MAX_TITLE:
            text = "\n\n".join(t for t in (title, text) if t)
            title = title[:MAX_TITLE].rsplit(" ", 1)[0] + "…"
        attachments = {}
        for document_id, name in _ATTACHMENT.findall(part):
            name = _line(name)
            if name.lower().endswith(".pdf"):
                attachments.setdefault(document_id, Attachment(id=document_id, name=name,
                                                               url=file_url(client, document_id)))
        items.append(Item(id=item_id, title=title, text=text, number=number,
                          attachments=list(attachments.values())))
    return items


def merge_items(items: list[Item], later: list[Item]) -> list[Item]:
    """An addendum's items over the agenda's: a changed item takes the addendum's
    title and text and keeps every attachment; a new item goes on the end."""
    merged = {item.id: item for item in items}
    for item in later:
        earlier = merged.get(item.id)
        if earlier:
            seen = {a.id for a in item.attachments}
            item.attachments = [a for a in earlier.attachments if a.id not in seen] + item.attachments
        merged[item.id] = item
    return list(merged.values())


def fetch_meetings(fetcher: Fetcher, client: str, start: date, end: date) -> list[dict]:
    """Every meeting listed from start to end, inclusive."""
    answer = fetcher.post_json(f"{_base(client)}/MeetingsCalendarView.aspx/GetCalendarMeetings", {
        "calendarStartDate": start.isoformat(),
        "calendarEndDate": (end + timedelta(days=1)).isoformat(),
    })
    rows = answer.get("d") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise ValueError("the meeting calendar didn't answer with a list of meetings")
    return rows


def fetch_agenda(fetcher: Fetcher, client: str, meeting_id: str, kind: str) -> str:
    return fetcher.get(agenda_page_url(client, meeting_id, kind)).decode("utf-8", errors="replace")


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: the item's text came with its agenda."""
    return ""


def _meeting_id(row: dict) -> str:
    """The meeting's GUID, checked before it goes into a URL."""
    meeting_id = str(row["ID"])
    if not _GUID.fullmatch(meeting_id):
        raise ValueError(f"a meeting on the calendar had an unexpected ID: {meeting_id[:60]!r}")
    return meeting_id


def build_meeting(client: str, row: dict, pages: dict[str, str]) -> Meeting:
    """A Meeting from a calendar row and its HTML agendas ({kind: page})."""
    meeting_id = _meeting_id(row)
    start = _start(row)
    name = body_name(row)
    covers = [_DOCUMENT.search(d.get("Url") or "") for d in _documents(row, "AgendaCover", ".pdf")]
    covers = [c.group(1) for c in covers if c]
    kinds = list(pages)
    items: list[Item] = []
    for kind in kinds:
        items = merge_items(items, parse_items(client, pages[kind]))
    return Meeting(
        body=Body(SOURCES[client], re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), name),
        id=meeting_id,
        date=start.date(),
        time=start.strftime("%I:%M %p").lstrip("0"),
        agenda_url=file_url(client, covers[-1]) if covers else None,
        url=agenda_page_url(client, meeting_id, kinds[0]) if kinds else calendar_url(client),
        items=items,
    )


def read_meeting(fetcher: Fetcher, client: str, row: dict) -> Meeting:
    """Fetch a meeting's HTML agendas, if it has any, and build the Meeting."""
    meeting_id = _meeting_id(row)
    pages = {kind: fetch_agenda(fetcher, client, meeting_id, kind) for kind in agenda_kinds(row)}
    return build_meeting(client, row, pages)


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead for each city.

    with_text and cache are unused: an agenda arrives with its items' text, and
    a few meetings a month are quick to read again each run.

    Returns the meetings and, for any city that failed, what went wrong.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        try:
            for row in sorted(fetch_meetings(fetcher, client, start, end), key=lambda r: r["StartDate"]):
                # The calendar already limits dates; checking again keeps a stray
                # meeting out even if the portal ignores the window.
                if start <= _start(row).date() <= end:
                    meetings.append(read_meeting(fetcher, client, row))
        except (httpx.HTTPError, ValueError, KeyError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"eScribe {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
