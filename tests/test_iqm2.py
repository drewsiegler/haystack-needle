from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from adapters import iqm2
from needle.fetch import Deferred
from needle.matcher import load_terms, match_meeting

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "iqm2"
TODAY = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc)  # 10 a.m. Pacific


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeFetcher:
    """Serves the saved pages: each portal's calendar, and one saved agenda for
    every meeting page on that portal."""

    def __init__(self):
        self.gets, self.defer, self.fail = [], set(), set()
        self.pages = {
            iqm2.calendar_page(c, date(2026, 10, 12), date(2026, 11, 13)):
                fixture(f"{c}_calendar_2026-10-12_2026-11-13.html") for c in iqm2.SOURCES}
        self.agendas = {"sccgov": fixture("sccgov_meeting_17578.html"),
                        "santaclaravta": fixture("santaclaravta_meeting_4678.html")}

    def get(self, url):
        client = url.split("//")[1].split(".")[0]
        if client in self.defer and "Detail_" in url:
            raise Deferred("later")
        if client in self.fail:
            return fixture("sccgov_error_page.html").encode()
        self.gets.append(url)
        if "Detail_Meeting" in url:
            return self.agendas[client].encode()
        if url not in self.pages:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))
        return self.pages[url].encode()


def pages_read(fetcher):
    return [url.split("//")[1].split(".")[0] + ":" + url.split("ID=")[1] for url in fetcher.gets if "Detail_Meeting" in url]


def test_calendar_lists_meetings_with_their_board_status_and_agenda():
    rows = iqm2.parse_calendar(fixture("sccgov_calendar_2026-10-12_2026-11-13.html"))
    assert len(rows) == 87
    assert sum(r["status"] == "Cancelled" for r in rows) == 10
    assert rows[2] == {
        "id": "17855", "date": date(2026, 10, 13), "time": "2:00 PM",
        "board": "Finance and Government Operations Committee", "type": "Regular Meeting",
        "status": "Scheduled", "agenda": "16009",
        "sig": "TUESDAY, OCTOBER 13, 2026 2:00 PM|Scheduled|14:16009,1:16009"}
    vta = iqm2.parse_calendar(fixture("santaclaravta_calendar_2026-10-12_2026-11-13.html"))
    assert [r["board"] for r in vta if r["id"] == "4678"] == ["Board of Directors"]


def test_a_meeting_with_no_time_still_has_its_date():
    page = fixture("sccgov_calendar_2026-10-12_2026-11-13.html").replace(
        "TUESDAY, OCTOBER 13, 2026  2:00 PM", "TUESDAY, OCTOBER 13, 2026  TBD")
    row = next(r for r in iqm2.parse_calendar(page) if r["id"] == "17855")
    assert (row["date"], row["time"]) == (date(2026, 10, 13), "")


def test_an_empty_calendar_is_not_an_error():
    # VTA often has no meetings in the 3 days a near-meeting run looks at.
    assert iqm2.parse_calendar(fixture("santaclaravta_calendar_2026-12-26_2026-12-27.html")) == []


def test_the_portals_error_page_is_an_error_not_an_empty_calendar():
    with pytest.raises(iqm2.PortalError):
        iqm2.parse_calendar(iqm2._decode(fixture("sccgov_error_page.html").encode()))


def test_outline_has_items_notes_attachments_and_numbers():
    items = {i[1]: i for i in iqm2.parse_outline(fixture("sccgov_meeting_17578.html"), "17578")}
    item_id, number, title, note, legifile, files = items["56"]
    assert (item_id, legifile) == ("127331", "127331")
    assert title.startswith("Consider recommendations relating to the Surveillance Use Policy")
    assert note.startswith("Possible action:\na. Adopt finding")
    assert len(files) == 9 and files[0] == ["265473", "Clean - Office of the Sheriff ALPR SUP Saratoga"]
    assert items["4.a"][2].startswith("Adjourn in honor and memory")
    # Printouts repeat the staff report, so they aren't attachments.
    assert not any("Printout" in name for i in items.values() for _, name in i[5])
    # An item with no legislative file gets an ID from its title.
    assert items["57"][0].startswith("17578-") and items["57"][4] is None


def test_vta_numbers_join_the_levels_and_keep_add_on_attachments():
    items = iqm2.parse_outline(fixture("santaclaravta_meeting_4678.html"), "4678")
    assert [(i[1], i[2]) for i in items if i[4]] == [
        ("2.1", "2026-36 VTA Strategic Plan Update"),
        ("2.2", "Silicon Valley Express Lanes Strategic Plan Discussion")]
    assert [name for _, name in items[5][5]] == ["Attachment A - SVEL Map Near-Term Projects", "261016_SVEL_SP_BdWkshp"]
    assert len({i[0] for i in items}) == len(items)  # "Roll Call" twice still gets two IDs


def test_item_text_is_the_staff_report():
    text = iqm2.parse_item_text(fixture("santaclaravta_legifile_10345.html"))
    assert text.startswith("Financial Impact\nThere is no financial impact.")
    assert "Recommendation\nReceive item on Silicon Valley Express Lanes" in text
    assert "Version History" not in text and "woopra" not in text


