"""One run: read agendas, match them, send alerts, check health, save the state.

    python -m needle.run --state data/state.json   # what the workflow runs
    python -m needle.run --dry-run                 # print what would be sent; send and save nothing
    python -m needle.run --dry-run --only iqm2     # the same, for one adapter
    python -m needle.run --test-email              # send one sample alert to check the mail setup

The workflow runs hourly. Every 2 hours the run reads every meeting from 2
days back to 30 days ahead; in the hours between, it re-reads only meetings
from 72 hours out through meeting day, to catch amended agendas and late memos.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from adapters import (agendamail, boardable, civicband, civicclerk, civicplus, escribe, icboard, iqm2,
                      legistar, midpen)
from needle import alerts, health, mailer, records, subscribers, votes
from needle import state as statefile
from needle.fetch import Deferred, Fetcher
from needle.matcher import DEEP_SCORE, compile_terms, match_meeting, snippet
from needle.models import Attachment, Body, Item, Match, Meeting, Source
from needle.pdftext import pdf_pages

ROOT = Path(__file__).resolve().parents[1]
WATCHLISTS = ROOT / "watchlists.yaml"
DEFAULT_STATE = ROOT / ".state" / "state.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
# School boards are back since Oct 27, 2026 (docs/scope.md): adapters/boardable.py reads
# Foothill-De Anza CCD and adapters/icboard.py Mountain View-Los Altos HSD.
ADAPTERS = {"legistar": legistar, "iqm2": iqm2, "civicclerk": civicclerk, "civicplus": civicplus,
            "escribe": escribe, "midpen": midpen, "agendamail": agendamail,
            "civicband": civicband, "boardable": boardable, "icboard": icboard}
FULL_EVERY = timedelta(minutes=110)  # a full run every 2 hours
NEAR_DAYS = 3  # between full runs, re-check meetings from 72 hours out through meeting day
TEXT_TTL = timedelta(hours=24)  # re-read item text at least daily, in case it changed quietly
DIGEST_HOUR = 2  # Pacific: the first run after 2 a.m. sends the day's digest (usually by 7; see the workflow)
MAX_DOWNLOADS = 150  # PDFs per run; the rest wait for the next run, soonest meetings first
BUDGET = timedelta(minutes=40)  # then stop reading meetings, well inside the job's 55-minute limit
# Slow hosts (IQM2 asks for a minute between requests) may hold the run up only
# this long; after that, a request that would wait is left for the next run.
# Requests whose minute has passed while the run did other work still go out.
SLOW_BUDGET = timedelta(minutes=25)
AGENDA_PAGES = 40  # how far into an agenda to look for the public comment notice
MIN_AGENDAS = 4  # a body's agendas needed before a phrase can be judged boilerplate
# Proton doesn't publish its paid plans' sending limits (they move with the account's
# reputation); its free plan's are 50 an hour and 150 a day. These stay under those.
# The owner's own email always goes; subscribers' past the cap waits for the next run.
SUBSCRIBER_SENDS = 20  # emails to subscribers per run
DAY_SENDS = 140  # emails a day, Pacific, the owner's included
NEW_TERMS_EVERY = timedelta(days=7)  # how often the owner's digest lists subscribers' new terms


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def iso(value) -> str | None:
    return value.isoformat() if value else None


def loose(name: str) -> tuple[str, ...]:
    """A body's name to compare loosely: Civic Band says "City Schools Liaison
    Committee" where Palo Alto's own email says "City School Liaison Committee"."""
    return tuple(w.rstrip("s") for w in re.findall(r"[a-z]+", name.lower()))


def key(source: Source, kind: str, ident: str) -> str:
    return f"{source.adapter}/{source.client}/{kind}/{ident}"


