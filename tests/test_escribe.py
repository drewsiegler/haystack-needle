import copy
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from adapters import escribe
from conftest import FIXTURES
from needle import state as statefile
from needle.matcher import compile_terms, match_meeting
from needle.run import Reader

DIR = FIXTURES / "escribe"
CALENDAR = json.loads((DIR / "campbell_calendar_2025-09-01_2026-12-31.json").read_text())
REGULAR = (DIR / "campbell_agenda_2026-10-06_regular.html").read_text()
EXECUTIVE = (DIR / "campbell_agenda_2026-10-06_executive.html").read_text()
NOV18 = (DIR / "campbell_agenda_2025-11-18_regular.html").read_text()
NOV18_ADDENDUM = (DIR / "campbell_addendum_2025-11-18_regular.html").read_text()
PORTAL = "https://pub-campbell.escribemeetings.com"
CALENDAR_CALL = f"{PORTAL}/MeetingsCalendarView.aspx/GetCalendarMeetings"


def row(meeting_id: str) -> dict:
    return copy.deepcopy(next(r for r in CALENDAR["d"] if r["ID"] == meeting_id))


OCT6_REGULAR = "39edefb9-445d-4941-9401-e4d460a04abc"
OCT6_EXECUTIVE = "26ad204a-42f4-4983-a280-272f64fc35eb"
OCT5_SPECIAL = "cbe9f3fe-f585-4195-88d7-d31958bdba68"
NOV4_REVISED = "700f5e71-b769-4c34-8dbc-40e313e77fb7"
NOV18_WITH_ADDENDUM = "d2bcdef9-0fed-4dd0-a88f-058746c3b560"


