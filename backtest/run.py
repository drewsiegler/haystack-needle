"""Replay the Legistar cases in backtest/cases.yaml through the matcher.

For each case, every listed stage is looked up on the agenda for its date (by
MatterId), and the item's title, item text and attachments are matched against
today's watchlists.yaml. Attachments changed after the meeting are left out,
since they couldn't have been read before it. The report says whether the case
was caught at its first appearance, the earliest stage caught, and where the
match landed. It also sweeps every agenda of the same agency in the weeks
before the first appearance, by title and agenda note, for earlier items the
case list may have missed.

Uses the network. Responses and attachments are cached in .cache/backtest
(gitignored), so a second run is fast and makes no requests.

    python backtest/run.py [--case ID] [--lookback DAYS] [--no-attachments]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import legistar  # noqa: E402
from needle.fetch import Fetcher  # noqa: E402
from needle.matcher import flag_boilerplate, load_terms, match_item, match_meeting  # noqa: E402
from needle.pdftext import pdf_pages  # noqa: E402

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


def replay_stage(fetcher, client, stage, terms, read_pages):
    meeting, item = find_stage(fetcher, client, stage["date"], stage["matter"])
    result = {"stage": stage, "meeting": meeting, "item": item, "matches": [], "late": 0}
    if item is None:
        return result
    # Leave out attachments last changed after the meeting day.
    kept = [a for a in item.attachments if a.modified is None or a.modified.date() <= meeting.date]
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


def run_case(fetcher, case, all_terms, lookback, read_pages):
    client = case["source"]["client"]
    terms = [t for t in all_terms if t.watchlist == case["watchlist"]]
    stages = [replay_stage(fetcher, client, s, terms, read_pages) for s in case["stages"]]
    first = case["first_appearance"]
    swept, sweep_matches = sweep(fetcher, client, first, lookback, terms)
    first_stage = stages[0]
    if first_stage["matches"]:
        flag_boilerplate(swept + [first_stage["meeting"]], sweep_matches + first_stage["matches"])
    matter = legistar.fetch_matter(fetcher, client, case["stages"][0]["matter"])
    return {"case": case, "stages": stages, "earlier": sweep_matches, "swept": len(swept),
            "matter": matter}


def report(results, skipped, lookback, attachments: bool) -> str:
    out = ["| Case | First appearance | Caught there? | Earliest stage caught | From where (first stage caught) |",
           "| --- | --- | --- | --- | --- |"]
    for r in results:
        stages = r["stages"]
        caught = [i for i, s in enumerate(stages) if s["matches"]]
        first = stages[0]
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
                out.append(f'- Stage {i}, {stage["date"]}, matter {stage["matter"]}: not found on that day\'s agenda')
                continue
            late = f' ({s["late"]} attachment(s) changed after the meeting left out)' if s["late"] else ""
            out.append(f'- Stage {i}, {stage["date"]}, {s["meeting"].body.name}, matter {stage["matter"]}: '
                       f'"{" ".join(s["item"].title.split())[:120]}"; {len(s["item"].attachments)} attachment(s){late}. '
                       f'Matched {where(s["matches"])}.')
        m = r["matter"]
        agenda_date = (m.get("MatterAgendaDate") or "")[:10]
        if agenda_date and agenda_date < str(case["first_appearance"]):
            out.append(f"- Legistar gives this matter a first agenda date of {agenda_date}, "
                       "earlier than the case's first appearance.")
        earlier = [x for x in r["earlier"]
                   if x.item.matter_id not in {str(s["matter"]) for s in case["stages"]}]
        seen = set()
        lines = []
        for x in sorted(earlier, key=lambda x: x.meeting.date):
            key = (x.meeting.id, x.item.matter_id or x.item.title)  # one line per item, not per row or term
            if key in seen:
                continue
            seen.add(key)
            lines.append(f'  - {x.meeting.date}, {x.meeting.body.name}, matter {x.item.matter_id}: '
                         f'"{" ".join(x.item.title.split())[:120]}" ({LOCATION_NAMES[x.location]}: "{x.term}")')
        out.append(f'- Earlier matches in the {lookback} days before (titles and agenda notes, '
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
    args = parser.parse_args()

    cases = yaml.safe_load((ROOT / "backtest" / "cases.yaml").read_text())
    terms = load_terms(ROOT / "watchlists.yaml")
    fetcher = Fetcher(cache_dir=ROOT / ".cache" / "backtest")
    read_pages = None if args.no_attachments else page_reader(fetcher)

    results, skipped = [], []
    for case in cases:
        if args.case and case["id"] != args.case:
            continue
        if case["source"]["adapter"] != "legistar":
            skipped.append(case["id"])
            continue
        print(f"Replaying {case['id']}…", file=sys.stderr)
        results.append(run_case(fetcher, case, terms, args.lookback, read_pages))
    print(report(results, skipped, args.lookback, read_pages is not None))
    print(f"\n{fetcher.requests} network requests (the rest came from the cache).", file=sys.stderr)


if __name__ == "__main__":
    main()
