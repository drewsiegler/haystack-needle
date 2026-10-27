"""Boardable adapter: the Foothill-De Anza Community College District and Fremont Union HSD.

Foothill-De Anza's board agendas moved from BoardDocs to Boardable. Its public
site is https://site.boardable.com/foothill-de-anza-community-college-district,
and site.boardable.com's robots.txt allows every bot. Fremont Union HSD posts its
agendas at https://site.boardable.com/fuhsd_board (found Oct 27, 2026; its first
meeting there is Oct 20). Boardable's app host is
another matter: app.boardable.com's robots.txt disallows everything but /login
and /start-trial, and that's where the public site's own JSON calls
(/api/public-sites/...) and its document links go. So the adapter reads only the
public site's pages, which carry their data with them, and the file links on
Amazon S3 that those pages hand out (checked Oct 15, 2026):

- The pages are a Nuxt app rendered on the server, with their data in a
  <script id="__NUXT_DATA__"> block: JSON in Nuxt's "devalue" form, a flat list
  whose entries point at each other by index. unflatten unpacks it.
- /{site}/meetings lists the next 10 upcoming meetings, soonest first, each
  with its groups (Boardable's name for a board or committee), and counts all
  the upcoming ones. The page ignores query strings: later pages and the past
  meetings come only from the JSON call, which robots.txt rules out.
- /{site}, the home page, lists the 4 most recent past meetings, without groups.
- /{site}/meetings/{slug} is one meeting: its groups, its time and its whole
  agenda as a tree of items, each with its text (recommended action, background,
  who submitted it) and its attachments. It also names the meetings just before
  and after it, of any group, which is how the adapter reaches upcoming meetings
  past the first 10, or past meetings older than the last 4.
- Each attachment comes with a link to its PDF on S3 that's good for 20 minutes
  (preSignedUrl, on boardable-production.s3.amazonaws.com, whose robots.txt
  answers 403: no rules). The run downloads that; alerts link to the
  attachment's public viewer, which lasts. A file the run gets to after its link
  has expired is refused, and the next run tries again with a fresh link.

An item's ID changes with each version of the agenda; its logical_id doesn't,
so that's the item's ID here. An attachment keeps its stored file name from one
version to the next, so a file is read once. Only the groups in GROUPS are
read: the board and its committees. An item's text arrives with its agenda, so
item_text has nothing left to read, and there's no matter history: "this moved
forward" comes from past alerts.
"""

from __future__ import annotations

import html
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

SITE = "https://site.boardable.com"
APP = "https://app.boardable.com"  # for links in alerts only: robots.txt keeps the run out
DAYS_BACK = 2
DAYS_AHEAD = 30
PAST_LISTED = 4  # past meetings on the home page
MAX_STEPS = 30  # meeting pages read past the end of either list, each way
MAX_TITLE = 300  # a longer title is cut here and the whole of it goes in the item's text
# The text comes with the agenda, so match it fresh every run instead of reusing
# last run's snippets: a revised agenda keeps its logical IDs.
TEXT_TTL = timedelta(0)
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {"fhda": Source("boardable", "fhda", "Foothill-De Anza CCD"),
           "fuhsd": Source("boardable", "fuhsd", "Fremont Union HSD")}
SITES = {"fhda": "foothill-de-anza-community-college-district", "fuhsd": "fuhsd_board"}
# The groups read on each site, by Boardable's name for them, and what alerts
# call them. The site's other groups are left out on purpose (docs/sources.md).
GROUPS = {
    "fhda": {
        "Main Governing Board": "Board of Trustees",
        "Audit and Finance Committee": "Audit and Finance Committee",
        "Citizens' Bond Oversight Committee": "Citizens' Bond Oversight Committee",
        "Financing Corporation Board of Directors": "Financing Corporation Board of Directors",
    },
    # Its board meetings are filed under one group for everyone invited to them.
    "fuhsd": {"All Board Meeting Users": "Board of Trustees"},
}

_DATA = re.compile(r'<script\b[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHOWN = re.compile(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2}) ?([AP]M)(?: ([A-Z]{2,5}))?")
_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1)}
_BREAKS = re.compile(r"</?(?:br|p|div|li|ul|ol|tr|table|h[1-6])\b[^>]*>", re.I)
_TAGS = re.compile(r"<[^>]*>")

