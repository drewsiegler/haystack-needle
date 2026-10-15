from datetime import date

import httpx
import pytest

from adapters import midpen
from conftest import FIXTURES
from needle.matcher import compile_terms, match_meeting
from needle.pdftext import pdf_pages
from pdfs import make_pdf

PAGES = FIXTURES / "midpen"
LIST = (PAGES / "public-meetings.html").read_text()
BOARD = (PAGES / "bod-20261014.html").read_text()
COMMITTEE = (PAGES / "pnr-20261006.html").read_text()
NOT_POSTED = (PAGES / "bod-20261018.html").read_text()
FILES = "https://www.openspace.org/sites/default/files/"


class PageFetcher:
    """Answers get from a {url: bytes or exception} map and records every call."""

    def __init__(self, routes: dict):
        self.routes, self.gets = routes, []

    def get(self, url, params=None):
        self.gets.append(url)
        answer = self.routes.get(url)
        if answer is None:
            raise AssertionError(f"no fixture for {url}")
        if isinstance(answer, Exception):
            raise answer
        return answer


def row(meeting_id: str) -> dict:
    return next(r for r in midpen.parse_list(LIST) if r["id"] == meeting_id)


def test_parse_list_reads_each_meeting_with_its_date_and_time():
    rows = midpen.parse_list(LIST)
    assert len(rows) == 12
    assert rows[0] == {"id": "bod-20261018", "path": "/about-us/meetings/bod-20261018",
                       "name": "Board Meeting", "date": date(2026, 10, 28), "time": "7:00 pm"}
    # The slug's date isn't the meeting's: bod-20261018 meets on Oct 28.
    assert row("lfpac-20261027")["name"] == "Legislative, Funding & Public Affairs Committee Meeting"


def test_parse_list_refuses_a_page_that_isnt_the_meeting_list():
    with pytest.raises(ValueError):
        midpen.parse_list("<html><body>Page not found</body></html>")


@pytest.mark.parametrize("meeting, body", [
    ("Board Meeting", "Board of Directors"),
    ("Special Board Meeting", "Board of Directors"),
    ("Planning & Natural Resources Committee Meeting", "Planning & Natural Resources Committee"),
])
def test_body_comes_from_the_meeting_name(meeting, body):
    assert midpen.body_name(meeting) == body


def test_body_comes_from_the_name_not_the_slug():
    # Listed at /about-us/meetings/bod-20260929, but it's a committee meeting.
    assert midpen.body_name(row("bod-20260929")["name"]) == "Board Appointee Evaluation Committee"


@pytest.mark.parametrize("name, number, title", [
    ("Item%204%20AwardofContractforSensitiveSpeciesProtectionProgram_R-26-149.pdf", "4",
     "Award of Contract for Sensitive Species Protection Program (R-26-149)"),
    ("Special%20Item%202%20OptionstoFillWard3Vacancy_R-26-147_REVISED.pdf", "Special 2",
     "Options to Fill Ward 3 Vacancy (R-26-147)"),
    ("Item%206%20AppointThreeMAABondOversightCommitteeMembers_R-26-151_0.pdf", "6",
     "Appoint Three MAA Bond Oversight Committee Members (R-26-151)"),
    ("Item%203%20AmendClass%26CompPlanforFieldResourceSpecialist_R-26-148.pdf", "3",
     "Amend Class & Comp Plan for Field Resource Specialist (R-26-148)"),
    ("3%20-%2020261006_REVISED_ConsiderUseDesignationforMyotisTrailatBCR_R-26-145.pdf", "3",
     "Consider Use Designation for Myotis Trailat BCR (R-26-145)"),
    ("Item%201_PNR_Minutes_DRAFT.pdf", "1", "PNR Minutes"),
    ("Item%202%20Claims_1.pdf", "2", "Claims"),
    ("GrasslandHabitatPhotoMonitoring.pdf", "", "Grassland Habitat Photo Monitoring"),
])
def test_file_item_titles_a_packet_file_from_its_name(name, number, title):
    info = midpen.file_item(FILES + name)
    assert (info["number"], info["title"]) == (number, title)