class PortalFetcher:
    """Answers post_json with the calendar and get from a {url: page or exception} map,
    and records every call."""

    def __init__(self, pages: dict, calendar=CALENDAR):
        self.pages, self.calendar = pages, calendar
        self.calls: list[tuple] = []

    def post_json(self, url, body):
        self.calls.append(("POST", url, body))
        if isinstance(self.calendar, Exception):
            raise self.calendar
        return self.calendar

    def get(self, url, params=None):
        self.calls.append(("GET", url))
        answer = self.pages.get(url)
        if answer is None:
            raise AssertionError(f"no fixture for {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer.encode()


def test_parse_items_reads_numbers_titles_recommended_actions_and_attachments():
    items = escribe.parse_items("campbell", REGULAR)
    # 24 on the page; the headings 3, 7, 9 and 11 have items under them and are left out.
    assert len(items) == 20
    assert {"3", "7", "9", "11"}.isdisjoint(i.number for i in items)
    assert [i.number for i in items][:4] == ["1", "2", "3.1", "4"]
    item = next(i for i in items if i.number == "9.2")
    assert item.id == "1580"
    assert item.title.startswith("Resolutions Amending the Compensation and Working Conditions")
    assert item.text.startswith("Recommended Action: That the City Council (1) adopt a resolution amending")
    assert [(a.id, a.url) for a in item.attachments][0] == (
        "2065", "https://pub-campbell.escribemeetings.com/FileStream.ashx?DocumentId=2065")
    assert item.attachments[1].name == "2026 Confidential Resolution_10.06.2026_Final Draft.pdf"
    assert len(item.attachments) == 4
    # The Details panel after the agenda lists every attachment again; none is counted twice.
    assert sum(len(i.attachments) for i in items) == 26


def test_a_heading_on_its_own_keeps_its_text():
    [*_, closed, adjourn] = escribe.parse_items("campbell", EXECUTIVE)
    assert (closed.number, closed.title) == ("3", "MOVE INTO CLOSED EXECUTIVE SESSION")
    assert "CONFERENCE WITH LEGAL COUNSEL—EXISTING LITIGATION" in closed.text
    assert "Labor Negotiations" in closed.text
    assert closed.text.startswith("A. Personnel\nB. Litigation")


def test_parse_items_refuses_a_page_that_is_not_an_agenda():
    with pytest.raises(ValueError, match="eScribe agenda"):
        escribe.parse_items("campbell", "<html><body>The portal is down for maintenance</body></html>")


def test_a_long_title_is_cut_and_kept_whole_in_the_text():
    [item] = escribe.parse_items("campbell", NOV18_ADDENDUM)
    assert item.number == "8.1" and len(item.title) <= escribe.MAX_TITLE + 1 and item.title.endswith("…")
    assert item.text.startswith("Public Hearing to consider recommendation of (1) an ordinance amending Chapter 21.24")
    assert "Recommended Action:" in item.text


def test_agenda_kinds_reads_a_revised_agenda_alone_or_an_agenda_and_its_addendum():
    assert escribe.agenda_kinds(row(NOV4_REVISED)) == ["Merged"]
    assert escribe.agenda_kinds(row(NOV18_WITH_ADDENDUM)) == ["Agenda", "Addendum"]
    assert escribe.agenda_kinds(row(OCT6_REGULAR)) == ["Agenda"]
    assert escribe.agenda_kinds(row(OCT6_REGULAR) | {"HasAgenda": False}) == []


def test_build_meeting_puts_every_kind_of_session_under_the_city_council():
    meeting = escribe.build_meeting("campbell", row(OCT6_REGULAR), {"Agenda": REGULAR})
    assert (meeting.body.id, meeting.body.name, meeting.body.source.name) == ("city-council", "City Council", "Campbell")
    assert (meeting.id, meeting.date, meeting.time) == (OCT6_REGULAR, date(2026, 10, 6), "7:00 PM")
    assert meeting.agenda_url == f"{PORTAL}/FileStream.ashx?DocumentId=2011"  # the short agenda PDF, not the packet
    assert meeting.url == f"{PORTAL}/Meeting.aspx?Id={OCT6_REGULAR}&Agenda=Agenda&lang=English"
    assert len(meeting.items) == 20
    executive = escribe.build_meeting("campbell", row(OCT6_EXECUTIVE), {"Agenda": EXECUTIVE})
    assert (executive.body, executive.time) == (meeting.body, "6:00 PM")


def test_an_addendum_updates_its_item_and_keeps_the_agendas_attachments():
    alone = escribe.build_meeting("campbell", row(NOV18_WITH_ADDENDUM), {"Agenda": NOV18})
    meeting = escribe.build_meeting("campbell", row(NOV18_WITH_ADDENDUM), {"Agenda": NOV18, "Addendum": NOV18_ADDENDUM})
    assert [i.id for i in meeting.items] == [i.id for i in alone.items]
    hearing = next(i for i in meeting.items if i.id == "834")
    before = next(i for i in alone.items if i.id == "834")
    assert [a.id for a in before.attachments] == ["379", "380", "381"]
    assert [a.id for a in hearing.attachments] == ["379", "380", "381", "386", "387", "388", "389"]
    assert hearing.text != before.text and "Inclusionary Housing Ordinance" in hearing.text


def test_collect_lists_the_window_and_reads_each_agenda_once():
    fetcher = PortalFetcher({
        escribe.agenda_page_url("campbell", OCT5_SPECIAL): EXECUTIVE,
        escribe.agenda_page_url("campbell", OCT6_EXECUTIVE): EXECUTIVE,
        escribe.agenda_page_url("campbell", OCT6_REGULAR): REGULAR,
    })
    meetings, failures = escribe.collect(fetcher, today=date(2026, 10, 7))
    assert failures == {}
    assert fetcher.calls[0] == ("POST", CALENDAR_CALL, {"calendarStartDate": "2026-10-05", "calendarEndDate": "2026-11-07"})
    # The two November 2025 meetings on the calendar are outside the window: their agendas aren't read.
    assert [c[1].split("Id=")[1].split("&")[0] for c in fetcher.calls[1:]] == [OCT5_SPECIAL, OCT6_EXECUTIVE, OCT6_REGULAR]
    assert [(m.date, m.time) for m in meetings] == [
        (date(2026, 10, 5), "6:20 PM"), (date(2026, 10, 6), "6:00 PM"), (date(2026, 10, 6), "7:00 PM")]


def test_collect_reads_a_revised_agenda_instead_of_the_first_one():
    revised = escribe.agenda_page_url("campbell", NOV4_REVISED, "Merged")
    fetcher = PortalFetcher({revised: REGULAR})
    meetings, failures = escribe.collect(fetcher, today=date(2025, 11, 4), days_back=0, days_ahead=1)
    assert failures == {}
    assert [c[1] for c in fetcher.calls if c[0] == "GET"] == [revised]
    assert meetings[0].url == revised


@pytest.mark.parametrize("calendar, failure", [
    (httpx.ConnectError("the portal is down"), "ConnectError: the portal is down"),
    ({"Message": "There was an error processing the request."},
     "ValueError: the meeting calendar didn't answer with a list of meetings"),
    ({"d": [row(OCT6_REGULAR) | {"ID": "../Admin"}]}, "ValueError: a meeting on the calendar had an unexpected ID: '../Admin'"),
])
def test_collect_reports_what_went_wrong_and_fetches_nothing_more(calendar, failure):
    fetcher = PortalFetcher({}, calendar)
    meetings, failures = escribe.collect(fetcher, today=date(2026, 10, 7))
    assert meetings == [] and failures == {"campbell": failure}
    assert [c[0] for c in fetcher.calls] == ["POST"]


def test_recommended_action_is_matched_as_item_text():
    meeting = escribe.build_meeting("campbell", row(OCT6_REGULAR), {"Agenda": REGULAR})
    terms = compile_terms({"pay": {"terms": ["market adjustment"]}})
    [match] = match_meeting(meeting, terms, lambda a: [])
    assert (match.item.number, match.location) == ("9.3", "item_text")


def test_item_text_is_matched_fresh_every_run():
    # A revised agenda keeps its item numbers, so last run's snippets mustn't stand in for new text.
    meeting = escribe.build_meeting("campbell", row(OCT6_REGULAR), {"Agenda": REGULAR})
    item = next(i for i in meeting.items if i.number == "9.3")
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    state = statefile.empty()
    terms = compile_terms({"pay": {"terms": ["market adjustment"]}})
    Reader(None, state, terms, now, 0).fill_text(escribe, meeting, item)
    item.text = "Recommended Action: That the City Council continue this item."
    Reader(None, state, terms, now + timedelta(minutes=60), 0).fill_text(escribe, meeting, item)
    assert item.text == "Recommended Action: That the City Council continue this item."