# devalue's stand-ins for values JSON can't hold, and Nuxt's wrappers around reactive state.
_SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: -0.0}
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def calendar_url(client: str) -> str:
    """The site's list of meetings, for people checking on a problem."""
    return f"{SITE}/{SITES[client]}/meetings"


def site_url(client: str) -> str:
    return f"{SITE}/{SITES[client]}"


def meeting_url(client: str, slug: str) -> str:
    return f"{SITE}/{SITES[client]}/meetings/{slug}"


def viewer_url(organization: str, meeting_slug: str, short_slug: str) -> str:
    """The attachment's public viewer, the link the meeting page itself gives people."""
    return f"{APP}/{organization}/public-site-data/meetings/{meeting_slug}/documents/{short_slug}/view"


def unflatten(values: list):
    """A value from devalue's flat list: entry 0 is the value, and lists and objects
    hold the indexes of their members. A list that starts with a name is a typed
    value: Nuxt's reactive wrappers are unwrapped, a Date stays a string, a Set
    becomes a list and a Map a dict. Other typed values become None."""
    done: dict[int, object] = {}

    def get(index):
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"the page data pointed at {index!r}")
        if index < 0:
            return _SPECIAL.get(index)
        if index in done:
            return done[index]
        value = values[index]
        if isinstance(value, list) and value and isinstance(value[0], str):
            kind, rest = value[0], value[1:]
            if kind in _WRAPPERS:
                done[index] = get(rest[0]) if rest else None
            elif kind == "Date":
                done[index] = rest[0] if rest else None
            elif kind == "Set":
                done[index] = [get(i) for i in rest]
            elif kind == "Map":
                done[index] = {str(get(k)): get(v) for k, v in zip(rest[::2], rest[1::2])}
            elif kind == "null":  # an object without a prototype
                done[index] = {str(k): get(v) for k, v in zip(rest[::2], rest[1::2])}
            else:
                done[index] = None
            return done[index]
        if isinstance(value, list):
            done[index] = out = []
            out.extend(get(i) for i in value)
            return out
        if isinstance(value, dict):
            done[index] = out = {}
            for key, i in value.items():
                out[key] = get(i)
            return out
        done[index] = value
        return value

    return get(0)


def page_data(page: str) -> dict:
    """The data a site page was rendered from: {key: value} for each of its data calls."""
    found = _DATA.search(page)
    if not found:
        raise ValueError("the page didn't carry Boardable's page data")
    try:
        root = unflatten(json.loads(found.group(1)))
    except (json.JSONDecodeError, IndexError, TypeError, RecursionError) as error:
        raise ValueError(f"Boardable's page data didn't read: {error}") from error
    data = root.get("data") if isinstance(root, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Boardable's page data had no data in it")
    return data


def fetch_data(fetcher: Fetcher, url: str) -> dict:
    return page_data(fetcher.get(url).decode("utf-8", errors="replace"))


def _paragraphs(fragment: str | None) -> str:
    text = _TAGS.sub("", _BREAKS.sub("\n", fragment or ""))
    text = html.unescape(text).replace("​", "").replace("\xa0", " ")
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _line(fragment: str | None) -> str:
    return " ".join(_paragraphs(fragment).split())


def _slug(value) -> str:
    """A meeting's slug, checked before it goes into a URL."""
    slug = str(value or "")
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"a meeting had an unexpected slug: {slug[:60]!r}")
    return slug


def _shown(text: str | None) -> datetime | None:
    """A time as the site shows it, "Mon, Oct 5, 2026 4:00 PM PDT", in Pacific time."""
    found = _SHOWN.search(text or "")
    if not found or found.group(1) not in _MONTHS:
        return None
    month, day, year, hour, minute, half, zone = found.groups()
    hour = int(hour) % 12 + (12 if half == "PM" else 0)
    when = datetime(int(year), _MONTHS[month], int(day), hour, int(minute))
    if zone in ("UTC", "GMT"):
        when = when.replace(tzinfo=timezone.utc).astimezone(PACIFIC).replace(tzinfo=None)
    return when


