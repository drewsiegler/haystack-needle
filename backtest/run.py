"""Replay the Legistar, IQM2, CivicClerk and CivicPlus cases in backtest/cases.yaml through the matcher.

For each case, every listed stage is looked up on the agenda for its date (by
MatterId on Legistar; on IQM2, by the meeting's ID and the start of the item's
title; on CivicPlus, by the agenda ID in the stage's URL, the agenda PDF being
the only item), and the item's title, item text and attachments are matched
against today's watchlists.yaml (or the file given with --watchlists). Attachments changed after the meeting are left out,
since they couldn't have been read before it. The report says whether the case
was caught at its first appearance, the earliest stage caught, and where the
match landed. It also sweeps every agenda of the same agency in the weeks
before the first appearance, by title and agenda note, for earlier items the
case list may have missed. On IQM2, which asks for a minute between requests,
the sweep covers only the first stage's board. On CivicPlus, where the agenda
PDF is all there is, the sweep reads the PDFs.

Uses the network. Responses and attachments are cached in .cache/backtest
(gitignored), so a second run is fast and makes no requests.

    python backtest/run.py [--case ID] [--lookback DAYS] [--no-attachments] [--watchlists FILE]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import civicclerk, civicplus, iqm2, legistar  # noqa: E402
from needle.fetch import Fetcher  # noqa: E402
from needle.matcher import flag_boilerplate, load_terms, match_item, match_meeting  # noqa: E402
from needle.pdftext import pdf_pages  # noqa: E402

PACIFIC = ZoneInfo("America/Los_Angeles")
LOCATION_NAMES = {"title": "title", "item_text": "item text", "attachment": "attachment"}


def page_reader(fetcher: Fetcher):
    def read(attachment) -> list[str]:
        if not attachment.url:
            return []
        try:
            return pdf_pages(fetcher.get(attachment.url))
        except Exception as error:  # a missing attachment shouldn't stop the replay
            print(f"  could not read {attachment.url}: {error}", file=sys.stderr)
            return []
    return read


def find_stage(fetcher, client, day: date, matter_id: int):
    """The meeting on `day` whose agenda carries the matter, and that item."""
    for event in legistar.fetch_events(fetcher, client, day, day):
        rows = legistar.fetch_event_items(fetcher, client, event["EventId"])
        if any(r.get("EventItemMatterId") == matter_id for r in rows):
            text = legistar.fetch_matter_text(fetcher, client, matter_id)
            meeting = legistar.parse_meeting(client, event, rows, {matter_id: text})
            item = next(i for i in meeting.items if i.matter_id == str(matter_id))
            return meeting, item
    return None, None


def item_by_title(meeting, stage):
    """The meeting's item whose title starts like the case's (up to any "...")."""
    wanted = " ".join(re.sub(r"^Item \d+:\s*", "", stage["item"]).split("...")[0].split())[:80].lower()
    return next((i for i in meeting.items if " ".join(i.title.split()).lower().startswith(wanted)), None)


def find_iqm2_stage(fetcher, client, stage):
    """The stage's IQM2 meeting and the item whose title starts like the case's
    (up to any "..."), with its staff report."""
    row = {"id": str(stage["iqm2_meeting_id"]), "date": stage["date"], "time": "",
           "board": stage["body"], "agenda": None}
    page = iqm2._decode(fetcher.get(iqm2.meeting_page(client, row["id"])))
    meeting = iqm2.build_meeting(client, row, iqm2.parse_outline(page, row["id"]))
    item = item_by_title(meeting, stage)
    if item:
        item.text = "\n\n".join(t for t in (item.text, iqm2.item_text(fetcher, client, item)) if t)
    return meeting, item


def find_civicclerk_stage(fetcher, client, stage):
    """The stage's CivicClerk meeting (found by its agenda ID on that day) and the
    item whose title starts like the case's. Its description comes with the agenda."""
    event = next((e for e in civicclerk.fetch_events(fetcher, client, stage["date"], stage["date"])
                  if e.get("agendaId") == stage["civicclerk_agenda_id"]), None)
    if event is None:
        return None, None
    meeting = civicclerk.read_meeting(fetcher, client, event)
    return meeting, item_by_title(meeting, stage)