def test_board_meeting_has_the_agenda_and_one_item_per_packet_file():
    meeting = midpen.build_meeting(row("bod-20261014"), midpen.parse_files(BOARD))
    assert (meeting.body.id, meeting.body.name) == ("board-of-directors", "Board of Directors")
    assert (meeting.id, meeting.date, meeting.time) == ("bod-20261014", date(2026, 10, 14), "5:00 pm")
    assert meeting.url == "https://www.openspace.org/about-us/meetings/bod-20261014"
    assert meeting.agenda_url == FILES + "0%20-%2020261014_BOD_Agenda_26-25_REVISED.pdf"
    agenda, *packet = meeting.items
    assert (agenda.id, agenda.title) == ("bod-20261014/agenda", "Agenda")
    assert [a.url for a in agenda.attachments] == [meeting.agenda_url]
    assert [i.number for i in packet] == ["Special 1", "Special 2"] + [str(n) for n in range(1, 10)]
    item = next(i for i in packet if i.number == "Special 2")
    assert item.id == "bod-20261014/item-special-2"  # kept when the file is replaced
    assert item.attachments[0].id == FILES + "Special%20Item%202%20OptionstoFillWard3Vacancy_R-26-147_REVISED.pdf"
    assert item.attachments[0].name == "Special Item 2 OptionstoFillWard3Vacancy_R-26-147_REVISED.pdf"
    assert item.matter_file == "R-26-147"


def test_committee_meeting_reads_its_own_body():
    meeting = midpen.build_meeting(row("pnr-20261006"), midpen.parse_files(COMMITTEE))
    assert meeting.body.name == "Planning & Natural Resources Committee"
    assert [i.title for i in meeting.items] == [
        "Agenda", "PNR Minutes",
        "Draft Strategic Plan for Adaptation & Resilience to Climate Change (R-26-144)",
        "Consider Use Designation for Myotis Trailat BCR (R-26-145)"]


def test_meeting_before_its_agenda_is_posted_has_no_items():
    meeting = midpen.build_meeting(row("bod-20261018"), midpen.parse_files(NOT_POSTED))
    assert meeting.items == [] and meeting.agenda_url is None


def test_parse_files_skips_links_outside_the_agenda_fields():
    page = BOARD.replace('<div class="field--agenda field-media-agenda">',
                         '<p><a href="/sites/default/files/Parking.pdf">Parking</a></p>'
                         '<div class="field--agenda field-media-agenda">')
    files = midpen.parse_files(page)
    assert "Parking" not in str(files["agenda"] + files["agenda_packet"])
    with pytest.raises(ValueError):
        midpen.parse_files("<html><body>Page not found</body></html>")


def test_collect_reads_the_list_then_only_the_meetings_in_the_window():
    fetcher = PageFetcher({
        midpen.LIST_URL: LIST.encode(),
        "https://www.openspace.org/about-us/meetings/bod-20261014": BOARD.encode(),
        "https://www.openspace.org/about-us/meetings/abc-20261020": NOT_POSTED.encode(),
    })
    meetings, failures = midpen.collect(fetcher, today=date(2026, 10, 15), days_back=2, days_ahead=5)
    assert failures == {}
    assert [m.id for m in meetings] == ["abc-20261020", "bod-20261014"]
    assert fetcher.gets[0] == midpen.LIST_URL and len(fetcher.gets) == 3


def test_collect_reports_a_failure_instead_of_raising():
    fetcher = PageFetcher({midpen.LIST_URL: httpx.ConnectError("down")})
    meetings, failures = midpen.collect(fetcher, today=date(2026, 10, 15))
    assert meetings == [] and "ConnectError" in failures["midpen"]


def test_matcher_finds_a_term_in_a_packet_file():
    meeting = midpen.build_meeting(row("bod-20261014"), midpen.parse_files(BOARD))
    report = make_pdf(["Staff report", "The Sensitive Species Protection Program covers the Bayfront preserves."])
    terms = compile_terms({"habitat": {"terms": ["Sensitive Species Protection"]}})

    def read_pages(attachment):
        return pdf_pages(report) if "R-26-149" in attachment.url else []

    matches = match_meeting(meeting, terms, read_pages)
    assert {m.item.number for m in matches} == {"4"}
    assert {m.location for m in matches} >= {"title"}


def test_calendar_url_is_the_public_meetings_page():
    assert midpen.calendar_url("midpen") == "https://www.openspace.org/get-involved/public-meetings"
