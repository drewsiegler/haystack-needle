"""IQM2 adapter: Santa Clara County (the Board of Supervisors, its committees,
boards and commissions) and VTA.

IQM2 (Granicus) has no API, and each portal's robots.txt asks for one request a
minute (Crawl-delay: 60). So the adapter reads as little as it can, and keeps
what it read in the run's state (`cache`) instead of reading it again:

- Citizens/Calendar.aspx?From=&To= lists every meeting in the window with its
  board, type, status and agenda links: one request per portal per run. The
  RSS feed (Services/RSS.aspx?Feed=Calendar) works for VTA but returns an error
  page for the county, and it lists only meetings with published documents.
- Citizens/Detail_Meeting.aspx?ID= is the agenda as a web page: each item with
  its "possible action" note, its legislative file and its attachments. It's
  read when a meeting's agenda first appears or its calendar links change, then
  again every couple of hours in the last 3 days (for add-ons) or daily before
  that. Soonest meetings go first, alternating between the portals.
- Citizens/Detail_LegiFile.aspx?ID= is an item's staff report (item_text). It
  has no modified time, so it's read once (TEXT_TTL = None).
- Citizens/FileOpen.aspx?Type=4&ID= is an attachment. Printouts (Type=30) repeat
  the staff report as a PDF and are skipped.

A legislative file keeps its ID from meeting to meeting, so it's the item's
matter_id. IQM2 has no matter history to read, so there's no matter_history:
"this moved forward" comes from past alerts.
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
from collections import deque
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Deferred, Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
NEAR_DAYS = 3  # in the last 3 days before a meeting, re-read its page more often
NEAR_REFRESH = timedelta(minutes=110)
REFRESH = timedelta(hours=24)
TEXT_TTL = None  # read an item's staff report once; IQM2 gives no modified time
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {
    client: Source("iqm2", client, name)
    for client, name in [
        ("sccgov", "Santa Clara County"),
        ("santaclaravta", "VTA"),
    ]
}


class PortalError(Exception):
    """The portal answered with its error page instead of the page asked for."""


def _base(client: str) -> str:
    return f"https://{client}.iqm2.com/Citizens"


def calendar_url(client: str) -> str:
    """The portal's public meeting calendar, for people checking on a problem."""
    return f"{_base(client)}/Calendar.aspx"


def calendar_page(client: str, start: date, end: date) -> str:
    return f"{_base(client)}/Calendar.aspx?From={start:%m/%d/%Y}&To={end:%m/%d/%Y}"


def meeting_page(client: str, meeting_id: str) -> str:
    return f"{_base(client)}/Detail_Meeting.aspx?ID={meeting_id}"