def first_hits(pages: list[str], terms) -> dict[str, list[str]]:
    """For each term, a snippet around its first match: {page number: [snippets]}.

    Matching the stored snippets again gives the same matches the full text
    did, so an unchanged document never has to be fetched again.
    """
    hits = defaultdict(list)
    for term in terms:
        for number, text in enumerate(pages, start=1):
            found = term.pattern.search(text)
            if found:
                hits[str(number)].append(snippet(text, found.start(), found.end()))
                break
    return dict(hits)


def cached_pages(entry: dict) -> list[str]:
    pages = [""] * entry["pages"]
    for number, snippets in entry["hits"].items():
        pages[int(number) - 1] = "\n\n".join(snippets)
    return pages


class Reader:
    """Item text and attachment pages, fetched only when new or changed."""

    def __init__(self, fetcher: Fetcher, state: dict, terms, now: datetime, max_downloads: int):
        self.fetcher, self.state, self.terms, self.now = fetcher, state, terms, now
        self.today = now.astimezone(PACIFIC).date().isoformat()
        self.max_downloads = max_downloads
        self.downloads = self.deferred = self.texts_read = 0

    def fill_text(self, adapter, meeting: Meeting, item: Item) -> None:
        """Set item.text to the agenda note plus the matter text, or to the snippets
        that matched last time if the item hasn't changed and was read in the last day."""
        k = key(meeting.body.source, "item", item.id)
        entry = self.state["items"].get(k)
        ttl = getattr(adapter, "TEXT_TTL", TEXT_TTL)  # None: read once
        if (entry and entry["modified"] == iso(item.modified)
                and (ttl is None or self.now - datetime.fromisoformat(entry["read"]) < ttl)):
            item.text = "\n\n".join(entry["hits"])
            entry["date"] = iso(meeting.date)
            return
        try:
            text = adapter.item_text(self.fetcher, meeting.body.source.client, item)
        except Deferred:
            return  # a slow host's time is up; match the agenda note alone and read it next run
        except httpx.HTTPError as error:
            print(f"Couldn't read the text of item {item.id}: {type(error).__name__}", file=sys.stderr)
            return  # match the agenda note alone; try the text again next run
        self.texts_read += 1
        item.text = "\n\n".join(t for t in (item.text, text) if t)
        self.state["items"][k] = {"date": iso(meeting.date), "modified": iso(item.modified),
                                  "read": iso(self.now), "hits": first_hits([item.text], self.terms).get("1", [])}

    def _download(self, url: str, shown: str | None = None) -> bytes | None:
        try:
            data = self.fetcher.get(url)
        except Deferred:
            return None  # a slow host's time is up; try next run
        except httpx.HTTPError as error:
            self.downloads += 1
            print(f"Couldn't download {shown or url}: {type(error).__name__}", file=sys.stderr)
            return None
        self.downloads += 1
        return data

    def pages(self, source: Source, attachment: Attachment) -> list[str]:
        k = key(source, "attachment", attachment.id)
        entry = self.state["attachments"].get(k)
        if attachment.pages is None and entry and entry["modified"] == iso(attachment.modified):
            entry["seen"] = self.today
            return cached_pages(entry)
        # Text the adapter already has is new if it has changed, whatever its date says.
        data = "\f".join(attachment.pages).encode() if attachment.pages is not None else attachment.data
        if data is None:
            url = alerts.safe_url(attachment.download_url or attachment.url)
            if not url:
                return []
            if self.downloads >= self.max_downloads:
                self.deferred += 1
                return cached_pages(entry) if entry else []
            data = self._download(url, attachment.url)  # log the lasting link, not a signed one
            if data is None:
                return cached_pages(entry) if entry else []
        digest = sha(data)
        if entry and entry["sha256"] == digest:
            entry.update(seen=self.today, modified=iso(attachment.modified))
            return cached_pages(entry)
        pages = attachment.pages if attachment.pages is not None else pdf_pages(data)
        self.state["attachments"][k] = {"seen": self.today, "modified": iso(attachment.modified),
                                        "sha256": digest, "pages": len(pages),
                                        "hits": first_hits(pages, self.terms)}
        return pages

    def comment(self, meeting: Meeting) -> tuple[str, str] | None:
        """The agenda's public comment notice, read once per agenda version."""
        k = key(meeting.body.source, "meeting", meeting.id)
        entry = self.state["meetings"].get(k)
        if not entry or entry["modified"] != iso(meeting.modified):
            url = alerts.safe_url(meeting.agenda_url)
            data = self._download(url) if url else None
            if data is None and url:
                return None  # try again next run
            info = alerts.comment_info(pdf_pages(data, AGENDA_PAGES)) if data else None
            entry = self.state["meetings"][k] = {
                "date": iso(meeting.date), "modified": iso(meeting.modified),
                "comment": info[0] if info else "", "comment_email": info[1] if info else ""}
        return (entry["comment"], entry["comment_email"]) if entry["comment"] else None