def _from_utc(text: str | None) -> datetime | None:
    """A UTC time, "2026-10-05T23:00:00Z" or "2026-10-05 23:00:00", in Pacific time."""
    try:
        when = datetime.fromisoformat((text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(PACIFIC).replace(tzinfo=None)


def _wanted(client: str, name) -> str | None:
    """What alerts call a group, or None for a group that isn't read."""
    name = " ".join(str(name or "").replace("’", "'").split())
    return GROUPS[client].get(name)


def body_for(client: str, groups) -> Body | None:
    """The meeting's body: its first group that's read, or None."""
    for group in groups or []:
        name = _wanted(client, group.get("name"))
        if name:
            return Body(SOURCES[client], str(group["id"]), name)
    return None


@dataclass
class Listed:
    """A meeting on one of the site's lists."""
    slug: str
    start: datetime
    groups: list | None  # None: the list doesn't say


def upcoming(data: dict) -> tuple[list[Listed], bool]:
    """The meetings page's upcoming meetings, soonest first, and whether there are more."""
    listing = data.get("meetings-data")
    if not isinstance(listing, dict) or not isinstance(listing.get("meetings"), list):
        raise ValueError("the meetings page didn't list meetings")
    rows = []
    for row in listing["meetings"]:
        start = _shown(row.get("dateDisplay")) or _shown(row.get("start"))
        if start is None:
            start = datetime.fromisoformat(row["start"])  # the list's own start is local time
        rows.append(Listed(_slug(row.get("slug")), start, row.get("groups") or []))
    total = (listing.get("pagination") or {}).get("total") or 0
    return rows, isinstance(total, int) and total > len(rows)


def past(data: dict) -> list[Listed]:
    """The home page's most recent past meetings, latest first."""
    rows = data.get("past-meetings")
    if not isinstance(rows, list):
        raise ValueError("the home page didn't list past meetings")
    listed = []
    for row in rows:
        start = _from_utc(row.get("start"))
        if start is None:
            raise ValueError(f"a past meeting had an unexpected start: {str(row.get('start'))[:40]!r}")
        listed.append(Listed(_slug(row.get("slug")), start, None))
    return listed


def organization(data: dict) -> str | None:
    """The organization's slug, from the site details every page carries."""
    for value in data.values():
        site = value.get("data") if isinstance(value, dict) else None
        if isinstance(site, dict) and site.get("organization_slug"):
            return _slug(site["organization_slug"])
    return None


def meeting_entry(data: dict, slug: str) -> dict | None:
    """A meeting page's meeting and agenda, or None if the site sent the list instead
    (it does that for a meeting that isn't public)."""
    entry = data.get(f"meeting-{slug}")
    if entry is None and "meetings-data" in data:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("meeting"), dict):
        raise ValueError(f"the page for meeting {slug} didn't carry the meeting")
    return entry


def meeting_start(meeting: dict) -> datetime:
    """When the meeting starts. The page shows it in Pacific time; its own start is UTC."""
    start = _shown(meeting.get("date")) or _from_utc(meeting.get("start"))
    if start is None:
        raise ValueError(f"meeting {meeting.get('id')} had no start time that could be read")
    return start


def parse_attachment(organization: str, meeting_slug: str, upload: dict) -> Attachment | None:
    """An attachment with a PDF to read, or None for a link or a file with no PDF.
    Boardable makes a PDF of most files; the S3 link is to that PDF."""
    link = upload.get("preSignedUrl") or ""
    parts = urlsplit(link)
    short = (upload.get("uuid") or {}).get("short_slug") or ""
    if (upload.get("attached_link") or parts.scheme != "https"
            or not (parts.hostname or "").endswith(".amazonaws.com")
            or not parts.path.lower().endswith(".pdf") or not _SLUG.fullmatch(short)):
        return None
    return Attachment(
        id=str(upload.get("file") or f"upload-{upload['id']}"),
        name=_line(upload.get("title")) or _line(upload.get("name")) or _line(upload.get("filename")),
        url=viewer_url(organization, meeting_slug, short),
        download_url=link,
    )


def parse_items(organization: str, meeting_slug: str, nodes: list,
                trail: tuple[str, ...] = ()) -> list[Item]:
    """The agenda's items in order, numbered by position (7, 7.1, 7.1.2). A heading
    with items under it is left out unless it has text or attachments of its own."""
    items: list[Item] = []
    position = 0
    for node in sorted(nodes or [], key=lambda n: n.get("order") or 0):
        if node.get("is_private"):
            continue
        position += 1
        number = trail + (str(position),)
        children = node.get("agenda") or []
        title = _line(node.get("text"))
        text = _paragraphs(node.get("description"))
        attachments = [a for a in (parse_attachment(organization, meeting_slug, u)
                                   for u in node.get("uploads") or []) if a]
        if title and (not children or text or attachments):
            if len(title) > MAX_TITLE:
                text = "\n\n".join(t for t in (title, text) if t)
                title = title[:MAX_TITLE].rsplit(" ", 1)[0] + "…"
            items.append(Item(id=str(node.get("logical_id") or node["id"]), title=title, text=text,
                              number=".".join(number), attachments=attachments))
        items += parse_items(organization, meeting_slug, children, number)
    return items


def build_meeting(client: str, organization: str, entry: dict) -> Meeting | None:
    """A Meeting from a meeting page's entry, or None for a group that isn't read.
    A meeting whose materials aren't published yet has no items."""
    meeting = entry["meeting"]
    body = body_for(client, meeting.get("groups"))
    if body is None:
        return None
    slug = _slug(meeting.get("slug"))
    start = meeting_start(meeting)
    published = meeting.get("materials_published")
    return Meeting(
        body=body,
        id=str(meeting["id"]),
        date=start.date(),
        time=start.strftime("%I:%M %p").lstrip("0"),
        url=meeting_url(client, slug),
        items=parse_items(organization, slug, entry.get("agenda") or []) if published else [],
    )


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    """Nothing more to read: the item's text came with its agenda."""
    return ""


class Site:
    """One client's site, read once per run: each meeting page is fetched at most once."""

    def __init__(self, fetcher: Fetcher, client: str):
        self.fetcher, self.client = fetcher, client
        self.pages: dict[str, dict | None] = {}
        self.organization = SITES[client]

    def entry(self, slug: str) -> dict | None:
        if slug not in self.pages:
            self.pages[slug] = meeting_entry(fetch_data(self.fetcher, meeting_url(self.client, slug)), slug)
        return self.pages[slug]

    def read(self, start: date, end: date) -> list[Meeting]:
        """The read groups' meetings from start to end, inclusive, soonest first."""
        home = fetch_data(self.fetcher, site_url(self.client))
        self.organization = organization(home) or self.organization
        soon, more = upcoming(fetch_data(self.fetcher, calendar_url(self.client)))
        recent = past(home)
        found: dict[str, Meeting] = {}

        def take(slug: str) -> datetime | None:
            """Read a meeting page; keep the meeting if it's in the window. Returns its start."""
            entry = self.entry(slug)
            if entry is None:
                return None
            meeting = build_meeting(self.client, self.organization, entry)
            if meeting and start <= meeting.date <= end:
                found[meeting.id] = meeting
            return meeting_start(entry["meeting"])

        def step(slug: str, way: str) -> None:
            """Follow the pages' links to the next (or previous) meeting while they're in the window."""
            for _ in range(MAX_STEPS):
                entry = self.entry(slug)
                slug = entry and entry["meeting"].get(way)
                if not slug:
                    return
                at = take(_slug(slug))
                if at is None or not start <= at.date() <= end:
                    return

        for row in recent + soon:
            if start <= row.start.date() <= end and (
                    row.groups is None or any(_wanted(self.client, g.get("name")) for g in row.groups)):
                take(row.slug)
        if more and soon and soon[-1].start.date() <= end:
            step(soon[-1].slug, "next_slug")
        if len(recent) >= PAST_LISTED and recent[-1].start.date() >= start:
            step(recent[-1].slug, "previous_slug")
        return sorted(found.values(), key=lambda m: (m.date, m.time))


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Meetings from days_back days ago to days_ahead days ahead for each site.

    with_text and cache are unused: an agenda arrives with its items' text, and
    a few meetings a month are quick to read again each run.

    Returns the meetings and, for any site that failed, what went wrong.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        try:
            meetings += Site(fetcher, client).read(start, end)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as error:
            failures[client] = f"{type(error).__name__}: {error}"
            print(f"Boardable {client}: {failures[client]}", file=sys.stderr)
    return meetings, failures
