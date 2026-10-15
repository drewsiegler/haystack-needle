"""iCompass ElectronicSchoolBoard (ic-board.com) adapter: Mountain View-Los Altos HSD.

The district's public board site is mvlapub.ic-board.com. It has no robots.txt,
so the usual one request a second applies.

- /Reference_Library/ESB_RSS/rss.xml lists the published agendas of the last
  three months: date and time, meeting type, and the meeting id (rssid=). An
  agenda appears there once it's published, about five days before the meeting.
- /public_agendaview.aspx?mtgId= is the agenda: each item's number, title, type
  (Action, Info) and a "revised" mark, with a link to the item's own page.
- /public_itemview.aspx?ItemId=&mtgId= is an item: labelled fields (Summary,
  Funding, Recommendation, Enclosure...) and its PDF attachments at
  /Attachments/{id}.pdf. A board agenda has about 40 items, one request each.

So item pages are read once and kept in the run's state (`cache`): again only
when the item's title or revised mark changes, or on a new day, in case a file
was added quietly. Their fields are the item's text, which arrives with the
agenda, so item_text has nothing left to read.
"""

from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {"mvla": Source("icboard", "mvla", "Mountain View-Los Altos HSD")}
HOSTS = {"mvla": "mvlapub.ic-board.com"}
BODY = ("board", "Board of Trustees")
# Item page fields that aren't item text: the meeting, the subject (the title
# again), the attachment list, the LCAP goal's heading and who signed off.
NOT_TEXT = {"MtgDate", "Category", "Type", "Subject", "Attachment", "StratName"}

_ROOT_ITEM = re.compile(
    r'_ri_lblItemNum">([^<]*)</span>.*?_ri_hypItemTitle"[^>]*href="([^"]*)">(.*?)</a>'
    r'\s*<span id="[^"]*_ri_lblRevised"[^>]*>(.*?)</span>.*?_ri_lblItemType">([^<]*)</span>', re.S)
_FIELD = re.compile(r'<span id="ctl00_ContentPlaceHolder1_ucBody_lbl(\w+)"[^>]*>')
_SPAN = re.compile(r"<(/?)span\b", re.I)
_ATTACHMENT = re.compile(r'_hypAttach"[^>]*href="([^"]*/Attachments/([0-9A-Fa-f-]+)\.pdf)"[^>]*>(.*?)</a>', re.S)
_BREAKS = re.compile(r"</?(?:br|p|div|li|ul|ol|tr|table|h[1-6])\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]*>")


def _base(client: str) -> str:
    return f"https://{HOSTS[client]}"


def calendar_url(client: str) -> str:
    """The district's list of agendas, for people checking on a problem."""
    return f"{_base(client)}/com/agenda_list.aspx"


def feed_url(client: str) -> str:
    return f"{_base(client)}/Reference_Library/ESB_RSS/rss.xml"


def agenda_url(client: str, meeting_id: str) -> str:
    return f"{_base(client)}/public_agendaview.aspx?mtgId={meeting_id}"


def _paragraphs(fragment: str) -> str:
    text = html.unescape(_TAGS.sub("", _BREAKS.sub("\n", fragment))).replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _line(fragment: str) -> str:
    return " ".join(_paragraphs(fragment).split())


def parse_feed(data: bytes) -> list[dict]:
    """The published agendas in the feed: id, date, time and meeting type."""
    rows = []
    for entry in ET.fromstring(data.lstrip(b"\xef\xbb\xbf")).iter("item"):
        found = re.search(r"rssid=(\d+)", entry.findtext("link") or "")
        when = " ".join((entry.findtext("title") or "").split()).removesuffix(" Agenda")
        if not found:
            continue
        start = datetime.strptime(when, "%b %d %Y %I:%M%p")
        rows.append({"id": found.group(1), "date": start.date(),
                     "time": start.strftime("%I:%M %p").lstrip("0"),
                     "type": " ".join((entry.findtext("description") or "").split())})
    return rows