def full_run_due(state: dict, now: datetime) -> bool:
    last = state["last_full_run"]
    return last is None or now - datetime.fromisoformat(last) >= FULL_EVERY


def record_agenda(state: dict, meeting: Meeting, matches: list[Match]) -> None:
    """Remember which terms matched each agenda, to spot boilerplate across meetings."""
    if not meeting.items:
        return  # not posted yet
    body = state["bodies"][key(meeting.body.source, "body", meeting.body.id)]
    body["agendas"][meeting.id] = {"date": iso(meeting.date),
                                   "hits": sorted({f"{m.watchlist}|{m.term}" for m in matches})}


def flag_boilerplate(state: dict, matches: list[Match]) -> None:
    """Hold back a phrase that's on more than half of a body's agendas, and note it for the digest."""
    for m in matches:
        body_key = key(m.meeting.body.source, "body", m.meeting.body.id)
        agendas = state["bodies"][body_key]["agendas"].values()
        hit = f"{m.watchlist}|{m.term}"
        count = sum(hit in a["hits"] for a in agendas)
        m.boilerplate = len(agendas) >= MIN_AGENDAS and count * 2 > len(agendas)
        note_key = f"{body_key}|{hit}"
        if m.boilerplate and note_key not in state["boilerplate"]:
            name = f"{m.meeting.body.source.name} {m.meeting.body.name}"
            # The state is public: a subscriber's own term goes unnamed.
            shown = "A subscriber's own term" if m.watchlist == subscribers.CUSTOM else f'"{m.term}" ({m.watchlist})'
            state["boilerplate"][note_key] = {
                "note": f'{shown} is on {count} of {len(agendas)} {name} agendas',
                "reported": False}


def prior_history(adapter, fetcher, state: dict, meeting: Meeting, item: Item, thread: str) -> list[tuple[date, str, str]]:
    """Earlier stops for this item: (date, body, action), from the platform or, failing that, past alerts."""
    rows = []
    if item.matter_id and hasattr(adapter, "matter_history"):
        try:
            rows = adapter.matter_history(fetcher, meeting.body.source.client, item.matter_id)
        except httpx.HTTPError as error:
            print(f"Couldn't read the history of matter {item.matter_id}: {type(error).__name__}", file=sys.stderr)
    rows = [r for r in rows if r[0] < meeting.date]
    if not rows:
        rows = sorted((date.fromisoformat(a["date"]), a["body_name"], "alerted")
                      for a in state["alerts"].values()
                      if a["thread"] == thread and a["date"] < iso(meeting.date))
    return rows


def sig(m: Match) -> str:
    return f"{m.watchlist}|{m.term}|{m.location}|{m.attachment.id if m.attachment else ''}"


def priority_of(matches: list[Match], priorities: dict) -> str:
    return "high" if any(priorities.get(m.watchlist) == "high" and m.score > DEEP_SCORE for m in matches) else "normal"


