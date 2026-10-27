import copy
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from adapters import boardable
from conftest import FIXTURES
from needle import state as statefile
from needle.matcher import compile_terms, match_meeting
from needle.run import Reader
from pdfs import make_pdf

DIR = FIXTURES / "boardable"
HOME = (DIR / "fhda_home.html").read_text()
MEETINGS = (DIR / "fhda_meetings.html").read_text()
BOARD = (DIR / "fhda_meeting_2026-10-05_board.html").read_text()
SITE = "https://site.boardable.com/foothill-de-anza-community-college-district"
BOARD_SLUG = "04c418-10052026-regular-meeting-of-the-board-of-trustees"
# The meetings on the home page's past list, besides the board meeting, and the one before them all.
SENATE_OCT12 = "f859ab-foothill-college-academic-senate-executive-committee-meeting"
ASFC_OCT8 = "348901-associated-students-of-foothill-campus-council"
CURRICULUM_OCT6 = "019ef7-10626-foothill-college-curriculum-committee-meeting"
ASFC_OCT1 = "4fb47c-associated-students-of-foothill-campus-council"
ASFC_OCT15 = "f69fa0-associated-students-of-foothill-campus-council"  # the one upcoming meeting listed

BOARD_GROUP = {"id": 73627, "name": "Main Governing Board"}
AUDIT = {"id": 73628, "name": "Audit and Finance Committee"}
SENATE = {"id": 73650, "name": "Foothill College Academic Senate"}
ASFC = {"id": 73638, "name": "Associated Students of Foothill College"}
CURRICULUM = {"id": 73649, "name": "Foothill College Curriculum Committee"}


def flatten(value) -> list:
    """devalue's flat list for a value, the way Nuxt writes page data (less its reactive wrappers)."""
    values, seen = [], {}

    def add(v):
        key = ("object", id(v)) if isinstance(v, (dict, list)) else (type(v).__name__, v)
        if key not in seen:
            seen[key] = i = len(values)
            values.append(None)
            values[i] = ({k: add(x) for k, x in v.items()} if isinstance(v, dict)
                         else [add(x) for x in v] if isinstance(v, list) else v)
        return seen[key]

    add(value)
    return values


def page(data: dict) -> str:
    """A site page rendered from data."""
    payload = json.dumps(flatten({"data": data, "state": {}})).replace("<", "\\u003C")
    return f'<html><body><script type="application/json" id="__NUXT_DATA__">{payload}</script></body></html>'


def board_entry() -> dict:
    return boardable.page_data(BOARD)[f"meeting-{BOARD_SLUG}"]


def board_meeting(**changes):
    entry = board_entry()
    entry["meeting"].update(changes)
    return boardable.build_meeting("fhda", "foothill-de-anza-community-college-district", entry)


def meeting_page(slug: str, meeting_id: int, shown: str, group: dict, previous_slug=None, next_slug=None) -> str:
    """A page for another meeting, made from the board meeting's."""
    data = boardable.page_data(BOARD)
    entry = data.pop(f"meeting-{BOARD_SLUG}")
    entry["meeting"].update(id=meeting_id, slug=slug, date=shown, groups=[group],
                            previous_slug=previous_slug, next_slug=next_slug)
    data[f"meeting-{slug}"] = entry
    return page(data)