def find_civicplus_stage(fetcher, client, stage):
    """The stage's AgendaCenter meeting, found by the agenda ID at the end of its URL.
    Its one item is the agenda, whose PDF is the attachment."""
    agenda_id = re.search(r"-(\d+)$", stage["url"]).group(1)
    row = next((r for r in civicplus.fetch_rows(fetcher, client, stage["date"], stage["date"])
                if r["id"] == agenda_id), None)
    if row is None:
        return None, None
    meeting = civicplus.build_meeting(client, row)
    [agenda] = meeting.items[0].attachments
    if day(agenda.modified) > meeting.date:
        # Replaced after the meeting: read the version that was up on the day, if any.
        versions = [v for v in civicplus.fetch_versions(fetcher, client, row["stamp"], row["id"])
                    if v["modified"] and day(v["modified"]) <= meeting.date]
        if versions:
            latest = max(versions, key=lambda v: v["modified"])
            agenda.url, agenda.modified = latest["url"], latest["modified"]
    return meeting, meeting.items[0]


def day(moment) -> date:
    """The Pacific date of a time; a time with no zone is taken as Pacific already."""
    return (moment.astimezone(PACIFIC) if moment.tzinfo else moment).date()


def is_civicplus(stage) -> bool:
    return "/AgendaCenter/ViewFile/Agenda/" in stage.get("url", "")


def stage_label(stage) -> str:
    if "matter" in stage:
        return f'matter {stage["matter"]}'
    if is_civicplus(stage):
        return f'agenda {stage["url"].rsplit("-", 1)[1]}'
    return f'agenda {stage["civicclerk_agenda_id"]}' if "civicclerk_agenda_id" in stage else f'meeting {stage["iqm2_meeting_id"]}'


def replay_stage(fetcher, client, stage, terms, read_pages):
    if "iqm2_meeting_id" in stage:
        meeting, item = find_iqm2_stage(fetcher, client, stage)
    elif "civicclerk_agenda_id" in stage:
        meeting, item = find_civicclerk_stage(fetcher, client, stage)
    elif is_civicplus(stage):
        meeting, item = find_civicplus_stage(fetcher, client, stage)
    else:
        meeting, item = find_stage(fetcher, client, stage["date"], stage["matter"])
    result = {"stage": stage, "meeting": meeting, "item": item, "matches": [], "late": 0}
    if item is None:
        return result
    # Leave out attachments last changed after the meeting day.
    kept = [a for a in item.attachments if a.modified is None or day(a.modified) <= meeting.date]
    result["late"] = len(item.attachments) - len(kept)
    item.attachments = kept
    result["matches"] = match_item(meeting, item, terms, read_pages, best_only=False)
    return result


def sweep(fetcher, client, first: date, days: int, terms):
    """Meetings and title/agenda-note matches in the `days` before the first appearance."""
    meetings = []
    for event in legistar.fetch_events(fetcher, client, first - timedelta(days=days),
                                       first - timedelta(days=1)):
        if legistar.is_placeholder(event):
            continue
        meetings.append(legistar.read_meeting(fetcher, client, event, with_text=False))
    matches = [m for meeting in meetings for m in match_meeting(meeting, terms)]
    return meetings, matches


def sweep_iqm2(fetcher, client, board: str, first: date, days: int, terms):
    """The same as sweep, for one IQM2 board's posted agendas."""
    page = iqm2._decode(fetcher.get(iqm2.calendar_page(client, first - timedelta(days=days),
                                                       first - timedelta(days=1))))
    meetings = []
    for row in iqm2.parse_calendar(page):
        if row["board"] == board and row["agenda"] and row["status"] != "Cancelled":
            outline = iqm2.parse_outline(iqm2._decode(fetcher.get(iqm2.meeting_page(client, row["id"]))), row["id"])
            meetings.append(iqm2.build_meeting(client, row, outline))
    matches = [m for meeting in meetings for m in match_meeting(meeting, terms)]
    return meetings, matches