def plan_alerts(by_item: list[tuple[Meeting, Item, list[Match]]], adapters: dict, fetcher,
                reader: Reader, state: dict, priorities: dict, today: date, now: datetime) -> list[dict]:
    """Items with a match that's new since their last alert, one per item per meeting,
    with what alert_for needs to build each recipient's alert. Records each in the state."""
    planned = []
    for meeting, item, matches in by_item:
        live = [m for m in matches if not m.boilerplate]
        if not live or meeting.date < today:
            continue  # nothing to say, or the meeting is over
        source = meeting.body.source
        thread = key(source, "matter", item.matter_id) if item.matter_id else key(source, "item", item.id)
        stage = f"{thread}@{meeting.id}"
        sigs = sorted({sig(m) for m in live})
        previous = state["alerts"].get(stage)
        if previous and set(sigs) <= set(previous["sigs"]):
            continue
        adapter = adapters[source.adapter]
        history = prior_history(adapter, fetcher, state, meeting, item, thread)
        earlier = [r for r in history if r[1] and r[1] != meeting.body.name]
        plan = {
            "meeting": meeting, "live": live, "id": sha(stage + "|" + "|".join(sigs))[:10],
            "before": set(previous["sigs"]) if previous else set(),
            "sent": previous["sent"] if previous else None,
            "moved": (f"from {earlier[-1][1]} ({alerts.day_text(earlier[-1][0])}, {earlier[-1][0].year})"
                      if earlier else ""),
            "history": [f"{alerts.day_text(d)}, {d.year}: {body}" + (f", {action}" if action else "")
                        for d, body, action in history],
            "comment": reader.comment(meeting)}
        state["alerts"][stage] = {
            "id": plan["id"], "thread": thread, "body": key(source, "body", meeting.body.id),
            "body_name": meeting.body.name, "date": iso(meeting.date), "priority": priority_of(live, priorities),
            "sigs": sorted(set(sigs) | plan["before"]), "sent": iso(now)}
        if previous:  # votes on the alert before the update still count, once per person
            state["alerts"][stage]["earlier_ids"] = previous.get("earlier_ids", []) + [previous["id"]]
            if previous.get("voters"):
                state["alerts"][stage]["voters"] = previous["voters"]
        planned.append(plan)
    return planned


def owner_wants(watchlist: str, term: str, place: str) -> bool:
    return watchlist != subscribers.CUSTOM  # everything on the watchlists; subscribers' own terms are theirs


def alert_for(plan: dict, wants, priorities: dict, subscriber: bool = False) -> dict | None:
    """One recipient's alert for a planned item: only the matches they want, or None
    if none of those is new to them. wants(watchlist, term, place) says who they
    are. A subscriber's own terms keep their labels, since the alert may wait in
    the public state for their digest; spelled() puts the words back to send it."""
    place = plan["meeting"].body.source.name
    mine = [m for m in plan["live"] if wants(m.watchlist, m.term, place)]
    before = {s for s in plan["before"] if wants(*s.split("|", 2)[:2], place)}
    if not mine or {sig(m) for m in mine} <= before:
        return None
    label = detail = ""
    if before:
        label, detail = "Updated", f"a new match since the alert on {plan['sent'][:10]}"
    elif plan["moved"]:
        label, detail = "This moved forward", plan["moved"]
    priority = priority_of(mine, priorities)
    if subscriber:
        mine = [replace(m, watchlist=subscribers.SHOWN) if m.watchlist == subscribers.CUSTOM else m for m in mine]
    alert = alerts.build(plan["meeting"], mine, plan["id"], priority, label, detail, plan["history"], plan["comment"])
    if subscriber:  # votes tune the watchlists, so a person's own terms get none
        on_list = [m for m in mine if m.watchlist != subscribers.SHOWN]
        alert["votes"] = alerts.vote_links(plan["id"], on_list[0].watchlist, on_list[0].term) if on_list else None
    return alert


def spelled(alert: dict, phrases: dict) -> dict:
    """A subscriber's alert with their own terms in words, to send."""
    return {**alert, "matches": [{**m, "term": phrases.get(m["term"], m["term"])}
                                 if m["watchlist"] == subscribers.SHOWN else m for m in alert["matches"]]}