def file_url(client: str, kind: int, file_id: str) -> str:
    return f"{_base(client)}/FileOpen.aspx?Type={kind}&ID={file_id}&Inline=True"


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, one line per <br> or block."""
    fragment = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    fragment = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d)>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    lines = (re.sub(r"\s+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _decode(data: bytes) -> str:
    page = data.decode("utf-8", "replace")
    if "Oops.. an error occured" in page:
        raise PortalError("the portal showed its error page")
    return page


def parse_calendar(page: str) -> list[dict]:
    """Each meeting on a calendar page: id, date, time, board, type, status, agenda, sig."""
    if "MeetingRow" not in page and "pnlMeetings" not in page:
        raise PortalError("the calendar page has no meeting list")
    rows = []
    for chunk in page.split('class="Row MeetingRow')[1:]:
        link = re.search(r'href="/Citizens/Detail_Meeting\.aspx\?ID=(\d+)" title="([^"]*)"', chunk)
        if not link:
            continue
        tip = html.unescape(link.group(2))
        when = datetime.strptime(" ".join(tip.split("\r", 1)[0].split()), "%A, %B %d, %Y %I:%M %p")
        field = {k: v.strip() for k, v in re.findall(r"(Board|Type|Status):\t([^\r]*)", tip)}
        links = re.findall(r"<a href='FileOpen\.aspx\?Type=(\d+)&(?:amp;)?ID=(\d+)", chunk)
        agenda = next((file_id for kind, file_id in links if kind == "14"), None)
        rows.append({
            "id": link.group(1), "date": when.date(), "time": when.strftime("%-I:%M %p"),
            "board": field.get("Board", ""), "type": field.get("Type", ""),
            "status": field.get("Status", ""), "agenda": agenda,
            # When the calendar's links or the time change, read the agenda page again.
            "sig": f"{when:%Y-%m-%d %H:%M}|{field.get('Status', '')}|"
                   + ",".join(f"{kind}:{file_id}" for kind, file_id in links),
        })
    return rows


def _cell(row: str, cls: str) -> str | None:
    found = re.search(rf"<td class='{cls}'[^>]*>(.*?)</td>", row, re.S)
    return found.group(1) if found else None


def parse_outline(page: str, meeting_id: str) -> list[list]:
    """The agenda items on a meeting page, as compact lists for the state:
    [id, number, title, note, legislative file id or None, [[attachment id, name], ...]].

    Rows are indented with empty cells; a row's attachments and its "possible
    action" note sit one level under it. Bold rows without a number are section
    headings. Item numbers join the levels: "9.a", or "2.1" for VTA.
    """
    start = page.find("<table id='MeetingDetail'")
    if start < 0:
        return []  # agenda not posted as a web page
    table = page[start:page.find("</table>", start)]
    items: list[list] = []
    path: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    current = None
    for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
        depth = re.match(r"(?:<td></td>)*", row).group(0).count("<td>")
        note = _cell(row, "Comments")
        if note is not None:
            if current is not None:
                current[3] = "\n".join(t for t in (current[3], _text(note)) if t)
            continue
        cell = _cell(row, "Title")
        if cell is None:
            continue
        href = (re.search(r"href='([^']*)'", cell) or [None, ""])[1]
        title = _text(re.sub(r"<span class='ItemTag'.*?</span>", "", cell, flags=re.S)).replace("\n", " ")
        upload = re.search(r"FileOpen\.aspx\?Type=(\d+)&(?:amp;)?ID=(\d+)", href)
        if upload:
            if upload.group(1) == "4" and current is not None:
                current[5].append([upload.group(2), title])
            continue
        number = _text(_cell(row, "Num") or "").rstrip(".")
        while path and path[-1][0] >= depth:
            path.pop()
        path.append((depth, number))
        if "<strong>" in cell and not number:
            current = None  # a section heading
            continue
        legifile = re.search(r"Detail_LegiFile\.aspx\?[^']*?\bID=(\d+)", href)
        if legifile:
            item_id = legifile.group(1)
        else:  # by title, so the ID holds when items are added above it
            item_id = f"{meeting_id}-{hashlib.sha256(title.encode()).hexdigest()[:10]}"
            seen[item_id] = seen.get(item_id, 0) + 1
            item_id += f"-{seen[item_id]}" if seen[item_id] > 1 else ""
        current = [item_id, ".".join(n for _, n in path if n), title, "",
                   legifile.group(1) if legifile else None, []]
        if title:
            items.append(current)
    return items


# The report's sections on a legislative file page, in page order (some files lack some).
REPORT_SECTIONS = ("ContentPlaceholder1_divFinancialImpact", "divDocumentComments", "divItemDiscussion", "divBody")


def parse_item_text(page: str) -> str:
    """The staff report on a legislative file page: financial impact, recommendation, discussion."""
    found = [i for i in (page.find(f'id="{section}"') for section in REPORT_SECTIONS) if i >= 0]
    if not found:
        return ""
    start = page.rfind("<", 0, min(found))
    end = page.find('id="divAttachmentEdit"', start)
    return _text(page[start:page.rfind("<", 0, end) if end > 0 else len(page)])


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """The item's staff report, or "" if it has no legislative file."""
    if not item.matter_id:
        return ""
    return parse_item_text(_decode(fetcher.get(f"{_base(client)}/Detail_LegiFile.aspx?ID={item.matter_id}")))