def sweep_civicclerk(fetcher, client, first: date, days: int, terms):
    """The same as sweep, for a CivicClerk city: every body's agendas, read where one is posted."""
    meetings = [civicclerk.read_meeting(fetcher, client, event)
                for event in civicclerk.fetch_events(fetcher, client, first - timedelta(days=days),
                                                     first - timedelta(days=1))
                if civicclerk.is_listed(event)]
    matches = [m for meeting in meetings for m in match_meeting(meeting, terms)]
    return meetings, matches


def sweep_civicplus(fetcher, client, first: date, days: int, terms, read_pages):
    """The same as sweep, for an AgendaCenter city: every body's agenda, PDF and all,
    since the PDF is the only text there is."""
    meetings = [civicplus.build_meeting(client, row)
                for row in civicplus.fetch_rows(fetcher, client, first - timedelta(days=days),
                                                first - timedelta(days=1))
                if row["category_id"] not in civicplus.SKIP_CATEGORIES.get(client, set())]
    matches = [m for meeting in meetings for m in match_meeting(meeting, terms, read_pages)]
    return meetings, matches


def where(matches) -> str:
    parts = []
    for location in ("title", "item_text", "attachment"):
        found = [m for m in matches if m.location == location]
        if not found:
            continue
        terms = ", ".join(dict.fromkeys(f'"{m.term}"' for m in found))
        label = LOCATION_NAMES[location]
        if location == "attachment":
            pages = sorted({(m.attachment.name, m.page) for m in found}, key=lambda x: (x[1], x[0]))
            label += " (" + "; ".join(f"{name} p. {page}" for name, page in pages[:3]) + ")"
        parts.append(f"{label}: {terms}")
    return "; ".join(parts) or "nothing"


def first_stage(result):
    """The stage at the case's first appearance; the first stage listed if none is dated then
    (a case can list an earlier mention, like a budget line, before the first agenda item)."""
    stages = result["stages"]
    return next((s for s in stages if s["stage"]["date"] == result["case"]["first_appearance"]), stages[0])


def run_case(fetcher, case, all_terms, lookback, read_pages):
    source = case["source"]
    client = source.get("client") or source.get("tenant") or source["site"].split(".")[0]
    terms = [t for t in all_terms if t.watchlist == case["watchlist"]]
    stages = [replay_stage(fetcher, client, s, terms, read_pages) for s in case["stages"]]
    first = case["first_appearance"]
    if source["adapter"] == "iqm2":
        swept, sweep_matches = sweep_iqm2(fetcher, client, case["stages"][0]["body"], first, lookback, terms)
    elif source["adapter"] == "civicclerk":
        swept, sweep_matches = sweep_civicclerk(fetcher, client, first, lookback, terms)
    elif source["adapter"] == "civicplus":
        swept, sweep_matches = sweep_civicplus(fetcher, client, first, lookback, terms, read_pages)
    else:
        swept, sweep_matches = sweep(fetcher, client, first, lookback, terms)
    first_seen = first_stage({"case": case, "stages": stages})
    if first_seen["matches"]:
        flag_boilerplate(swept + [first_seen["meeting"]], sweep_matches + first_seen["matches"])
    matter = (legistar.fetch_matter(fetcher, client, case["stages"][0]["matter"])
              if source["adapter"] == "legistar" else {})
    return {"case": case, "stages": stages, "earlier": sweep_matches, "swept": len(swept),
            "swept_what": "agenda PDFs" if source["adapter"] == "civicplus" and read_pages
            else "titles and agenda notes", "matter": matter}