def test_item_text_needs_no_request_without_a_legislative_file():
    fetcher = FakeFetcher()
    item = iqm2.build_meeting("sccgov", {"id": "1", "date": TODAY, "time": "", "board": "B", "agenda": None},
                              [["1-x", "1", "Roll Call.", "", None, []]]).items[0]
    assert iqm2.item_text(fetcher, "sccgov", item) == "" and fetcher.gets == []


def test_first_run_reads_each_agenda_soonest_first_taking_turns_between_portals():
    fetcher, cache = FakeFetcher(), {}
    meetings, failures = iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW)
    assert failures == {}
    assert len(meetings) == 77 + 21  # cancelled meetings left out
    assert pages_read(fetcher)[:4] == ["sccgov:17855", "santaclaravta:4697", "sccgov:18132", "santaclaravta:4668"]
    assert len(pages_read(fetcher)) == 16 + 4
    posted = [m for m in meetings if m.items]
    assert len(posted) == 20 and all(m.agenda_url for m in posted)
    # A meeting with no agenda yet still counts for the health check.
    unposted = next(m for m in meetings if m.id == "17607")
    assert unposted.body.name == "Board of Supervisors" and unposted.items == []
    board = next(m for m in meetings if m.id == "4678")
    assert board.url == "https://santaclaravta.iqm2.com/Citizens/Detail_Meeting.aspx?ID=4678"
    assert board.items[5].attachments[0].url == \
        "https://santaclaravta.iqm2.com/Citizens/FileOpen.aspx?Type=4&ID=15121&Inline=True"


def test_later_runs_reread_only_changed_new_or_stale_agendas():
    fetcher, cache = FakeFetcher(), {}
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW)

    fetcher.gets.clear()
    meetings, _ = iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW + timedelta(hours=1))
    assert pages_read(fetcher) == []  # just the two calendars
    assert len([m for m in meetings if m.items]) == 20  # rebuilt from the cache

    fetcher.gets.clear()
    cache["sccgov"]["17747"]["sig"] = "an older version of the calendar row"
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW + timedelta(hours=1))
    assert pages_read(fetcher) == ["sccgov:17747"]

    # Two hours on, agendas for the next 3 days are read again for add-ons; later ones wait a day.
    fetcher.gets.clear()
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW + timedelta(hours=2))
    near = {r for r in pages_read(fetcher)}
    assert near and all(date.fromisoformat(cache[c][i]["date"]) <= TODAY + timedelta(days=3)
                        for c, i in (r.split(":") for r in near))


def test_a_portal_out_of_time_lists_its_meetings_and_reads_them_next_run():
    fetcher, cache = FakeFetcher(), {}
    fetcher.defer = {"sccgov"}
    meetings, failures = iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW)
    assert failures == {} and cache["sccgov"] == {}
    assert len([m for m in meetings if m.items]) == 4  # VTA only
    fetcher.defer = set()
    fetcher.gets.clear()
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW + timedelta(hours=1))
    assert len(pages_read(fetcher)) == 16


def test_a_portal_showing_its_error_page_fails_alone():
    fetcher = FakeFetcher()
    fetcher.fail = {"sccgov"}
    meetings, failures = iqm2.collect(fetcher, today=TODAY, with_text=False, cache={}, now=NOW)
    assert list(failures) == ["sccgov"] and "error page" in failures["sccgov"]
    assert {m.body.source.client for m in meetings} == {"santaclaravta"}


def test_cache_forgets_meetings_that_are_over_or_off_the_calendar():
    fetcher, cache = FakeFetcher(), {}
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW)
    cache["sccgov"]["99"] = {"date": "2026-10-20", "sig": "", "fetched": NOW.isoformat(), "items": []}
    cache["sccgov"]["98"] = {"date": "2026-10-01", "sig": "", "fetched": NOW.isoformat(), "items": []}
    cache["sccgov"]["97"] = {"date": "2026-12-01", "sig": "", "fetched": NOW.isoformat(), "items": []}
    iqm2.collect(fetcher, today=TODAY, with_text=False, cache=cache, now=NOW)
    assert "99" not in cache["sccgov"] and "98" not in cache["sccgov"]
    assert "97" in cache["sccgov"]  # beyond this calendar's window; not judged


def test_the_county_license_plate_reader_policy_matches_in_its_title():
    terms = load_terms(ROOT / "watchlists.yaml")
    row = {"id": "17578", "date": date(2026, 1, 27), "time": "9:30 AM", "board": "Board of Supervisors",
           "agenda": "15600"}
    meeting = iqm2.build_meeting("sccgov", row, iqm2.parse_outline(fixture("sccgov_meeting_17578.html"), "17578"))
    found = {(m.item.number, m.term, m.location) for m in match_meeting(meeting, terms)}
    assert ("56", "automated license plate", "title") in found
    assert ("56", "surveillance use policy", "title") in found