def build_meeting(client: str, row: dict, outline: list[list]) -> Meeting:
    return Meeting(
        body=Body(SOURCES[client], row["board"], row["board"]),
        id=row["id"], date=row["date"], time=row["time"],
        agenda_url=file_url(client, 14, row["agenda"]) if row["agenda"] else None,
        url=meeting_page(client, row["id"]),
        items=[Item(id=item_id, title=title, text=note, number=number, matter_id=legifile,
                    attachments=[Attachment(a_id, name, file_url(client, 4, a_id)) for a_id, name in files])
               for item_id, number, title, note, legifile, files in outline],
    )


def pages_due(rows: list[dict], known: dict, today: date, now: datetime) -> list[dict]:
    """Meetings whose agenda page should be read this run: new or changed ones
    first, then stale ones, soonest first within each."""
    due = []
    for row in rows:
        if not row["agenda"]:
            continue  # no agenda posted yet
        entry = known.get(row["id"])
        if entry is None or entry["sig"] != row["sig"]:
            due.append((0, row["date"], row))
            continue
        near = row["date"] <= today + timedelta(days=NEAR_DAYS)
        if now - datetime.fromisoformat(entry["fetched"]) >= (NEAR_REFRESH if near else REFRESH):
            due.append((1, row["date"], row))
    return [row for *_, row in sorted(due, key=lambda d: d[:2])]


def read_pages(fetcher: Fetcher, due: dict[str, list[dict]], cache: dict, now: datetime) -> None:
    """Read due agenda pages into the cache, taking turns between portals (each has
    its own minute between requests), until each portal's queue is empty or deferred."""
    queues = {client: deque(rows) for client, rows in due.items() if rows}
    while queues:
        for client in list(queues):
            row = queues[client].popleft()
            if not queues[client]:
                del queues[client]
            try:
                outline = parse_outline(_decode(fetcher.get(meeting_page(client, row["id"]))), row["id"])
            except Deferred:
                queues.pop(client, None)  # this portal's time is up; the rest wait for the next run
                continue
            except (httpx.HTTPError, PortalError) as error:
                print(f"IQM2 {client}: couldn't read meeting {row['id']}: {type(error).__name__}: {error}",
                      file=sys.stderr)
                continue
            cache[client][row["id"]] = {"date": row["date"].isoformat(), "sig": row["sig"],
                                        "fetched": now.isoformat(), "items": outline}


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None, now: datetime | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead for each portal.

    cache is kept in the state between runs: each meeting's agenda outline and
    when it was read. A meeting whose page hasn't been read yet comes back with
    no items, and is read on a later run.
    """
    now = now or datetime.now(timezone.utc)
    today = today or now.astimezone(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    cache = {} if cache is None else cache
    listed: dict[str, list[dict]] = {}
    failures: dict[str, str] = {}
    for client in clients:
        try:
            rows = parse_calendar(_decode(fetcher.get(calendar_page(client, start, end))))
        except (httpx.HTTPError, PortalError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"IQM2 {client}: {failures[client]}", file=sys.stderr)
            continue
        listed[client] = [r for r in rows if start <= r["date"] <= end and r["status"] != "Cancelled"]
        known = cache.setdefault(client, {})
        keep = {r["id"] for r in listed[client]}
        for meeting_id, entry in list(known.items()):
            day = date.fromisoformat(entry["date"])
            if day < today - timedelta(days=DAYS_BACK) or (start <= day <= end and meeting_id not in keep):
                del known[meeting_id]  # over, cancelled or taken off the calendar
    read_pages(fetcher, {c: pages_due(rows, cache[c], today, now) for c, rows in listed.items()}, cache, now)

    meetings = []
    for client, rows in listed.items():
        for row in sorted(rows, key=lambda r: (r["date"], r["id"])):
            entry = cache[client].get(row["id"])
            meeting = build_meeting(client, row, entry["items"] if entry else [])
            if with_text:
                for item in meeting.items:
                    try:
                        item.text = "\n\n".join(t for t in (item.text, item_text(fetcher, client, item)) if t)
                    except (httpx.HTTPError, PortalError) as error:
                        print(f"IQM2 {client}: couldn't read item {item.id}: {type(error).__name__}",
                              file=sys.stderr)
            meetings.append(meeting)
    return meetings, failures
