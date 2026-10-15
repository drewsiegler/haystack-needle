import copy
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

from adapters import civicclerk
from conftest import FakeFetcher, load
from needle.matcher import load_terms, match_meeting

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 14)  # a window of Oct 12 to Nov 13


def events(name):
    return load(f"civicclerk/{name}")["items"]


def raw_item(agenda: dict, item_id: int) -> dict:
    """An item's row from a saved agenda, wherever it's nested."""
    def walk(rows):
        for row in rows:
            yield row
            yield from walk(row.get("childItems") or [])
    return next(row for row in walk(agenda["items"]) if row["id"] == item_id)


def lah_council():
    """The Los Altos Hills council's Jan 15, 2026 agenda, with the Flock item on it."""
    event = next(e for e in events("losaltoshillsca_events_2026-01-10_2026-01-20.json") if e["id"] == 4354)
    return civicclerk.parse_meeting("losaltoshillsca", event, load("civicclerk/losaltoshillsca_meeting_4957.json"))


def test_meeting_comes_from_the_event():
    meeting = lah_council()

    assert meeting.body.name == "City Council"
    assert meeting.body.source.name == "Los Altos Hills"
    assert meeting.id == "4354"
    # CivicClerk labels local times "Z"; 6:00 p.m. means 6:00 p.m. here.
    assert (meeting.date, meeting.time) == (date(2026, 1, 15), "6:00 PM")
    assert meeting.url == "https://losaltoshillsca.portal.civicclerk.com/event/4354/overview"
    assert meeting.agenda_url == ("https://losaltoshillsca.api.civicclerk.com/v1/Meetings/"
                                  "GetMeetingFileStream(fileId=8501,plainText=false)")
    # The packet was posted again on Feb 6, which counts as the agenda changing.
    assert meeting.modified == datetime(2026, 2, 6, 12, 26, 46, 850000, tzinfo=timezone.utc)


def test_items_leave_out_section_headings_and_number_themselves_by_section():
    meeting = lah_council()

    assert [i.number for i in meeting.items] == ["5.A", "5.I", "6.A", "6.B", "7.A"]
    flock = meeting.items[2]
    assert flock.title.startswith("Discussion and Possible Direction on Terminating Agreement with FLOCK Safety")
    assert (flock.id, flock.matter_id, flock.matter_file) == ("23122", "2025-876", "2025-876")
    assert flock.modified == meeting.modified
    assert [a.name for a in flock.attachments][:2] == ["ITEM 6A Flock staff report", "ITEM 6A Attachment 1 - 1st Flock Agreement"]
    assert len(flock.attachments) == 7


def test_attachments_use_links_that_stay_good():
    attachment = lah_council().items[2].attachments[0]

    assert attachment.id == "13491"
    assert attachment.url == "https://losaltoshillsca.api.civicclerk.com/v1/Meetings/GetAttachmentFile(fileId=13491)"
    assert attachment.modified == datetime(2026, 1, 9, 20, 4, 46, 567000, tzinfo=timezone.utc)
    assert "blob.core.windows.net" not in attachment.url


