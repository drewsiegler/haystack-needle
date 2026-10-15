"""Midpen adapter: the Midpeninsula Regional Open Space District's board and committees.

Midpen posts its agendas on its own website, www.openspace.org (Drupal). Its
robots.txt is Drupal's stock one: it shuts out /admin/, /search/, /user/ and
the like, not the meeting pages or /sites/default/files/, so the usual one
request a second applies. The Laserfiche portal it links holds older records;
the current year's agendas are all on the website.

- /get-involved/public-meetings lists this year's meetings, upcoming first, in
  one table: the meeting's name ("Board Meeting", "Special Board Meeting",
  "Planning & Natural Resources Committee Meeting"), its date and time, and a
  link to its page. The name gives the body; the page's slug doesn't (a
  committee meeting can sit at /about-us/meetings/bod-...).
- /about-us/meetings/{slug} is the meeting's page. Once the agenda is posted
  (72 hours ahead, 24 for a special meeting) it links the agenda PDF and the
  agenda packet, one PDF per item, named for the item: "Item 4
  AwardofContractforSensitiveSpeciesProtectionProgram_R-26-149.pdf".

So each meeting has an item for the agenda, with its PDF, and one for each
packet file, titled from the file's name with its staff report number. The
matcher reads the PDFs. A file replaced on the site gets a new name (a
"_REVISED" or "_1" on the end), so it's read again; the item keeps its id,
which comes from the meeting and the item's number.
"""

from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
PACIFIC = ZoneInfo("America/Los_Angeles")
SITE = "https://www.openspace.org"
LIST_URL = f"{SITE}/get-involved/public-meetings"

SOURCES = {"midpen": Source("midpen", "midpen", "Midpen")}

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_LINK = re.compile(r'href="(/about-us/meetings/[a-z0-9-]+)"[^>]*>(.*?)</a>', re.S)
_WHEN = re.compile(r"activity-date'>([^<]*)<.*?activity-time'>([^<]*)<", re.S)
# Each field on a meeting page ("agenda", "agenda_packet", "minutes"...) opens with
# a div like this; a file link belongs to the field opened last before it.
_FIELD = re.compile(r'<div class="field--(?!file\b|item\b)([a-z_]+) field-')
_PDF = re.compile(r'href="((?:https://www\.openspace\.org)?/sites/default/files/[^"?#]+?\.pdf)"', re.I)
_NUMBER = re.compile(r"(?i)^(special\s+)?(?:item\s*)?(\d+(?:\.\d+)?[a-z]?)(?:\s*-\s*|[\s_]+)(.*)$")
_REPORT = re.compile(r"R-\d{2}-\d{2,4}")
# Small words run into the word before them in a file name: "AwardofContractfor".
# Not "and", "at", "in" or "on", which end too many words (Grassland, Habitat, Mountain).
_SMALL = re.compile(r"(?<=[a-z]{3})(for|of|to|with|the)(?= [A-Z])")
_TAGS = re.compile(r"<[^>]*>")


def calendar_url(client: str) -> str:
    """Midpen's public meetings page, for people checking on a problem."""
    return LIST_URL


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", fragment)).split())


def body_name(meeting_name: str) -> str:
    """The body from a meeting's name: "Special Board Meeting" is the Board of Directors."""
    name = re.sub(r"^(?:Special|Regular|Adjourned)\s+", "", meeting_name)
    name = re.sub(r"\s+Meeting$", "", name)
    return "Board of Directors" if name == "Board" else name


def body_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_list(page: str) -> list[dict]:
    """Every meeting in the public meetings table: its page, name, date and time."""
    if "views-field-field-date" not in page:
        raise ValueError("the page didn't look like Midpen's public meetings list")
    rows = []
    for row in _ROW.finditer(page):
        link, when = _LINK.search(row.group(1)), _WHEN.search(row.group(1))
        if not (link and when):
            continue
        rows.append({
            "id": link.group(1).rsplit("/", 1)[1],
            "path": link.group(1),
            "name": _text(link.group(2)),
            "date": datetime.strptime(_text(when.group(1)), "%A, %B %d, %Y").date(),
            "time": _text(when.group(2)),
        })
    return rows


