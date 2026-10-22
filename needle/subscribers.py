"""Other organizers' alerts: the subscriber list, which matches each person gets,
and the keyed hashes that stand in for them in the public state file.

The list lives in the SUBSCRIBERS Actions secret (and in the Subscribers page's
copy on Drew's Mac: needle/signups.py), as JSON, one entry per person:

    [{"email": "someone@example.org", "watchlists": ["immigration"], "terms": ["Flock"],
      "custom": ["Measure A"], "places": ["San José"]}]

watchlists and terms come from watchlists.yaml. custom terms are the person's
own, matched for them alone. places are cities and agencies, named as the
adapters name them; an empty list means all of them. A person gets a match when
its place is one of theirs and it's in one of their watchlists, one of their
terms or one of their own terms.

The state file is public, so a person is known there only by an HMAC of their
address, keyed with the SUBSCRIBERS_KEY secret, and their own terms only by an
HMAC of the term. Nothing here prints or logs an address.

    python -m needle.subscribers import responses.csv | gh secret set SUBSCRIBERS
    python -m needle.subscribers key | gh secret set SUBSCRIBERS_KEY
    python -m needle.subscribers places      # the names the sign-up form should offer
"""

from __future__ import annotations

import argparse
import csv
import hmac
import json
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import yaml

from needle.matcher import Term, term_pattern

