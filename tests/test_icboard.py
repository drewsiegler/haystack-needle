from datetime import date

import httpx

from adapters import icboard
from conftest import FIXTURES
from needle.matcher import compile_terms, match_meeting

F = FIXTURES / "icboard"
FEED = (F / "mvla_rss.xml").read_bytes()
AGENDA = (F / "mvla_agenda_975.html").read_bytes()
ITEM = (F / "mvla_item_10.4.html").read_bytes()
ITEM_10_4 = "OIGo3fsifZ4="
BASE = "https://mvlapub.ic-board.com"
TODAY = date(2026, 10, 13)  # a window of Oct 11 to Nov 12: only the Oct 12 meeting


class PageFetcher:
    """Answers get from {url-prefix: bytes or exception}, longest prefix first, and records every call."""

    def __init__(self, routes: dict):
        self.routes, self.gets = routes, []

    def get(self, url, params=None):
        self.gets.append(url)
        for prefix in sorted(self.routes, key=len, reverse=True):
            if url.startswith(prefix):
                answer = self.routes[prefix]
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"no fixture for {url}")


def fetcher(**changes):
    routes = {f"{BASE}/Reference_Library/ESB_RSS/rss.xml": FEED,
              f"{BASE}/public_agendaview.aspx?mtgId=975": AGENDA,
              f"{BASE}/public_itemview.aspx?ItemId=": b"<html><body>An item with nothing filled in</body></html>",
              f"{BASE}/public_itemview.aspx?ItemId={ITEM_10_4}": ITEM}
    routes.update(changes)
    return PageFetcher(routes)


def item_gets(f):
    return [url for url in f.gets if "public_itemview" in url]


def test_parse_feed_lists_published_agendas_with_their_date_and_time():
    rows = icboard.parse_feed(FEED)
    assert len(rows) == 6
    assert rows[0] == {"id": "975", "date": date(2026, 10, 12), "time": "7:00 PM", "type": "Regular Board Meeting"}
    assert rows[-1]["date"] == date(2026, 8, 3)  # "Aug  3 2026 7:00PM Agenda", two spaces


def test_parse_agenda_reads_each_item_number_title_and_type():
    items = icboard.parse_agenda(AGENDA.decode())
    assert len(items) == 38
    contract = next(i for i in items if i["number"] == "10.4")
    assert contract["title"] == "Contractor Agreement Between Mountain View Los Altos High School District and Ed Theory LLC."
    assert contract["type"] == "Action" and contract["revised"] == ""
    assert f"ItemId={ITEM_10_4}" in contract["href"]


def test_parse_item_keeps_the_labelled_fields_and_the_pdf():
    found = icboard.parse_item("mvla", ITEM.decode())
    assert found["text"].startswith("Enclosure: Contractor Agreement, Ed Theory LLC.\n\nSummary: This contract")
    assert "Funding: Special Education Funds, up to $122,310.00." in found["text"]
    assert "Recommendation: Approve the agreement" in found["text"]
    for left_out in ("Subject", "Meeting Date", "Strat", "10/12/2026"):
        assert left_out not in found["text"]
    assert found["files"] == [["38917259-ed86-49a2-9ace-40d1d150846a", "Ed Theory LLC Sch Psych.pdf",
                               f"{BASE}/Attachments/38917259-ed86-49a2-9ace-40d1d150846a.pdf"]]


def test_collect_reads_the_feed_the_agenda_and_each_item_page():
    f, cache = fetcher(), {}
    meetings, failures = icboard.collect(f, today=TODAY, cache=cache)
    assert failures == {}
    [meeting] = meetings
    assert (meeting.id, meeting.date, meeting.time) == ("975", date(2026, 10, 12), "7:00 PM")
    assert meeting.body.name == "Board of Trustees" and meeting.body.source.name == "Mountain View-Los Altos HSD"
    assert meeting.agenda_url == f"{BASE}/public_agendaview.aspx?mtgId=975"
    assert len(meeting.items) == 38 and len(item_gets(f)) == 38
    contract = next(i for i in meeting.items if i.number == "10.4")
    assert contract.id == ITEM_10_4 and "Ed Theory" in contract.text
    assert [a.name for a in contract.attachments] == ["Ed Theory LLC Sch Psych.pdf"]
    assert icboard.item_text(f, "mvla", contract) == ""


def test_item_pages_are_read_again_only_when_changed_or_on_a_new_day():
    cache = {}
    icboard.collect(fetcher(), today=TODAY, cache=cache)

    again = fetcher()
    meetings, _ = icboard.collect(again, today=TODAY, cache=cache)
    assert item_gets(again) == []  # the same day: everything from the cache
    contract = next(i for i in meetings[0].items if i.number == "10.4")
    assert "Ed Theory" in contract.text and len(contract.attachments) == 1

    revised = fetcher(**{f"{BASE}/public_agendaview.aspx?mtgId=975": AGENDA.replace(
        b'ri_lblRevised" class="stored-text-label"></span>', b'ri_lblRevised" class="stored-text-label">Revised</span>', 1)})
    icboard.collect(revised, today=TODAY, cache=cache)
    assert len(item_gets(revised)) == 1  # only the item marked revised

    next_day = fetcher()
    icboard.collect(next_day, today=date(2026, 10, 14), cache=cache)
    assert len(item_gets(next_day)) == 38


def test_items_of_a_meeting_that_is_over_are_forgotten():
    cache = {}
    icboard.collect(fetcher(), today=TODAY, cache=cache)
    assert len(cache["mvla"]) == 38
    icboard.collect(fetcher(), today=date(2026, 10, 20), cache=cache)  # Oct 12 is past the two days back
    assert cache["mvla"] == {}


def test_a_near_meeting_run_keeps_items_the_full_runs_still_need():
    cache = {}
    icboard.collect(fetcher(), today=TODAY, cache=cache)
    icboard.collect(fetcher(), today=date(2026, 10, 14), days_back=0, days_ahead=3, cache=cache)
    assert len(cache["mvla"]) == 38  # Oct 12 is still inside a full run's two days back


def test_an_item_page_that_fails_leaves_that_item_without_text():
    f = fetcher(**{f"{BASE}/public_itemview.aspx?ItemId={ITEM_10_4}": httpx.ReadTimeout("slow")})
    meetings, failures = icboard.collect(f, today=TODAY, cache={})
    assert failures == {}
    contract = next(i for i in meetings[0].items if i.number == "10.4")
    assert contract.text == "" and contract.attachments == []


def test_a_feed_that_fails_is_reported_not_raised():
    meetings, failures = icboard.collect(
        fetcher(**{f"{BASE}/Reference_Library/ESB_RSS/rss.xml": httpx.ConnectError("down")}), today=TODAY, cache={})
    assert meetings == [] and failures == {"mvla": "ConnectError: down"}
    _, failures = icboard.collect(
        fetcher(**{f"{BASE}/Reference_Library/ESB_RSS/rss.xml": b"<html>maintenance</html"}), today=TODAY, cache={})
    assert "ParseError" in failures["mvla"]


def test_item_text_is_matched():
    meetings, _ = icboard.collect(fetcher(), today=TODAY, cache={})
    terms = compile_terms({"test": {"terms": ["school psychologist"]}})
    [match] = match_meeting(meetings[0], terms)
    assert (match.item.number, match.location) == ("10.4", "item_text")