def serve_subscribers(roster, state: dict, outbox: Outbox, plans: list[dict], priorities: dict,
                      today: date, digest_due: bool) -> None:
    """Each subscriber's own email, one message per person: a note the first time,
    then alerts for what they picked, right away or in their own morning digest.
    Past the send cap, or if the mail server won't take it, an email waits for the
    next run, and the owner's digest says so."""
    day = iso(today)
    records, sends = state["subscribers"], state["sends"]
    people = {roster.person(p): p for p in roster.people}
    for gone in set(records) - set(people):
        del records[gone]  # off the list: forget them
    phrases = roster.phrases()

    def send(person, message, on_fail) -> bool:
        if outbox.room <= 0:
            sends["held"] += 1
            return False

        def failed(reason):
            sends["held"] += 1
            if reason.startswith("refused") and person.number not in sends["refused"]:
                sends["refused"].append(person.number)
            on_fail()

        outbox.send(*message, to=person.email, on_fail=failed)
        return True

    for k, person in people.items():
        record = records.setdefault(k, {"welcomed": None, "held": [], "digest": [], "digest_sent": None})
        now = [a for a in record["held"] if a["date"] >= day]  # held back last run; the meeting's not over
        record["held"] = []
        for plan in plans:
            alert = alert_for(plan, person.wants, priorities, subscriber=True)
            if alert:
                (now if alert["priority"] == "high" else record["digest"]).append(alert)
        if not record["welcomed"]:
            if not send(person, alerts.welcome_email(person.watchlists, person.terms, [p for _, p in person.custom],
                                                     list(person.places), priorities),
                        lambda r=record: r.update(welcomed=None)):
                record["held"] = now
                continue
            record["welcomed"] = day
        for alert in now:
            if not send(person, alerts.alert_email(spelled(alert, phrases), subscriber=True),
                        lambda r=record, a=alert: r["held"].append(a)):
                record["held"].append(alert)
        if digest_due and record["digest_sent"] != day:
            waiting = record["digest"]
            if not waiting:
                record["digest_sent"] = day
            elif send(person, alerts.digest_email([spelled(a, phrases) for a in waiting], [], today, subscriber=True),
                      lambda r=record, w=waiting: r.update(digest=w + r["digest"], digest_sent=None)):
                record["digest"], record["digest_sent"] = [], day


class Outbox:
    """Sends email, or in a dry run prints it. Never prints an address.

    Messages wait until flush(), which sends them all over one connection, one
    message per recipient. A subscriber's message that doesn't go calls its
    on_fail with the reason, so it can wait for the next run; one to the owner
    that doesn't go fails the run.
    """

    def __init__(self, dry_run: bool, settings: mailer.Settings | None = None, send=mailer.send):
        self.dry_run, self.settings, self._send = dry_run, settings, send
        self.sent: list[str] = []
        self.room = float("inf")  # emails to subscribers still allowed this run (the send cap)
        self.delivered = 0
        self._queue: list[tuple] = []

    def send(self, subject: str, text: str, html: str | None = None, to: str | None = None,
             on_fail=None) -> None:
        """to: a subscriber's address; None sends to the owner (ALERT_TO)."""
        if to:
            self.room -= 1
        self.sent.append(subject)
        if self.dry_run:
            print("=" * 72 + f"\n{'To: a subscriber' + chr(10) if to else ''}Subject: {subject}\n\n{text}")
        else:
            self._queue.append((mailer.compose(subject, text, html, to or self.settings.to), bool(to), on_fail))

    def flush(self) -> None:
        queue, self._queue = self._queue, []
        if not queue:
            return
        results = self._send([message for message, _, _ in queue], self.settings)
        theirs, missed, mine = 0, 0, []
        for (message, subscriber, on_fail), error in zip(queue, results):
            if error is None:
                self.delivered += 1
                if subscriber:
                    theirs += 1
                else:
                    print(f"Sent: {message['Subject']}")
            elif subscriber:
                missed += 1
                if on_fail:
                    on_fail(error)
            else:
                mine.append(error)
        if theirs or missed:  # a count only: subscribers' subjects say what they follow
            print(f"Sent {theirs} email{'s' if theirs != 1 else ''} to subscribers"
                  + (f"; {missed} didn't go and will be tried again." if missed else "."))
        if mine:
            raise mailer.MailError(f"Didn't send {len(mine)} email{'s' if len(mine) != 1 else ''} "
                                   f"to you: {mine[0]}.")


