"""The shared data model: Source > Body > Meeting > Item > Attachment, plus Match.

Adapters build these from their platform's responses, so the matcher and the
alerts never see a platform's own field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Source:
    adapter: str  # "legistar"
    client: str  # the platform's name for the agency: Legistar client, IQM2 site...
    name: str  # what organizers call it: "San José"


@dataclass(frozen=True)
class Body:
    source: Source
    id: str
    name: str  # "City Council", "Rules and Open Government Committee"


@dataclass
class Attachment:
    id: str
    name: str
    url: str  # the link for people, in alerts
    modified: datetime | None = None  # UTC; lets a later run skip unchanged files
    # Where the run downloads the file, when that isn't url: Boardable hands out
    # S3 links that last 20 minutes, too short for an alert.
    download_url: str | None = None


@dataclass
class Item:
    id: str
    title: str
    text: str = ""  # item text beyond the title: agenda note, staff recommendation
    number: str = ""  # agenda number as printed, e.g. "6.1"
    # Legistar's MatterId stays the same while an item moves from committee to
    # council, so it's how an alert can say "this moved forward".
    matter_id: str | None = None
    matter_file: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    modified: datetime | None = None


@dataclass
class Meeting:
    body: Body
    id: str
    date: date
    time: str = ""
    agenda_url: str | None = None
    url: str | None = None  # the meeting's public web page
    items: list[Item] = field(default_factory=list)
    modified: datetime | None = None


# Where a match landed, best first.
LOCATIONS = ("title", "item_text", "attachment")


@dataclass
class Match:
    meeting: Meeting
    item: Item
    watchlist: str
    term: str
    location: str  # one of LOCATIONS
    score: int
    snippet: str
    page: int | None = None  # 1-based page, for attachment matches
    attachment: Attachment | None = None
    boilerplate: bool = False  # flagged for review instead of alerting