def report(results, skipped, lookback, attachments: bool) -> str:
    out = ["| Case | First appearance | Caught there? | Earliest stage caught | From where (first stage caught) |",
           "| --- | --- | --- | --- | --- |"]
    for r in results:
        stages = r["stages"]
        caught = [i for i, s in enumerate(stages) if s["matches"]]
        first = first_stage(r)
        if first["item"] is None:
            there = "not found on that day's agenda"
        else:
            there = "yes" if first["matches"] else "no"
            if any(m.boilerplate for m in first["matches"]):
                there += " (flagged as boilerplate)"
        if caught:
            s = stages[caught[0]]
            earliest = f'{caught[0] + 1} of {len(stages)}: {s["stage"]["date"]}, {s["meeting"].body.name}'
            source = where(s["matches"])
        else:
            earliest, source = "none", "nothing"
        out.append(f'| {r["case"]["id"]} | {r["case"]["first_appearance"]} | {there} | {earliest} | {source} |')

    out.append("")
    for r in results:
        case = r["case"]
        out.append(f'### {case["id"]} ({case["watchlist"]})')
        for i, s in enumerate(r["stages"], start=1):
            stage = s["stage"]
            if s["item"] is None:
                out.append(f'- Stage {i}, {stage["date"]}, {stage_label(stage)}: not found on that day\'s agenda')
                continue
            late = f' ({s["late"]} attachment(s) changed after the meeting left out)' if s["late"] else ""
            out.append(f'- Stage {i}, {stage["date"]}, {s["meeting"].body.name}, {stage_label(stage)}: '
                       f'"{" ".join(s["item"].title.split())[:120]}"; {len(s["item"].attachments)} attachment(s){late}. '
                       f'Matched {where(s["matches"])}.')
        m = r["matter"]
        agenda_date = (m.get("MatterAgendaDate") or "")[:10]
        if agenda_date and agenda_date < str(case["first_appearance"]):
            out.append(f"- Legistar gives this matter a first agenda date of {agenda_date}, "
                       "earlier than the case's first appearance.")
        replayed = {s["item"].matter_id or s["item"].id for s in r["stages"] if s["item"]}
        earlier = [x for x in r["earlier"] if (x.item.matter_id or x.item.id) not in replayed]
        seen = set()
        lines = []
        for x in sorted(earlier, key=lambda x: x.meeting.date):
            key = (x.meeting.id, x.item.matter_id or x.item.title)  # one line per item, not per row or term
            if key in seen:
                continue
            seen.add(key)
            which = f"matter {x.item.matter_id}" if x.item.matter_id else f"item {x.item.number}"
            lines.append(f'  - {x.meeting.date}, {x.meeting.body.name}, {which}: '
                         f'"{" ".join(x.item.title.split())[:120]}" ({LOCATION_NAMES[x.location]}: "{x.term}")')
        out.append(f'- Earlier matches in the {lookback} days before ({r["swept_what"]}, '
                   f'{r["swept"]} meetings): {len(lines) or "none"}')
        out += lines[:10]
        if len(lines) > 10:
            out.append(f"  - and {len(lines) - 10} more")
        out.append("")
    if skipped:
        out.append("Not replayed (their adapters come in later phases): " + ", ".join(skipped) + ".")
    if not attachments:
        out.append("Attachments were not read (--no-attachments).")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", help="replay only this case id")
    parser.add_argument("--lookback", type=int, default=60, help="days swept before each first appearance")
    parser.add_argument("--no-attachments", action="store_true", help="skip downloading attachments")
    parser.add_argument("--watchlists", type=Path, default=ROOT / "watchlists.yaml",
                        help="watchlists to match with, to try term changes (default: %(default)s)")
    args = parser.parse_args()

    cases = yaml.safe_load((ROOT / "backtest" / "cases.yaml").read_text())
    terms = load_terms(args.watchlists)
    fetcher = Fetcher(cache_dir=ROOT / ".cache" / "backtest")
    read_pages = None if args.no_attachments else page_reader(fetcher)

    results, skipped = [], []
    for case in cases:
        if args.case and case["id"] != args.case:
            continue
        if case["source"]["adapter"] not in ("legistar", "iqm2", "civicclerk", "civicplus"):
            skipped.append(case["id"])
            continue
        print(f"Replaying {case['id']}…", file=sys.stderr)
        results.append(run_case(fetcher, case, terms, args.lookback, read_pages))
    print(report(results, skipped, args.lookback, read_pages is not None))
    print(f"\n{fetcher.requests} network requests (the rest came from the cache).", file=sys.stderr)


if __name__ == "__main__":
    main()