def parse_agenda(page: str) -> list[dict]:
    """The agenda's items in order (section headings left out)."""
    if "lblItemNum" not in page and "dgAgenda" not in page:
        raise ValueError("the agenda page didn't look like an ElectronicSchoolBoard agenda")
    return [{"number": _line(number).rstrip("."), "href": html.unescape(href), "title": _line(title),
             "revised": _line(revised), "type": _line(kind)}
            for number, href, title, revised, kind in _ROOT_ITEM.findall(page)]


def _span_body(page: str, start: int) -> str:
    """The inside of the span that opens just before start, nested spans and all."""
    depth = 1
    for tag in _SPAN.finditer(page, start):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return page[start:tag.start()]
    return page[start:]


def parse_item(client: str, page: str) -> dict:
    """An item page's text (its labelled fields) and attachments."""
    fields = {m.group(1): _paragraphs(_span_body(page, m.end())) for m in _FIELD.finditer(page)}
    text = []
    for name, value in fields.items():
        if name in NOT_TEXT or name.startswith(("Label", "RecBy")) or not value:
            continue
        label = fields.get(f"Label{name}") or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)
        text.append(f"{label}: {value}")
    files = [[file_id.lower(), _line(name) or "Attachment", url]
             for url, file_id, name in _ATTACHMENT.findall(page)
             if url.startswith(_base(client) + "/")]
    return {"text": "\n\n".join(text), "files": files}


def _due(entry: dict | None, sig: str, today: date) -> bool:
    return entry is None or entry["sig"] != sig or entry["read"] != today.isoformat()


def read_meeting(fetcher: Fetcher, client: str, row: dict, cache: dict, today: date) -> Meeting:
    """The meeting's agenda, with each item's text and attachments from its page,
    read now if it's new, changed or last read before today."""
    meeting = Meeting(
        body=Body(SOURCES[client], *BODY), id=row["id"], date=row["date"], time=row["time"],
        agenda_url=agenda_url(client, row["id"]))
    page = fetcher.get(agenda_url(client, row["id"])).decode("utf-8", errors="replace")
    for line in parse_agenda(page):
        item_id = re.search(r"ItemId=([^&]+)", line["href"]).group(1)
        sig = f"{line['title']}|{line['revised']}"
        entry = cache.get(item_id)
        if _due(entry, sig, today):
            try:
                found = parse_item(client, fetcher.get(_base(client) + line["href"]).decode("utf-8", errors="replace"))
            except httpx.HTTPError as error:
                print(f"ElectronicSchoolBoard {client}: couldn't read item {line['number']}: "
                      f"{type(error).__name__}", file=sys.stderr)
            else:
                entry = cache[item_id] = {"meeting": row["id"], "date": row["date"].isoformat(), "sig": sig,
                                          "read": today.isoformat(), **found}
        meeting.items.append(Item(
            id=item_id, title=line["title"], number=line["number"],
            text=entry["text"] if entry else "",
            attachments=[Attachment(id=file_id, name=name, url=url)
                         for file_id, name, url in (entry["files"] if entry else [])],
        ))
    return meeting


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: the item page's fields came with the agenda."""
    return ""


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead.

    with_text is unused: the item pages are where the attachments are listed, so
    they're always read, and kept in cache (the run's state) between runs.

    Returns the meetings and, for any district that failed, what went wrong.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    cache = {} if cache is None else cache
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        known = cache.setdefault(client, {})
        # Forget items of meetings that are over: past every run's window, so a
        # near-meeting run (0 days back) keeps what the full runs still read.
        oldest = (today - timedelta(days=max(days_back, DAYS_BACK))).isoformat()
        for item_id in [k for k, v in known.items() if v["date"] < oldest]:
            del known[item_id]
        try:
            for row in parse_feed(fetcher.get(feed_url(client))):
                if start <= row["date"] <= end:
                    meetings.append(read_meeting(fetcher, client, row, known, today))
        except (httpx.HTTPError, ValueError, ET.ParseError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"ElectronicSchoolBoard {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