ROOT = Path(__file__).resolve().parents[1]
CUSTOM = "custom"  # the watchlist a person's own terms match under
SHOWN = "your terms"  # what their alerts call it
MIN_KEY = 32  # characters
MAX_TERM = 100  # characters in a person's own term
_EMAIL = re.compile(r"[^@\s<>,;\"']+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


class SubscriberError(Exception):
    pass


def fold(text) -> str:
    """Text to compare loosely: any case, no accents, hyphens as spaces, "&" as "and"
    ("San Jose" is "San José")."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.casefold().replace("-", " ").replace("_", " ").replace("&", " and ").split())


@dataclass(frozen=True, repr=False)
class Subscriber:
    number: int  # place in the list, for messages that mustn't name the address
    email: str
    watchlists: frozenset = frozenset()
    terms: frozenset = frozenset()  # as written in watchlists.yaml
    custom: tuple = ()  # (label, phrase) pairs; the label is a keyed hash of the phrase
    places: tuple = ()  # as given; empty means all

    def __repr__(self) -> str:  # keep the address out of tracebacks and logs
        return f"Subscriber({self.number})"

    def wants(self, watchlist: str, term: str, place: str) -> bool:
        if self.places and fold(place) not in {fold(p) for p in self.places}:
            return False
        if watchlist == CUSTOM:
            return any(term == label for label, _ in self.custom)
        return watchlist in self.watchlists or term in self.terms


class Roster:
    """The people on the list, and the key their hashes are made with."""

    def __init__(self, key: str):
        self._key = key.encode()
        self.people: list[Subscriber] = []

    def __repr__(self) -> str:
        return f"Roster({len(self.people)} people)"

    def _hmac(self, text: str) -> str:
        return hmac.new(self._key, text.encode(), sha256).hexdigest()

    def person(self, subscriber: Subscriber) -> str:
        """Who a state record belongs to, without saying who."""
        return self._hmac(f"person|{subscriber.email}")[:16]

    def label(self, phrase: str) -> str:
        """A person's own term as the state file names it."""
        return "#" + self._hmac(f"term|{' '.join(phrase.casefold().split())}")[:10]

    def phrases(self) -> dict[str, str]:
        return {label: phrase for s in self.people for label, phrase in s.custom}

    def terms(self) -> list[Term]:
        """Everyone's own terms, each once, to match with the watchlists."""
        return [Term(CUSTOM, label, re.compile(term_pattern(phrase), re.IGNORECASE))
                for label, phrase in self.phrases().items()]


def known_terms(watchlists: dict) -> dict[str, str]:
    return {fold(t): str(t) for w in watchlists.values() for t in (w or {}).get("terms") or []}


def load(raw: str | None, key: str | None, watchlists: dict, owner: str | None = None,
         warn=lambda line: print(line, file=sys.stderr)) -> Roster | None:
    """The roster from the SUBSCRIBERS and SUBSCRIBERS_KEY secrets, or None if
    there's no list. owner, the ALERT_TO address, gets everything already."""
    if not (raw or "").strip():
        return None
    if len((key or "").strip()) < MIN_KEY:
        raise SubscriberError(
            "The SUBSCRIBERS secret is set but SUBSCRIBERS_KEY isn't (or is too short). Make one with "
            "python -m needle.subscribers key | gh secret set SUBSCRIBERS_KEY")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as error:  # its message quotes no text from the secret
        raise SubscriberError(f"The SUBSCRIBERS secret isn't valid JSON (line {error.lineno}, column "
                              f"{error.colno}). Make it again with python -m needle.subscribers import.") from None
    if not isinstance(entries, list):
        raise SubscriberError("The SUBSCRIBERS secret should be a list. Make it again with "
                              "python -m needle.subscribers import.")
    roster = Roster(key.strip())
    terms = known_terms(watchlists)
    owner = (owner or "").strip().lower()
    seen = set()
    for number, entry in enumerate(entries, start=1):
        entry = entry if isinstance(entry, dict) else {}
        email = str(entry.get("email") or "").strip().lower()
        if not _EMAIL.fullmatch(email):
            warn(f"Subscriber {number} has no usable email address; left out.")
            continue
        if email == owner or email in seen:
            continue  # the owner gets everything already
        seen.add(email)
        picked = {str(w) for w in entry.get("watchlists") or []}
        if picked - set(watchlists):
            warn(f"Subscriber {number} picked {', '.join(sorted(picked - set(watchlists)))}, "
                 "not a watchlist now; left out.")
        chosen = {terms[fold(t)] for t in entry.get("terms") or [] if fold(t) in terms}
        custom = {}
        for phrase in entry.get("custom") or []:
            phrase = " ".join(str(phrase).split())
            if fold(phrase) in terms:
                chosen.add(terms[fold(phrase)])
            elif phrase:
                custom.setdefault(roster.label(phrase), phrase)
        subscriber = Subscriber(number, email, frozenset(picked & set(watchlists)), frozenset(chosen),
                                tuple(custom.items()), tuple(str(p) for p in entry.get("places") or []))
        if not (subscriber.watchlists or subscriber.terms or subscriber.custom):
            warn(f"Subscriber {number} picked nothing the app can match; left out.")
            continue
        roster.people.append(subscriber)
    return roster


# Turning the sign-up form's answers into the list


COLUMNS = (  # field, the words in a question's title that name it; checked in this order
    # Whole words, since a title can explain itself: "Pick whole categories. Matches
    # for surveillance are emailed right away" is the categories question.
    ("email", re.compile(r"\be-?mail\b")),
    ("custom", re.compile(r"\b(?:new|own|other) terms?\b")),
    ("terms", re.compile(r"\bterms?\b")),
    ("watchlists", re.compile(r"\b(?:categor|watchlist|topic)")),
    ("places", re.compile(r"\b(?:cit(?:y|ies)|agenc|place)")),
)


def columns(header: list[str]) -> dict[str, int]:
    found = {}
    for index, name in enumerate(header):
        for field, words in COLUMNS:
            if words.search(name.lower()):
                found.setdefault(field, index)
                break
    return found


def values(cell: str) -> list[str]:
    """A cell's answers: a form exports several picks in one cell."""
    return [v.strip() for v in re.split(r"[;\n|]+|,\s*", cell or "") if v.strip()]


def place_names() -> list[str]:
    from needle.run import ADAPTERS  # here, so the run can import this module
    return sorted({s.name for a in ADAPTERS.values() for s in a.SOURCES.values()}, key=fold)


def read_form(rows: list[list[str]], watchlists: dict, places: list[str]) -> tuple[list[dict], list[str]]:
    """The subscriber list from a CryptPad form export (header row first), and notes
    on what was left out. The notes name rows, never addresses."""
    if not rows:
        return [], ["The file is empty."]
    found = columns(rows[0])
    if "email" not in found:
        raise SubscriberError(f"No email column among: {', '.join(rows[0])}")
    names = {fold(n): n for n in watchlists}
    terms = known_terms(watchlists)
    place_by = {fold(p): p for p in places}
    notes, by_email = [], {}

    def cell(row, field):
        index = found.get(field)
        return row[index] if index is not None and index < len(row) else ""

    for number, row in enumerate(rows[1:], start=2):
        email = cell(row, "email").strip().lower()
        if not _EMAIL.fullmatch(email):
            if any(c.strip() for c in row):
                notes.append(f"Row {number}: no usable email address; left out.")
            continue
        entry = {"email": email, "watchlists": [], "terms": [], "custom": [], "places": []}
        for value in values(cell(row, "watchlists")):
            if fold(value) in names:
                entry["watchlists"].append(names[fold(value)])
            else:
                notes.append(f'Row {number}: "{value}" isn\'t a watchlist; left out.')
        for value in values(cell(row, "terms")) + values(cell(row, "custom")):
            value = " ".join(value.replace("|", " ").split())[:MAX_TERM]
            if fold(value) in terms:
                entry["terms"].append(terms[fold(value)])
            elif value:
                entry["custom"].append(value)
        for value in values(cell(row, "places")):
            if fold(value).startswith("all"):
                entry["places"] = []
                break
            if fold(value) in place_by:
                entry["places"].append(place_by[fold(value)])
            else:
                notes.append(f'Row {number}: "{value}" isn\'t a city or agency the app reads; left out.')
        for field in ("watchlists", "terms", "custom", "places"):
            entry[field] = list(dict.fromkeys(entry[field]))
        if not (entry["watchlists"] or entry["terms"] or entry["custom"]):
            notes.append(f"Row {number}: picked no watchlist or term; left out.")
            continue
        if email in by_email:
            notes.append(f"Row {number} replaces row {by_email[email][0]}, from the same address.")
        by_email[email] = (number, entry)
    return [entry for _, entry in by_email.values()], notes


def _write(text: str, out: Path | None, what: str) -> None:
    if out:
        if out.resolve().is_relative_to(ROOT):
            sys.exit(f"Write the {what} outside the repo, so it's never committed.")
        out.write_text(text)
    elif sys.stdout.isatty():
        sys.exit(f"This would show the {what} on screen. Send it straight to the secret instead:\n"
                 f"  python -m needle.subscribers {'import FILE' if what == 'subscriber list' else 'key'} "
                 f"| gh secret set {'SUBSCRIBERS' if what == 'subscriber list' else 'SUBSCRIBERS_KEY'}")
    else:
        sys.stdout.write(text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    form = commands.add_parser("import", help="turn the form's CSV export into the SUBSCRIBERS secret")
    form.add_argument("csv", type=Path, help="the export, with only approved rows")
    form.add_argument("--out", type=Path, help="write to this file (outside the repo) instead of a pipe")
    key = commands.add_parser("key", help="make a SUBSCRIBERS_KEY")
    key.add_argument("--out", type=Path, help="write to this file (outside the repo) instead of a pipe")
    commands.add_parser("places", help="list the cities and agencies the app reads")
    args = parser.parse_args(argv)

    if args.command == "places":
        print("\n".join(place_names()))
    elif args.command == "key":
        _write(secrets.token_hex(32), args.out, "key")
    else:
        watchlists = yaml.safe_load((ROOT / "watchlists.yaml").read_text())
        with args.csv.open(newline="", encoding="utf-8-sig") as file:
            rows = list(csv.reader(file))
        try:
            entries, notes = read_form(rows, watchlists, place_names())
        except SubscriberError as error:
            sys.exit(str(error))
        _write(json.dumps(entries, ensure_ascii=False, separators=(",", ":")), args.out, "subscriber list")
        print(f"{len(entries)} subscriber{'s' if len(entries) != 1 else ''} from {max(len(rows) - 1, 0)} rows.",
              file=sys.stderr)
        for note in notes:
            print(note, file=sys.stderr)
        own = sorted({t for e in entries for t in e["custom"]}, key=fold)
        if own:
            print("Their own terms, to consider for watchlists.yaml: " + "; ".join(own), file=sys.stderr)


if __name__ == "__main__":
    main()