def new_custom_terms(state: dict, roster, today: date) -> list[str]:
    """Once a week, the terms subscribers added themselves since the last list, for
    the owner to consider for watchlists.yaml. The state keeps only their labels."""
    listed = state["custom"]
    if not roster or (listed["on"] and today - date.fromisoformat(listed["on"]) < NEW_TERMS_EVERY):
        return []
    people = Counter(label for p in roster.people for label, _ in p.custom)
    phrases = roster.phrases()
    new = sorted((label for label in phrases if label not in listed["labels"]), key=lambda l: subscribers.fold(phrases[l]))
    listed.update(labels=sorted(phrases), on=iso(today))
    return [f'"{phrases[label]}" ({people[label]} {"person" if people[label] == 1 else "people"})' for label in new]


def sending_notes(sends: dict) -> list[str]:
    """For the owner's digest: subscribers' email that had to wait, and addresses refused."""
    notes = []
    if sends["held"]:
        notes.append(f"{sends['held']} email{'s' if sends['held'] != 1 else ''} to subscribers had to wait for "
                     f"a later run: the send cap ({SUBSCRIBER_SENDS} a run, {DAY_SENDS} a day) or the mail "
                     "server held them. If this happens most days, see \"Sending limits\" in the README.")
    if sends["refused"]:
        numbers = ", ".join(str(n) for n in sorted(sends["refused"]))
        notes.append(f"The mail server refused the address of subscriber {numbers} (their place in the "
                     "SUBSCRIBERS list). Check it in the sign-up form's export.")
    return notes


