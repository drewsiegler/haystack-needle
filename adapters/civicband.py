"""Civic Band adapter: agendas Civic Band has collected, for a city whose own agenda site turns bots away.

Palo Alto posts its agendas on PrimeGov, whose robots.txt says no to every bot
but search engines, and its agenda emails only say an agenda is up. Civic Band
(civic.band) collects agendas and minutes from about 1,200 municipalities,
Palo Alto's among them, and publishes each city as a Datasette site with a JSON
API. Its robots.txt shuts out a list of named AI and SEO crawlers and has no
rule for other bots. Drew chose to read Palo Alto's agendas from it (Oct 16,
2026), knowing Civic Band collects them from PrimeGov.

- {city}.ca.civic.band/meetings/agendas.json lists agenda pages: the body
  ("CityCouncil", "ClimateActionandSustainabilityCommittee"), the meeting date,
  the page number and the page's text. One request covers the window; Datasette
  hands out a "next" token when there's more.
- Each body's agenda for a date becomes a meeting with one item, "Agenda", whose
  attachment carries the pages' text, so a match says which page it's on. Civic
  Band has the agenda only, not staff reports.
- The alert links the agenda's pages on Civic Band and the city's own portal.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from needle.fetch import Fetcher
from needle.models import Attachment, Body, Item, Meeting, Source

DAYS_BACK = 2
DAYS_AHEAD = 30
MAX_REQUESTS = 10  # pages of results a run follows; a month of agendas fits in one
TEXT_TTL = None  # no item text: the pages are read as an attachment
PACIFIC = ZoneInfo("America/Los_Angeles")

SOURCES = {"paloalto": Source("civicband", "paloalto", "Palo Alto")}
# The city's own agenda portal, for people: alerts and health notices link it.
PORTALS = {"paloalto": "https://cityofpaloalto.primegov.com/public/portal"}


def site(client: str) -> str:
    return f"https://{client}.ca.civic.band"


def body_name(meeting: str) -> str:
    """Civic Band runs a body's name together: "ClimateActionandSustainabilityCommittee",
    "PolicyAndamp;ServicesCommittee" (an "&" escaped twice). Spaced out again."""
    name = meeting.replace("Andamp;", "And").replace("&amp;", "&")
    name = re.sub(r"(?<=[a-z])(and|of|for)(?=[A-Z])", r" \1 ", name)
    name = re.sub(r"(?<=[a-z'])(?=[A-Z])", " ", name)
    return re.sub(r"(?<=\s)(And|Of|For|The)(?=\s)", lambda m: m[0].lower(), name)


def collect(fetcher: Fetcher, clients=tuple(SOURCES), today: date | None = None,
            with_text: bool = True, days_back: int = DAYS_BACK, days_ahead: int = DAYS_AHEAD,
            cache: dict | None = None) -> tuple[list[Meeting], dict[str, str]]:
    """Agendas from days_back days ago to days_ahead days ahead.

    with_text and cache are unused: the pages' text comes with them.

    Returns the meetings and, for each city that failed, what went wrong.
    """
    today = today or datetime.now(PACIFIC).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    meetings: list[Meeting] = []
    failures: dict[str, str] = {}
    for client in clients:
        params = {"date__gte": start.isoformat(), "date__lte": end.isoformat(),
                  "_col": ["meeting", "date", "page", "text"], "_shape": "objects", "_size": "max"}
        pages = defaultdict(dict)  # (meeting, date) -> {page number: text}
        try:
            for _ in range(MAX_REQUESTS):
                answer = fetcher.get_json(f"{site(client)}/meetings/agendas.json", params)
                for row in answer["rows"]:
                    pages[(row["meeting"], row["date"])][int(row["page"])] = row["text"] or ""
                if not answer.get("next"):
                    break
                params["_next"] = answer["next"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            failures[client] = f"Couldn't read Civic Band: {type(error).__name__}"
            continue
        for (meeting, day), by_number in pages.items():
            meetings.append(_meeting(client, meeting, date.fromisoformat(day),
                                     [by_number[n] for n in sorted(by_number)]))
    return meetings, failures


def _meeting(client: str, meeting: str, day: date, pages: list[str]) -> Meeting:
    name = body_name(meeting)
    body = Body(SOURCES[client], re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), name)
    ident = f"{day.isoformat()}-{body.id}"
    link = f"{site(client)}/meetings/agendas?" + urlencode({"meeting": meeting, "date": day.isoformat()})
    agenda = Attachment(ident, "Agenda (Civic Band)", link, pages=pages)
    return Meeting(body, ident, day, url=PORTALS[client], items=[Item(ident, "Agenda", attachments=[agenda])])


def item_text(fetcher: Fetcher, client: str, item: Item) -> str:
    return ""


def calendar_url(client: str) -> str:
    return PORTALS[client]
