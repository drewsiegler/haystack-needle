from datetime import date, datetime, timezone

import httpx
import pytest

from adapters import civicplus
from conftest import FIXTURES
from needle.matcher import DEEP_SCORE, compile_terms, match_meeting
from needle.pdftext import pdf_pages
from pdfs import make_pdf

SARATOGA_JANUARY = (FIXTURES / "civicplus" / "saratoga_search_2026-01-01_2026-01-31.html").read_text()
LAH_OCTOBER = (FIXTURES / "civicplus" / "losaltoshills_search_2026-10-12_2026-11-13.html").read_text()
TERMS = compile_terms({"policing-contracts": {"terms": ["Sheriff's Office contract", "law enforcement service"]}})


class PageFetcher:
    """Answers get from a {url-prefix: bytes or exception} map and records every call."""

    def __init__(self, routes: dict):
        self.routes, self.gets = routes, []

    def get(self, url, params=None):
        self.gets.append(url)
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"no fixture for {url}")


def test_parse_rows_reads_each_agenda_with_its_body_and_posting_time():
    rows = civicplus.parse_rows(SARATOGA_JANUARY)
    assert len(rows) == 11
    council = next(r for r in rows if r["id"] == "1401")
    assert council == {
        "category_id": "13", "category": "City Council", "id": "1401", "stamp": "01212026",
        "date": date(2026, 1, 21), "title": "City Council Regular Meeting Agenda",
        # "Amended Jan 21, 2026 1:02 PM", Pacific
        "modified": datetime(2026, 1, 21, 21, 2, tzinfo=timezone.utc),
    }
    # A row that was never amended gives its posting time instead.
    planning = next(r for r in rows if r["id"] == "1397")
    assert planning["category"] == "Planning Commission"
    assert planning["modified"] == datetime(2026, 1, 9, 21, 51, tzinfo=timezone.utc)
    # HTML entities in body names come through as text.
    lah = civicplus.parse_rows(LAH_OCTOBER)
    assert "Environmental Design & Protection Committee" in {r["category"] for r in lah}


def test_parse_rows_refuses_a_page_that_is_not_an_agenda_list():
    with pytest.raises(ValueError, match="AgendaCenter"):
        civicplus.parse_rows("<html><body>Site maintenance</body></html>")


def test_build_meeting_makes_the_agenda_pdf_the_one_item_to_read():
    row = next(r for r in civicplus.parse_rows(SARATOGA_JANUARY) if r["id"] == "1401")
    meeting = civicplus.build_meeting("saratoga", row)
    pdf = "https://www.saratoga.ca.us/AgendaCenter/ViewFile/Agenda/_01212026-1401"
    assert meeting.body.name == "City Council" and meeting.body.source.name == "Saratoga"
    assert meeting.agenda_url == pdf
    assert meeting.url == "https://www.saratoga.ca.us/AgendaCenter/City-Council-13/?#_01212026-1401"
    [item] = meeting.items
    assert item.title == "City Council Regular Meeting Agenda"
    assert [(a.id, a.url, a.modified) for a in item.attachments] == [("1401", pdf, row["modified"])]


def test_agenda_text_is_matched_and_a_deep_packet_page_scores_low():
    row = next(r for r in civicplus.parse_rows(SARATOGA_JANUARY) if r["id"] == "1401")
    meeting = civicplus.build_meeting("saratoga", row)
    pages = ["SARATOGA CITY COUNCIL", "1. CONSENT CALENDAR",
             "2.1 Multi-Jurisdictional Sheriff’s Office Contract Negotiations Ad Hoc Committee"]
    pages += ["Staff report"] * 11 + ["costs of the law enforcement services agreement"]
    pdf = make_pdf([p.replace("’", "'") for p in pages])
    [first, deep] = sorted(match_meeting(meeting, TERMS, lambda a: pdf_pages(pdf)), key=lambda m: m.page)
    assert (first.term, first.location, first.page, first.score) == ("Sheriff's Office contract", "attachment", 3, 10)
    assert "2.1 Multi-Jurisdictional" in first.snippet
    assert (deep.page, deep.score) == (15, DEEP_SCORE)


def test_collect_searches_each_city_once_and_skips_the_council_civicclerk_reads():
    # Add a City Council row (category 15) to the October page: CivicClerk reads that council.
    council = ('<div class="listing"><h2 aria-controls="category-panel-15">City Council</h2><table><tbody>'
               '<tr id="row3221abc" class="catAgendaRow"><td><h3 class="noMargin" id="h410152026-3221">'
               "<strong>Oct 15, 2026</strong> &mdash; Posted Oct 9, 2026 4:00 PM</h3>"
               '<p><a href="/AgendaCenter/ViewFile/Agenda/_10152026-3221">City Council Regular Meeting</a></p>'
               "</td></tr></tbody></table></div>")
    fetcher = PageFetcher({"https://www.losaltoshills.ca.gov/": (LAH_OCTOBER + council).encode()})
    meetings, failures = civicplus.collect(fetcher, clients=("losaltoshills",), today=date(2026, 10, 14))
    assert failures == {}
    assert fetcher.gets == ["https://www.losaltoshills.ca.gov/AgendaCenter/Search/?term=&CIDs=all"
                            "&startDate=10/12/2026&endDate=11/13/2026&dateRange=&dateSelector="]
    assert sorted(m.id for m in meetings) == ["3217", "3218", "3219", "3220"]


def test_collect_keeps_going_when_one_city_fails_and_drops_rows_outside_the_window():
    fetcher = PageFetcher({
        "https://www.saratoga.ca.us/": httpx.ConnectError("the site is down"),
        "https://www.losaltoshills.ca.gov/": LAH_OCTOBER.encode(),
    })
    meetings, failures = civicplus.collect(fetcher, clients=("saratoga", "losaltoshills"),
                                           today=date(2026, 10, 14), days_back=0, days_ahead=3)
    assert failures == {"saratoga": "ConnectError: the site is down"}
    assert sorted(m.id for m in meetings) == ["3219", "3220"]  # Oct 12 and 13 are before the window


def test_parse_versions_lists_the_current_pdf_and_the_ones_it_replaced():
    page = (FIXTURES / "civicplus" / "saratoga_previousversions_10072026-1482.html").read_text()
    versions = civicplus.parse_versions(page)
    assert len(versions) == 5
    assert versions[0] == {"path": "/AgendaCenter/ViewFile/Agenda/_10072026-1482",
                           "modified": datetime(2026, 10, 8, 20, 25, tzinfo=timezone.utc)}
    assert versions[1] == {"path": "/AgendaCenter/ViewFile/ArchivedAgenda/_10072026-1327",
                           "modified": datetime(2026, 10, 7, 20, 11, tzinfo=timezone.utc)}
