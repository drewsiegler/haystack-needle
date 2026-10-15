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
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from adapters import civicclerk, civicplus, iqm2, legistar
from needle import alerts, health, mailer
from needle import state as statefile
from needle.fetch import Deferred, Fetcher
from needle.matcher import DEEP_SCORE, compile_terms, match_meeting, snippet
from needle.models import Attachment, Body, Item, Match, Meeting, Source
from needle.pdftext import pdf_pages

ROOT = Path(__file__).resolve().parents[1]
WATCHLISTS = ROOT / "watchlists.yaml"
DEFAULT_STATE = ROOT / ".state" / "state.json"
PACIFIC = ZoneInfo("America/Los_Angeles")
ADAPTERS = {"legistar": legistar, "iqm2": iqm2, "civicclerk": civicclerk, "civicplus": civicplus}
FULL_EVERY = timedelta(minutes=110)  # a full run every 2 hours
NEAR_DAYS = 3  # between full runs, re-check meetings from 72 hours out through meeting day
TEXT_TTL = timedelta(hours=24)  # re-read item text at least daily, in case it changed quietly
DIGEST_HOUR = 7  # Pacific
MAX_DOWNLOADS = 150  # PDFs per run; the rest wait for the next run, soonest meetings first
BUDGET = timedelta(minutes=40)  # then stop reading meetings, well inside the job's 55-minute limit
# Slow hosts (IQM2 asks for a minute between requests) may hold the run up only
# this long; after that, a request that would wait is left for the next run.
# Requests whose minute has passed while the run did other work still go out.
SLOW_BUDGET = timedelta(minutes=25)
AGENDA_PAGES = 40  # how far into an agenda to look for the public comment notice
MIN_AGENDAS = 4  # a body's agendas needed before a phrase can be judged boilerplate


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def iso(value) -> str | None:
    return value.isoformat() if value else None


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

    def _download(self, url: str) -> bytes | None:
        try:
            data = self.fetcher.get(url)
        except Deferred:
            return None  # a slow host's time is up; try next run
        except httpx.HTTPError as error:
            self.downloads += 1
            print(f"Couldn't download {url}: {type(error).__name__}", file=sys.stderr)
            return None
        self.downloads += 1
        return data

    def pages(self, source: Source, attachment: Attachment) -> list[str]:
        k = key(source, "attachment", attachment.id)
        entry = self.state["attachments"].get(k)
        if entry and entry["modified"] == iso(attachment.modified):
            entry["seen"] = self.today
            return cached_pages(entry)
        if not alerts.safe_url(attachment.url):
            return []
        if self.downloads >= self.max_downloads:
            self.deferred += 1
            return cached_pages(entry) if entry else []
        data = self._download(attachment.url)
        if data is None:
            return cached_pages(entry) if entry else []
        digest = sha(data)
        if entry and entry["sha256"] == digest:
            entry.update(seen=self.today, modified=iso(attachment.modified))
            return cached_pages(entry)
        pages = pdf_pages(data)
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
            state["boilerplate"][note_key] = {
                "note": f'"{m.term}" ({m.watchlist}) is on {count} of {len(agendas)} {name} agendas',
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


def plan_alerts(by_item: list[tuple[Meeting, Item, list[Match]]], adapters: dict, fetcher,
                reader: Reader, state: dict, priorities: dict, today: date, now: datetime) -> list[dict]:
    """New and updated alerts, one per item per meeting. Records each in the state."""
    planned = []
    for meeting, item, matches in by_item:
        live = [m for m in matches if not m.boilerplate]
        if not live or meeting.date < today:
            continue  # nothing to say, or the meeting is over
        source = meeting.body.source
        thread = key(source, "matter", item.matter_id) if item.matter_id else key(source, "item", item.id)
        stage = f"{thread}@{meeting.id}"
        sigs = sorted({f"{m.watchlist}|{m.term}|{m.location}|{m.attachment.id if m.attachment else ''}"
                       for m in live})
        previous = state["alerts"].get(stage)
        if previous and set(sigs) <= set(previous["sigs"]):
            continue
        adapter = adapters[source.adapter]
        history = prior_history(adapter, fetcher, state, meeting, item, thread)
        label = detail = ""
        if previous:
            label, detail = "Updated", f"a new match since the alert on {previous['sent'][:10]}"
        else:
            earlier = [r for r in history if r[1] and r[1] != meeting.body.name]
            if earlier:
                label = "This moved forward"
                detail = f"from {earlier[-1][1]} ({alerts.day_text(earlier[-1][0])}, {earlier[-1][0].year})"
        priority = ("high" if any(priorities.get(m.watchlist) == "high" and m.score > DEEP_SCORE
                                  for m in live) else "normal")
        alert = alerts.build(
            meeting, live, sha(stage + "|" + "|".join(sigs))[:10], priority, label, detail,
            [f"{alerts.day_text(d)}, {d.year}: {body}" + (f", {action}" if action else "")
             for d, body, action in history],
            reader.comment(meeting))
        state["alerts"][stage] = {
            "id": alert["id"], "thread": thread, "body": key(source, "body", meeting.body.id),
            "body_name": meeting.body.name, "date": iso(meeting.date), "priority": priority,
            "sigs": sorted(set(sigs) | set(previous["sigs"] if previous else [])), "sent": iso(now)}
        planned.append(alert)
    return planned


class Outbox:
    """Sends email, or in a dry run prints it. Never prints the address it sends to."""

    def __init__(self, dry_run: bool, settings: mailer.Settings | None = None, send=mailer.send):
        self.dry_run, self.settings, self._send = dry_run, settings, send
        self.sent: list[str] = []

    def send(self, subject: str, text: str, html: str | None = None) -> None:
        if self.dry_run:
            print("=" * 72 + f"\nSubject: {subject}\n\n{text}")
        else:
            self._send([mailer.compose(subject, text, html, self.settings.to)], self.settings)
            print(f"Sent: {subject}")
        self.sent.append(subject)


def run(state: dict, fetcher, outbox: Outbox, now: datetime, adapters: dict = ADAPTERS,
        watchlists_path: Path = WATCHLISTS, full: bool | None = None,
        max_downloads: int = MAX_DOWNLOADS, budget: timedelta = BUDGET, clock=time.monotonic) -> None:
    deadline = clock() + budget.total_seconds()
    pacific = now.astimezone(PACIFIC)
    today = pacific.date()
    raw = Path(watchlists_path).read_text()
    watchlists = yaml.safe_load(raw)
    terms = compile_terms(watchlists)
    priorities = {name: (w or {}).get("priority", "normal") for name, w in watchlists.items()}
    statefile.use_watchlists(state, sha(raw))
    full = full_run_due(state, now) if full is None else full
    reader = Reader(fetcher, state, terms, now, max_downloads)
    notices, failed = [], set()

    meetings: list[Meeting] = []
    for name, adapter in adapters.items():
        window = {} if full else {"days_back": 0, "days_ahead": NEAR_DAYS}
        found, failures = adapter.collect(fetcher, today=today, with_text=False,
                                          cache=state["cache"].setdefault(name, {}), **window)
        meetings += found
        for client, source in adapter.SOURCES.items():
            source_key = f"{name}/{client}"
            if client in failures:
                failed.add(source_key)
            notice = health.record_source(state, source_key, source.name, failures.get(client),
                                          adapter.calendar_url(client))
            if notice:
                notices.append(notice)

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

    for alert in plan_alerts(list(by_item.values()), adapters, fetcher, reader, state, priorities, today, now):
        if alert["priority"] == "high":
            outbox.send(*alerts.alert_email(alert))
        else:
            state["digest"].append(alert)

    if pacific.hour >= DIGEST_HOUR and state["last_digest"] != iso(today):
        pending = [k for k, v in state["boilerplate"].items() if not v["reported"]]
        if state["digest"] or pending:
            outbox.send(*alerts.digest_email(state["digest"], [state["boilerplate"][k]["note"] for k in pending], today))
            state["digest"] = []
            for k in pending:
                state["boilerplate"][k]["reported"] = True
        state["last_digest"] = iso(today)

    if full:
        calendars = {f"{n}/{c}": a.calendar_url(c) for n, a in adapters.items() for c in a.SOURCES}
        notices += health.quiet_bodies(state, today, failed, calendars)
        state["last_full_run"] = iso(now)
    for subject, text in notices:
        outbox.send(subject, text)

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
            return
    except mailer.MailError as error:
        sys.exit(str(error))

    state = statefile.load(args.state)
    ping_url = None if args.dry_run else os.environ.get("HEALTHCHECKS_URL")
    try:
        run(state, Fetcher(cache_dir=args.cache, slow_budget=SLOW_BUDGET.total_seconds()), outbox,
            datetime.now(timezone.utc), adapters={args.only: ADAPTERS[args.only]} if args.only else ADAPTERS,
            full=True if args.full else None, max_downloads=args.max_downloads)
    except Exception as error:
        if ping_url:
            health.ping(ping_url, fail=True)
        if isinstance(error, mailer.MailError):
            sys.exit(str(error))
        raise
    finally:
        if not args.dry_run:
            statefile.save(state, args.state)
    if ping_url:
        health.ping(ping_url)


if __name__ == "__main__":
    main()