def test_only_readable_attachments_are_listed():
    row = raw_item(load("civicclerk/losaltoshillsca_meeting_4957.json"), 23122)["attachmentsList"][0]
    for change in ({}, {"contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                   {"isLink": True}, {"isDeleted": True}, {"isPublished": False}):
        parsed = civicclerk.parse_attachment("losaltoshillsca", {**row, **change})
        assert (parsed is not None) == (not change)


def test_html_in_titles_is_removed():
    meeting = lah_council()
    hearing = next(i for i in meeting.items if i.number == "7.A")

    assert hearing.title.startswith("Resolution Approving a Conditional Use Permit")
    assert "<" not in hearing.title and "&" not in hearing.title


def test_description_is_the_item_text_and_a_table_reads_as_lines():
    event = next(e for e in events("milpitasca_events_2026-10-12_2026-11-13.json") if e["agendaId"] == 2102)
    meeting = civicclerk.parse_meeting("milpitasca", event, load("civicclerk/milpitasca_meeting_2102.json"))

    closed, amendment, report = meeting.items
    assert meeting.body.name == "City Council" and meeting.time == "7:00 PM"
    assert closed.title == "(a)CONFERENCE WITH LABOR NEGOTIATOR"
    assert "Rick Bolanos, Liebert Cassidy Whitmore\nEmployee Organization(s)" in closed.text
    assert amendment.number == "5" and amendment.text.startswith("Approve")
    assert [a.name[:6] for a in report.attachments] == ["ATT E_", "ATT F_"]


def test_a_very_long_title_is_cut_and_kept_whole_in_the_text():
    row = {**raw_item(load("civicclerk/milpitasca_meeting_2102.json"), 13906),
           "agendaObjectItemName": "word " * 200, "agendaObjectItemDescription": "Staff recommends approval."}

    item = civicclerk.parse_items("milpitasca", [row], None)[0]

    assert len(item.title) <= civicclerk.MAX_TITLE + 1 and item.title.endswith("…")
    assert item.text.startswith("word word") and item.text.endswith("Staff recommends approval.")


def test_items_in_a_numbered_section_get_a_two_part_number():
    items = civicclerk.parse_items("gilroyca", load("civicclerk/gilroyca_meeting_615.json")["items"], None)

    assert [(i.number, i.title) for i in items][:2] == [("1.1", "Call to Order"), ("1.2", "Roll Call")]


def test_an_agenda_that_isnt_published_has_no_items_but_the_meeting_is_still_listed():
    event = next(e for e in events("milpitasca_events_2026-10-12_2026-11-13.json") if e["agendaId"] == 2129)
    meeting = civicclerk.parse_meeting("milpitasca", event, load("civicclerk/milpitasca_meeting_2129_unpublished.json"))

    assert meeting.items == []
    assert (meeting.date, meeting.time) == (date(2026, 10, 28), "7:00 PM")
    assert meeting.agenda_url is None  # only a notice is posted

    leaked = copy.deepcopy(load("civicclerk/losaltoshillsca_meeting_4957.json"))
    leaked["agendaIsPublish"] = False
    assert civicclerk.parse_meeting("losaltoshillsca", events("losaltoshillsca_events_2026-01-10_2026-01-20.json")[4], leaked).items == []


def test_the_item_has_no_text_left_to_read():
    fetcher = FakeFetcher({})

    assert civicclerk.item_text(fetcher, "losaltoshillsca", lah_council().items[2]) == ""
    assert fetcher.calls == []


def test_the_flock_item_is_found_by_title():
    terms = load_terms(ROOT / "watchlists.yaml")

    found = {(m.item.number, m.term, m.location) for m in match_meeting(lah_council(), terms)}

    assert ("6.A", "Flock", "title") in found


WINDOW = (date(2026, 10, 12), date(2026, 11, 13))


def routes():
    base = "https://losaltoshillsca.api.civicclerk.com/v1"
    page1 = load("civicclerk/losaltoshillsca_events_2026-10-12_2026-11-13_page1.json")
    page2 = load("civicclerk/losaltoshillsca_events_2026-10-12_2026-11-13_page2.json")
    agenda = load("civicclerk/losaltoshillsca_meeting_4957.json")
    empty = load("civicclerk/milpitasca_meeting_2129_unpublished.json")
    return page1, {
        civicclerk.events_url("losaltoshillsca", *WINDOW, skip=0): page1,
        civicclerk.events_url("losaltoshillsca", *WINDOW, skip=3): page2,
        f"{base}/Meetings/5138": agenda,
        f"{base}/Meetings/5169": empty,
    }


def test_collect_asks_for_the_window_and_follows_the_next_page():
    page1, table = routes()
    fetcher = FakeFetcher(table)

    meetings, failures = civicclerk.collect(fetcher, ["losaltoshillsca"], today=TODAY, with_text=False)

    assert failures == {}
    assert len(meetings) == 6  # three events on each page
    # toDate is left out of the list, so the window ends the day after Nov 13.
    assert fetcher.calls[0] == ("https://losaltoshillsca.api.civicclerk.com/v1/Events/GetEventsList("
                                "fromDate='2026-10-12',toDate='2026-11-14',categoryIds=[],skip=0,"
                                "sortDescending=false,cursorId=0)", None)
    assert fetcher.calls[1][0].endswith(",skip=3,sortDescending=false,cursorId=0)")  # page 1 had three
    # An agenda is read only for events that have one: two agendas, not six.
    assert [url.rsplit("/", 1)[1] for url, _ in fetcher.calls if "/Meetings/" in url] == ["5169", "5138"]
    regular = next(m for m in meetings if m.id == "4654")
    assert len(regular.items) == 5 and regular.date == date(2026, 10, 15)


def test_collect_leaves_out_deleted_and_unpublished_events():
    page1, table = routes()
    page1["items"][0]["isDeleted"] = True
    page1["items"][1]["isPublished"] = "Unpublished"

    meetings, _ = civicclerk.collect(FakeFetcher(table), ["losaltoshillsca"], today=TODAY, with_text=False)

    assert len(meetings) == 4
    assert {"4704", "4654"}.isdisjoint({m.id for m in meetings})


def test_collect_leaves_out_events_dated_outside_the_window():
    page1, table = routes()
    page1["items"][2]["startDateTime"] = "2027-12-31T10:00:00Z"
    page1["items"][2]["agendaId"] = 0

    meetings, _ = civicclerk.collect(FakeFetcher(table), ["losaltoshillsca"], today=TODAY, with_text=False)

    assert len(meetings) == 5


def test_a_client_that_is_down_doesnt_stop_the_others():
    _, table = routes()
    table[civicclerk.events_url("milpitasca", *WINDOW)] = lambda params: (_ for _ in ()).throw(
        httpx.ConnectError("no route"))

    meetings, failures = civicclerk.collect(FakeFetcher(table), ["milpitasca", "losaltoshillsca"],
                                            today=TODAY, with_text=False)

    assert list(failures) == ["milpitasca"] and "ConnectError" in failures["milpitasca"]
    assert len(meetings) == 6


def test_a_page_that_says_there_is_more_but_lists_nothing_ends_the_list():
    page1, table = routes()
    table[civicclerk.events_url("losaltoshillsca", *WINDOW, skip=3)] = {"hasMore": True, "items": []}
    fetcher = FakeFetcher(table)

    meetings, failures = civicclerk.collect(fetcher, ["losaltoshillsca"], today=TODAY, with_text=False)

    assert failures == {} and len(meetings) == 3
    assert sum("GetEventsList" in url for url, _ in fetcher.calls) == 2


def test_an_answer_that_isnt_json_is_a_failure_not_a_crash():
    _, table = routes()
    table[civicclerk.events_url("losaltoshillsca", *WINDOW)] = lambda params: (_ for _ in ()).throw(
        ValueError("Expecting value"))

    _, failures = civicclerk.collect(FakeFetcher(table), ["losaltoshillsca"], today=TODAY, with_text=False)

    assert "ValueError" in failures["losaltoshillsca"]


def test_every_source_has_a_calendar_page():
    assert set(civicclerk.SOURCES) == {"milpitasca", "morganhillca", "gilroyca", "losaltoshillsca", "losaltosca"}
    assert civicclerk.calendar_url("gilroyca") == "https://gilroyca.portal.civicclerk.com/"