def run(state: dict, fetcher, outbox: Outbox, now: datetime, adapters: dict = ADAPTERS,
        watchlists_path: Path = WATCHLISTS, full: bool | None = None,
        max_downloads: int = MAX_DOWNLOADS, budget: timedelta = BUDGET, clock=time.monotonic,
        load_roster=lambda watchlists: None, records_to: records.Settings | None = None) -> None:
    """load_roster(watchlists) gives the subscribers (needle/subscribers.py), or None.
    records_to: where the owner's "Ask for the records" links go (needle/records.py)."""
    deadline = clock() + budget.total_seconds()
    pacific = now.astimezone(PACIFIC)
    today = pacific.date()
    raw = Path(watchlists_path).read_text()
    watchlists = yaml.safe_load(raw)
    roster = load_roster(watchlists)
    owner_terms = compile_terms(watchlists)
    terms = owner_terms + (roster.terms() if roster else [])  # matched once, for everyone
    priorities = {name: (w or {}).get("priority", "normal") for name, w in watchlists.items()}
    statefile.use_watchlists(state, terms)
    sends = state["sends"]
    if sends["day"] != iso(today):
        sends.update(day=iso(today), count=0)
    outbox.room = min(SUBSCRIBER_SENDS, DAY_SENDS - sends["count"])
    full = full_run_due(state, now) if full is None else full
    reader = Reader(fetcher, state, terms, now, max_downloads)
    notices, failed = [], set()

    meetings: list[Meeting] = []
    for name, adapter in adapters.items():
        window = {} if full else {"days_back": 0, "days_ahead": NEAR_DAYS}
        found, failures = adapter.collect(fetcher, today=today, with_text=False,
                                          cache=state["cache"].setdefault(name, {}), **window)
        meetings += found
        if hasattr(adapter, "collected_votes"):  # the agenda inbox also gets the alerts' votes
            counted = votes.count(state, adapter.collected_votes())
            if counted:
                print(f"Counted {counted} vote{'s' if counted != 1 else ''}.")
        for client, source in adapter.SOURCES.items():
            source_key = f"{name}/{client}"
            if client in failures:
                failed.add(source_key)
            notice = health.record_source(state, source_key, source.name, failures.get(client),
                                          adapter.calendar_url(client))
            if notice:
                notices.append(notice)

    # An agenda emailed as a notice only goes in the digest once, unless another
    # source reads that meeting's agenda (Civic Band, for Palo Alto).
    readable = {(m.body.source.name, loose(m.body.name), m.date) for m in meetings if not m.notice}
    for meeting in meetings:
        if meeting.notice and (meeting.body.source.name, loose(meeting.body.name), meeting.date) not in readable:
            state["posted"].setdefault(key(meeting.body.source, "meeting", meeting.id), {
                "date": iso(meeting.date), "line": f"{meeting.body.source.name}: {meeting.notice}",
                "url": meeting.url, "reported": False})

    by_body = defaultdict(list)
    for meeting in meetings:
        by_body[meeting.body].append(meeting.date)
    for body, dates in by_body.items():
        health.record_meetings(state, key(body.source, "body", body.id),
                               f"{body.source.name} {body.name}", dates)

    by_item: dict[tuple[int, int], tuple[Meeting, Item, list[Match]]] = {}
    all_matches = []
    left = 0
    for meeting in sorted(meetings, key=lambda m: (m.date < today, m.date)):  # soonest first
        if clock() > deadline:
            left += 1  # read next run; already-read items are cached, so it gets further
            continue
        adapter = adapters[meeting.body.source.adapter]
        for item in meeting.items:
            reader.fill_text(adapter, meeting, item)
        found = match_meeting(meeting, terms, lambda a, s=meeting.body.source: reader.pages(s, a))
        record_agenda(state, meeting, found)
        all_matches += found
        for m in found:
            by_item.setdefault((id(meeting), id(m.item)), (meeting, m.item, []))[2].append(m)
    flag_boilerplate(state, all_matches)

    plans = plan_alerts(list(by_item.values()), adapters, fetcher, reader, state, priorities, today, now)
    for plan in plans:
        alert = alert_for(plan, owner_wants, priorities)
        if alert and alert["priority"] == "high":
            outbox.send(*alerts.alert_email(alert, records_to=records_to))
        elif alert:
            state["digest"].append(alert)
    digest_due = pacific.hour >= DIGEST_HOUR
    if roster:
        serve_subscribers(roster, state, outbox, plans, priorities, today, digest_due)

    if digest_due and state["last_digest"] != iso(today):
        pending = [k for k, v in state["boilerplate"].items() if not v["reported"]]
        posted = [v for v in state["posted"].values() if not v["reported"]]
        voted = state["votes"]
        new_terms = new_custom_terms(state, roster, today)
        sending = sending_notes(sends)
        if state["digest"] or pending or posted or voted["new"] or voted["skipped"] or new_terms or sending:
            outbox.send(*alerts.digest_email(state["digest"], [state["boilerplate"][k]["note"] for k in pending],
                                             today, sorted(posted, key=lambda v: v["date"]),
                                             votes.since_digest(state), votes.to_review(state, today, owner_terms),
                                             voted["skipped"], new_terms, sending, records_to=records_to))
            for entry in posted:
                entry["reported"] = True
            state["digest"], voted["new"], voted["skipped"] = [], [], 0
            sends.update(held=0, refused=[])
            for k in pending:
                state["boilerplate"][k]["reported"] = True
        state["last_digest"] = iso(today)

    if full:
        calendars = {f"{n}/{c}": a.calendar_url(c) for n, a in adapters.items() for c in a.SOURCES}
        # A body the run no longer reads (dropped from an adapter) isn't quiet, just not asked.
        not_read = {k.rsplit("/body/", 1)[0] for k in state["bodies"]} - set(calendars)
        notices += health.quiet_bodies(state, today, failed | not_read, calendars)
        state["last_full_run"] = iso(now)
    for subject, text in notices:
        outbox.send(subject, text)
    try:
        outbox.flush()
    finally:
        sends["count"] += outbox.delivered

    statefile.prune(state, today)
    print(f"{'Full' if full else 'Near-meeting'} run: {len(meetings)} meetings, "
          f"{len(all_matches)} matches, {reader.texts_read} item texts read, "
          f"{reader.downloads} downloads ({reader.deferred} left for the next run), "
          f"{left} meetings left for the next run (time budget), "
          f"{getattr(fetcher, 'deferred', 0)} requests to slow sites left for the next run, "
          f"{len(outbox.sent)} emails, {len(state['digest'])} waiting for the digest.")


