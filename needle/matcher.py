"""Match agenda items against watchlists.yaml.

The rules (docs/scope.md, Matching):
- Whole words only, any case, except terms listed under case_sensitive, which
  match only in the case written.
- A term's last word may be plural: "license plate reader" matches "Readers".
- A hyphen, with or without spaces around it, counts as a space:
  "Los Gatos-Monte Sereno" matches "Los Gatos - Monte Sereno" and "Los Gatos Monte Sereno".
- Score by where the match landed: title > item text > attachment, and lower
  again deep in a long packet.
- A term that matches in more than half of a body's agendas is flagged as
  boilerplate, for review instead of an alert.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import yaml

from needle.models import Attachment, Item, Match, Meeting

SCORES = {"title": 30, "item_text": 20, "attachment": 10}
DEEP_PAGE = 10  # an attachment match past this page is digest-only
DEEP_SCORE = 5
SNIPPET_CHARS = 80  # of context on each side of a match

# Hyphen, Unicode hyphen, non-breaking hyphen and en dash: PDF text uses all four.
DASHES = "-‐‑–"
_SPLIT = re.compile(rf"\s*[{DASHES}]\s*|\s+")
_SEPARATOR = rf"(?:\s*[{DASHES}]\s*|\s+)"


@dataclass(frozen=True)
class Term:
    watchlist: str
    text: str
    pattern: re.Pattern


def _word_pattern(word: str, last: bool) -> str:
    if not last or not word[-1:].isalpha():
        return re.escape(word)
    if len(word) > 2 and word[-1] in "yY" and word[-2].lower() not in "aeiou":
        return re.escape(word[:-1]) + "(?:y|ies)"  # technology, technologies
    return re.escape(word) + "(?:e?s)?"  # reader(s), sheriff(s)


def term_pattern(term: str) -> str:
    words = [w for w in _SPLIT.split(term.strip()) if w]
    body = _SEPARATOR.join(_word_pattern(w, i == len(words) - 1) for i, w in enumerate(words))
    # Lookarounds instead of \b, so a term ending in ")" like "287(g)" still works.
    return rf"(?<!\w){body}(?!\w)"


def compile_terms(watchlists: dict) -> list[Term]:
    terms = []
    for name, watchlist in watchlists.items():
        sensitive = {str(t) for t in watchlist.get("case_sensitive") or []}
        for text in watchlist.get("terms") or []:
            text = str(text)
            flags = 0 if text in sensitive else re.IGNORECASE
            terms.append(Term(name, text, re.compile(term_pattern(text), flags)))
    return terms


def load_terms(path: Path) -> list[Term]:
    return compile_terms(yaml.safe_load(Path(path).read_text()))


def snippet(text: str, start: int, end: int, chars: int = SNIPPET_CHARS) -> str:
    left = max(0, start - chars)
    right = min(len(text), end + chars)
    out = " ".join(text[left:right].split())
    return ("…" if left else "") + out + ("…" if right < len(text) else "")


PageReader = Callable[[Attachment], list[str]]


def match_item(meeting: Meeting, item: Item, terms: Iterable[Term],
               read_pages: PageReader | None = None, best_only: bool = True) -> list[Match]:
    """Match one item's title, item text and (given read_pages) attachments.

    best_only gives one Match per term, where it scored highest. Otherwise every
    location a term landed in is listed, first hit per location, which is what
    the backtest reports.
    """
    terms = list(terms)
    found: dict[tuple[str, str, str], Match] = {}

    def look(location, text, page=None, attachment=None):
        for term in terms:
            key = (term.watchlist, term.text, location)
            if key in found:
                continue
            m = term.pattern.search(text)
            if m:
                score = SCORES[location]
                if page is not None and page > DEEP_PAGE:
                    score = DEEP_SCORE
                found[key] = Match(meeting, item, term.watchlist, term.text, location, score,
                                   snippet(text, m.start(), m.end()), page, attachment)

    look("title", item.title)
    look("item_text", item.text)
    if read_pages:
        for attachment in item.attachments:
            for number, page_text in enumerate(read_pages(attachment), start=1):
                look("attachment", page_text, number, attachment)

    matches = sorted(found.values(), key=lambda m: -m.score)
    if not best_only:
        return matches
    best: dict[tuple[str, str], Match] = {}
    for m in matches:
        best.setdefault((m.watchlist, m.term), m)
    return list(best.values())


def match_meeting(meeting: Meeting, terms: Iterable[Term],
                  read_pages: PageReader | None = None) -> list[Match]:
    terms = list(terms)
    return [m for item in meeting.items for m in match_item(meeting, item, terms, read_pages)]


def flag_boilerplate(meetings: list[Meeting], matches: list[Match], min_meetings: int = 4) -> None:
    """Flag matches whose term matched in more than half of the body's meetings.

    Needs at least min_meetings meetings of a body to judge; with fewer, nothing
    is flagged. Sets Match.boilerplate in place.
    """
    meetings_by_body = defaultdict(set)
    for meeting in meetings:
        meetings_by_body[meeting.body].add(meeting.id)
    hits = defaultdict(set)  # (body, watchlist, term) -> meeting ids
    for m in matches:
        hits[(m.meeting.body, m.watchlist, m.term)].add(m.meeting.id)
    for m in matches:
        total = len(meetings_by_body[m.meeting.body])
        count = len(hits[(m.meeting.body, m.watchlist, m.term)])
        m.boilerplate = total >= min_meetings and count * 2 > total