class SiteFetcher:
    """Answers get from a {url: page or exception} map and records every call."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        answer = self.pages.get(url)
        if answer is None:
            raise AssertionError(f"no fixture for {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, bytes) else answer.encode()


def site_pages(**extra) -> dict:
    pages = {SITE: HOME, f"{SITE}/meetings": MEETINGS}
    pages.update({f"{SITE}/meetings/{slug}": html for slug, html in extra.items()})
    return pages


def test_unflatten_reads_devalues_typed_values_and_shared_entries():
    values = [["ShallowReactive", 1],
              {"a": 2, "b": 3, "c": 4, "d": -1, "e": 5, "f": 6, "g": 2},
              ["Reactive", 7], ["Date", "2026-10-05T23:00:00.000Z"], ["Set", 8, 8], ["Map", 8, 9],
              ["null", "k", 9], [8, 9], "x", 1]
    data = boardable.unflatten(values)
    assert data == {"a": ["x", 1], "b": "2026-10-05T23:00:00.000Z", "c": ["x", "x"], "d": None,
                    "e": {"x": 1}, "f": {"k": 1}, "g": ["x", 1]}
    assert data["a"] is data["g"]  # one entry, read once


@pytest.mark.parametrize("html, error", [
    ("<html><title>Vercel Security Checkpoint</title></html>", "didn't carry Boardable's page data"),
    ('<script id="__NUXT_DATA__">[{"data": 5}, </script>', "didn't read"),
    ('<script id="__NUXT_DATA__">[{"data": 1}, 99]</script>', "had no data in it"),
])
def test_page_data_refuses_a_page_without_boardables_data(html, error):
    with pytest.raises(ValueError, match=error):
        boardable.page_data(html)


def test_build_meeting_reads_the_board_meeting_item_by_item():
    meeting = board_meeting()
    assert (meeting.body.name, meeting.body.id, meeting.body.source.name) == (
        "Board of Trustees", "73627", "Foothill-De Anza CCD")
    assert (meeting.id, meeting.date, meeting.time) == ("511045", date(2026, 10, 5), "4:00 PM")
    assert meeting.url == f"{SITE}/meetings/{BOARD_SLUG}" and meeting.agenda_url is None
    assert len(meeting.items) == 34
    assert sum(len(i.attachments) for i in meeting.items) == 26
    titles = [i.title for i in meeting.items]
    # Section headings are left out; a heading with text of its own stays.
    assert "APPROVAL OF CONSENT CALENDAR (ACTION)" not in titles
    consent = meeting.items[titles.index("Approval of Consent Calendar")]
    assert consent.number == "7.1" and consent.text
    rivian = next(i for i in meeting.items if "California Multiple Award Schedule" in i.title)
    assert (rivian.id, rivian.number) == ("4614658", "7.1.4")  # the logical ID, which outlasts a revision
    assert rivian.text.startswith("Recommended Action:\nAuthorize the Director of Purchasing")
    assert "replace outdated police vehicles" in rivian.text and "<" not in rivian.text
    assert [i.number for i in meeting.items[-4:]] == ["14.1", "14.2.1", "14.2.2", "15.1"]


def test_fremont_union_reads_its_board_meetings_group():
    slug = "1ad658-board-of-trustees-regular-meeting-agenda"
    data = boardable.page_data((DIR / "fuhsd_meeting_2026-10-20_board.html").read_text())
    meeting = boardable.build_meeting("fuhsd", boardable.organization(data), data[f"meeting-{slug}"])
    assert (meeting.body.name, meeting.body.source.name) == ("Board of Trustees", "Fremont Union HSD")
    assert (meeting.date, meeting.time) == (date(2026, 10, 20), "4:30 PM")
    assert meeting.url == f"https://site.boardable.com/fuhsd_board/meetings/{slug}"
    assert len(meeting.items) == 40 and sum(len(i.attachments) for i in meeting.items) == 23
    minutes = next(i for i in meeting.items if i.number == "11.1")
    assert minutes.title == "Approval of September 22, 2026 Board Meeting Minutes"
    assert minutes.attachments[0].url.startswith(
        "https://app.boardable.com/fremont-union-high-school-district-1/public-site-data/meetings/")


def test_attachments_are_downloaded_from_s3_and_linked_to_the_public_viewer():
    meeting = board_meeting()
    contracts = next(i for i in meeting.items if i.title == "Ratification of Contracts and Purchase Orders")
    report = contracts.attachments[0]
    assert report.id == "1340331--f546727c-1823-477e-a1d9-f2faaf2b8e4f.pdf"  # the stored file, same in every version
    assert report.name == "20261005_POBoardReport.pdf"
    assert report.url == ("https://app.boardable.com/foothill-de-anza-community-college-district/public-site-data"
                          f"/meetings/{BOARD_SLUG}/documents/821edb96/view")
    assert report.download_url.startswith("https://boardable-production.s3.amazonaws.com/43191/pdfs/3798119.pdf?")


@pytest.mark.parametrize("change", [
    {"attached_link": "https://example.com/a-web-page"},
    {"preSignedUrl": "https://files.example.com/43191/pdfs/1.pdf?sig=1"},
    {"preSignedUrl": "http://boardable-production.s3.amazonaws.com/43191/pdfs/1.pdf"},
    {"preSignedUrl": "https://boardable-production.s3.amazonaws.com/43191/uploads/1.docx?sig=1"},
    {"preSignedUrl": None},
    {"uuid": {"short_slug": "../../admin"}},
])
def test_parse_attachment_skips_links_and_files_with_no_pdf_on_s3(change):
    upload = next(u for n in board_entry()["agenda"] for c in n["agenda"] for u in c["uploads"])
    assert boardable.parse_attachment("org", BOARD_SLUG, upload) is not None
    assert boardable.parse_attachment("org", BOARD_SLUG, upload | change) is None


def test_a_long_title_is_cut_and_kept_whole_in_the_text():
    title = "Approve the agreement " + "for campus safety services " * 15
    [item] = boardable.parse_items("org", BOARD_SLUG, [{"id": 1, "logical_id": 2, "text": title,
                                                        "description": "<p>Recommended Action: approve.</p>"}])
    assert len(item.title) <= boardable.MAX_TITLE + 1 and item.title.endswith("…")
    assert item.text == title.strip() + "\n\nRecommended Action: approve."


def test_a_private_item_is_left_out_and_not_counted():
    nodes = [{"id": 1, "text": "Closed item", "is_private": 1, "order": 0},
             {"id": 2, "text": "Open item", "is_private": 0, "order": 1}]
    assert [(i.id, i.number) for i in boardable.parse_items("org", BOARD_SLUG, nodes)] == [("2", "1")]


def test_only_the_board_and_its_committees_are_read():
    assert board_meeting(groups=[SENATE]) is None
    assert board_meeting(groups=[ASFC, AUDIT]).body.name == "Audit and Finance Committee"
    assert board_meeting(groups=[{"id": 9, "name": "Citizens’ Bond Oversight Committee"}]).body.id == "9"


def test_a_meeting_without_published_materials_has_no_items():
    assert board_meeting(materials_published=False).items == []


def test_a_meeting_page_that_sends_the_list_instead_is_skipped():
    listing = boardable.page_data(MEETINGS)
    assert boardable.meeting_entry(listing, BOARD_SLUG) is None
    with pytest.raises(ValueError, match="didn't carry the meeting"):
        boardable.meeting_entry(boardable.page_data(HOME), BOARD_SLUG)


def test_times_are_read_in_pacific_time():
    four_pm = datetime(2026, 10, 5, 16, 0)
    assert boardable._shown("Mon, Oct 5, 2026 4:00 PM PDT") == four_pm
    assert boardable._shown("Mon, Oct 5, 2026 11:00 PM UTC") == four_pm
    assert boardable._from_utc("2026-10-05T23:00:00Z") == four_pm
    assert boardable._from_utc("2026-10-05 23:00:00") == four_pm
    assert boardable._shown("Thu, Oct 15, 2026 12:00 PM PDT") == datetime(2026, 10, 15, 12, 0)
    assert boardable._shown("Thu, Oct 15, 2026 12:30 AM PDT") == datetime(2026, 10, 15, 0, 30)


def test_collect_reads_only_listed_meetings_of_the_groups_it_reads():
    fetcher = SiteFetcher(site_pages(**{
        SENATE_OCT12: meeting_page(SENATE_OCT12, 518885, "Mon, Oct 12, 2026 2:00 PM PDT", SENATE)}))
    meetings, failures = boardable.collect(fetcher, clients=("fhda",), today=date(2026, 10, 14))
    assert meetings == [] and failures == {}
    # The past list doesn't name groups, so the Oct 12 page is read to find out; the
    # upcoming student council meeting is listed with its group, so its page isn't.
    assert fetcher.calls == [SITE, f"{SITE}/meetings", f"{SITE}/meetings/{SENATE_OCT12}"]


def test_collect_reads_the_board_from_the_past_list_and_steps_back_past_its_end():
    fetcher = SiteFetcher(site_pages(**{
        SENATE_OCT12: meeting_page(SENATE_OCT12, 518885, "Mon, Oct 12, 2026 2:00 PM PDT", SENATE),
        ASFC_OCT8: meeting_page(ASFC_OCT8, 519850, "Thu, Oct 8, 2026 2:00 PM PDT", ASFC),
        CURRICULUM_OCT6: meeting_page(CURRICULUM_OCT6, 515586, "Tue, Oct 6, 2026 2:00 PM PDT", CURRICULUM),
        BOARD_SLUG: BOARD,
        ASFC_OCT1: meeting_page(ASFC_OCT1, 510000, "Thu, Oct 1, 2026 2:00 PM PDT", ASFC, next_slug=BOARD_SLUG),
    }))
    meetings, failures = boardable.collect(fetcher, clients=("fhda",), today=date(2026, 10, 6))
    assert failures == {}
    assert [(m.body.name, m.date) for m in meetings] == [("Board of Trustees", date(2026, 10, 5))]
    # All four past meetings are in the window, so there may be more: the board meeting's
    # page names the one before it, which is too early, so that's where it stops.
    assert fetcher.calls[-1] == f"{SITE}/meetings/{ASFC_OCT1}"
    assert len(fetcher.calls) == len(set(fetcher.calls)) == 7


def test_collect_steps_forward_when_more_meetings_are_upcoming_than_listed():
    listing = boardable.page_data(MEETINGS)
    listing["meetings-data"]["pagination"]["total"] = 11
    audit, board_nov9, later = "aa11bb-audit-and-finance-committee", "cc22dd-11092026-regular-meeting", "ee33ff-later"
    fetcher = SiteFetcher(site_pages(**{
        SENATE_OCT12: meeting_page(SENATE_OCT12, 518885, "Mon, Oct 12, 2026 2:00 PM PDT", SENATE),
        ASFC_OCT15: meeting_page(ASFC_OCT15, 520602, "Thu, Oct 15, 2026 2:00 PM PDT", ASFC, next_slug=audit),
        audit: meeting_page(audit, 520700, "Mon, Oct 19, 2026 1:00 PM PDT", AUDIT, next_slug=board_nov9),
        board_nov9: meeting_page(board_nov9, 520800, "Mon, Nov 9, 2026 4:00 PM PST", BOARD_GROUP, next_slug=later),
        later: meeting_page(later, 520900, "Mon, Nov 16, 2026 2:00 PM PST", BOARD_GROUP, next_slug="ff44aa-never"),
    }) | {f"{SITE}/meetings": page(listing)})
    meetings, failures = boardable.collect(fetcher, clients=("fhda",), today=date(2026, 10, 14))
    assert failures == {}
    assert [(m.body.name, m.date, m.time) for m in meetings] == [
        ("Audit and Finance Committee", date(2026, 10, 19), "1:00 PM"),
        ("Board of Trustees", date(2026, 11, 9), "4:00 PM")]
    assert fetcher.calls[-1] == f"{SITE}/meetings/{later}"  # past the window's end (Nov 13): stop


@pytest.mark.parametrize("home, failure", [
    (httpx.ConnectError("the site is down"), "ConnectError: the site is down"),
    ("<html><title>Vercel Security Checkpoint</title></html>",
     "ValueError: the page didn't carry Boardable's page data"),
])
def test_collect_reports_what_went_wrong(home, failure):
    fetcher = SiteFetcher({SITE: home})
    meetings, failures = boardable.collect(fetcher, clients=("fhda",), today=date(2026, 10, 14))
    assert meetings == [] and failures == {"fhda": failure}
    assert fetcher.calls == [SITE]


def test_an_unexpected_slug_never_goes_into_a_url():
    home = boardable.page_data(HOME)
    home["past-meetings"][0]["slug"] = "../../admin"
    fetcher = SiteFetcher({SITE: page(home), f"{SITE}/meetings": MEETINGS})
    meetings, failures = boardable.collect(fetcher, clients=("fhda",), today=date(2026, 10, 14))
    assert failures == {"fhda": "ValueError: a meeting had an unexpected slug: '../../admin'"}
    assert fetcher.calls == [SITE, f"{SITE}/meetings"]


def test_item_text_is_matched_with_the_agenda():
    terms = compile_terms({"policing-contracts": {"terms": ["police vehicles"]}})
    [match] = match_meeting(board_meeting(), terms, lambda a: [])
    assert (match.item.number, match.location) == ("7.1.4", "item_text")


def test_item_text_is_matched_fresh_every_run():
    meeting = board_meeting()
    item = next(i for i in meeting.items if i.number == "7.1.4")
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    state = statefile.empty()
    terms = compile_terms({"policing-contracts": {"terms": ["police vehicles"]}})
    Reader(None, state, terms, now, 0).fill_text(boardable, meeting, item)
    item.text = "Recommended Action: Continue this item."
    Reader(None, state, terms, now + timedelta(minutes=60), 0).fill_text(boardable, meeting, item)
    assert item.text == "Recommended Action: Continue this item."


def test_the_run_downloads_the_s3_link_and_logs_the_lasting_one(capsys):
    meeting = board_meeting()
    report = copy.copy(next(i for i in meeting.items if i.number == "7.1.1").attachments[0])
    terms = compile_terms({"policing-contracts": {"terms": ["Rivian"]}})
    fetcher = SiteFetcher({report.download_url: make_pdf(["Purchase orders", "Rivian Automotive, LLC"])})
    reader = Reader(fetcher, statefile.empty(), terms, datetime(2026, 10, 5, 12, tzinfo=timezone.utc), 5)
    assert "Rivian Automotive, LLC" in reader.pages(meeting.body.source, report)[1]
    assert fetcher.calls == [report.download_url]
    # An expired link is refused; the message names the viewer link, not the signed one.
    report.id, report.download_url = "another-file.pdf", report.download_url + "&expired=1"
    fetcher.pages[report.download_url] = httpx.HTTPStatusError(
        "403", request=httpx.Request("GET", report.download_url), response=httpx.Response(403))
    assert reader.pages(meeting.body.source, report) == []
    assert f"Couldn't download {report.url}: HTTPStatusError" in capsys.readouterr().err