def test_email() -> tuple[str, str, str]:
    """A sample alert, to check that mail goes out and how it looks."""
    source = Source("legistar", "example", "Exampleville")
    meeting = Meeting(Body(source, "1", "City Council"), "1", datetime.now(PACIFIC).date(),
                      "6:00 PM", "https://github.com/drewsiegler/haystack-needle")
    item = Item("1", "Approve agreement with vendor for public safety technology", number="7")
    match = Match(meeting, item, "surveillance", "license plate reader", "item_text", 20,
                  "…the agreement covers 12 automated license plate reader cameras…")
    alert = alerts.build(meeting, [match], "test", "high", "Test email",
                         "if you can read this, Haystack Needle can send you alerts")
    _, text, html = alerts.alert_email(alert)
    return "[Haystack Needle] Test email", text, html


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="state file (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="print the emails instead of sending; save nothing")
    parser.add_argument("--test-email", action="store_true", help="send one sample alert and stop")
    parser.add_argument("--full", action="store_true", help="read every meeting, even between full runs")
    parser.add_argument("--max-downloads", type=int, default=MAX_DOWNLOADS, help="PDF downloads this run")
    parser.add_argument("--cache", type=Path, help="cache responses here (for repeated dry runs)")
    parser.add_argument("--only", choices=sorted(ADAPTERS), help="run one adapter (dry runs only)")
    args = parser.parse_args(argv)
    if args.only and not args.dry_run:
        parser.error("--only is for dry runs: a real run with one adapter would make the others look quiet")

    try:
        settings = None if args.dry_run else mailer.settings_from_env()
        outbox = Outbox(args.dry_run, settings)
        if args.test_email:
            outbox.send(*test_email())
            outbox.flush()
            return
    except mailer.MailError as error:
        sys.exit(str(error))

    def load_roster(watchlists):
        owner = settings.to if settings else os.environ.get("ALERT_TO")
        return subscribers.load(os.environ.get("SUBSCRIBERS"), os.environ.get("SUBSCRIBERS_KEY"), watchlists, owner)

    state = statefile.load(args.state)
    ping_url = None if args.dry_run else os.environ.get("HEALTHCHECKS_URL")
    try:
        run(state, Fetcher(cache_dir=args.cache, slow_budget=SLOW_BUDGET.total_seconds()), outbox,
            datetime.now(timezone.utc), adapters={args.only: ADAPTERS[args.only]} if args.only else ADAPTERS,
            full=True if args.full else None, max_downloads=args.max_downloads, load_roster=load_roster,
            records_to=records.settings_from_env())
    except Exception as error:
        if ping_url:
            health.ping(ping_url, fail=True)
        if isinstance(error, (mailer.MailError, subscribers.SubscriberError)):
            sys.exit(str(error))
        raise
    finally:
        try:
            outbox.flush()  # what a run that failed partway had ready, as if it had gone as it went
        except mailer.MailError as error:
            print(error, file=sys.stderr)
        if not args.dry_run:
            statefile.save(state, args.state)
    if ping_url:
        health.ping(ping_url)


if __name__ == "__main__":
    main()