def parse_files(page: str) -> dict[str, list[str]]:
    """The PDF links on a meeting page, by field: {"agenda": [...], "agenda_packet": [...]}."""
    if '<article class="event-page"' not in page:
        raise ValueError("the page didn't look like a Midpen meeting page")
    fields = [(m.start(), m.group(1)) for m in _FIELD.finditer(page)]
    files: dict[str, list[str]] = {}
    for link in _PDF.finditer(page):
        field = next((name for start, name in reversed(fields) if start < link.start()), None)
        if field:
            url = link.group(1) if link.group(1).startswith("http") else SITE + link.group(1)
            if url not in files.setdefault(field, []):
                files[field].append(url)
    return files


def _tidy(words: str) -> str:
    """Spaces back into a run-together file name: "Class&CompPlanforField" -> "Class & Comp Plan for Field"."""
    words = re.sub(r"\s*&\s*", " & ", words.replace("_", " "))
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", words)
    words = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", words)
    words = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Z])", " ", words)
    for _ in range(2):  # "Planforthe Board": "the" first, then "for"
        words = _SMALL.sub(r" \1", words)
    return " ".join(words.split())


def file_item(url: str) -> dict:
    """An agenda packet file's item number, title and staff report number, from its name.

    "Special Item 2 OptionstoFillWard3Vacancy_R-26-147_REVISED.pdf" is special item
    2, "Options to Fill Ward 3 Vacancy (R-26-147)".
    """
    stem = re.sub(r"(?i)\.pdf$", "", unquote(url.rsplit("/", 1)[1]))
    stem = re.sub(r"_\d{1,2}$", "", stem)  # Drupal's suffix for a name already taken
    found = _NUMBER.match(stem)
    special, number, rest = (found.group(1), found.group(2), found.group(3)) if found else (None, "", stem)
    report = _REPORT.search(rest)
    rest = _REPORT.sub(" ", rest)
    rest = re.sub(r"(?<![0-9])(?:19|20)\d{6}(?![0-9])", " ", rest)  # a date: 20261006
    rest = re.sub(r"(?i)(?<![A-Za-z])(revised|draft)(?![a-z])", " ", rest)
    title = _tidy(rest) or (f"Item {number}" if number else "Agenda packet file")
    if report:
        title += f" ({report.group(0)})"
    return {"number": f"Special {number}" if special and number else number, "title": title,
            "report": report.group(0) if report else None}


def _item_id(meeting_id: str, number: str, url: str) -> str:
    if number:
        return f"{meeting_id}/item-{body_id(number)}"
    return f"{meeting_id}/file-{body_id(unquote(url.rsplit('/', 1)[1]))}"


def build_meeting(row: dict, files: dict[str, list[str]]) -> Meeting:
    """A meeting with an item for its agenda and one for each agenda packet file.
    Before the agenda is posted the page has no files, and the meeting no items."""
    name = body_name(row["name"])
    agenda = files.get("agenda", [])
    items = []
    if agenda:
        items.append(Item(
            id=f"{row['id']}/agenda", title="Agenda",
            attachments=[Attachment(id=url, name=unquote(url.rsplit("/", 1)[1]), url=url) for url in agenda],
        ))
    seen = {item.id for item in items}
    for url in files.get("agenda_packet", []):
        info = file_item(url)
        item_id = _item_id(row["id"], info["number"], url)
        if item_id in seen:  # two files under one number: keep them apart
            item_id = _item_id(row["id"], "", url)
        seen.add(item_id)
        items.append(Item(
            id=item_id, title=info["title"], number=info["number"], matter_file=info["report"],
            attachments=[Attachment(id=url, name=unquote(url.rsplit("/", 1)[1]), url=url)],
        ))
    return Meeting(
        body=Body(SOURCES["midpen"], body_id(name), name),
        id=row["id"],
        date=row["date"],
        time=row["time"],
        agenda_url=agenda[0] if agenda else None,
        url=SITE + row["path"],
        items=items,
    )


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: each item's PDF is its attachment."""
    return ""


TEXT_TTL = None  # item_text has nothing to read, so there's nothing to re-read


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead: the list page,
    then each of those meetings' pages (a handful a run).

    with_text and cache are unused: the PDFs are read as attachments.

    Returns the meetings and, if the site failed, what went wrong.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        try:
            rows = parse_list(fetcher.get(LIST_URL).decode("utf-8", errors="replace"))
            for row in rows:
                if start <= row["date"] <= end:
                    page = fetcher.get(SITE + row["path"]).decode("utf-8", errors="replace")
                    meetings.append(build_meeting(row, parse_files(page)))
        except (httpx.HTTPError, ValueError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"Midpen: {failures[client]}", file=sys.stderr)
    return meetings, failures
