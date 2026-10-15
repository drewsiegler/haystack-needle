from datetime import date, datetime

import httpx

from adapters import legistar
from conftest import FakeFetcher, load


def test_parse_meeting_keeps_matter_ids_and_attachments():
    event = load("legistar/mountainview_events.json")[0]
    rows = load("legistar/mountainview_eventitems_3354.json")
    meeting = legistar.parse_meeting("mountainview", event, rows)

    assert meeting.body.name == "City Council"
    assert meeting.body.source.name == "Mountain View"
    assert meeting.date == date(2026, 2, 24)
    assert meeting.agenda_url.endswith("Agenda_and_Notice.pdf")
    alpr = [i for i in meeting.items if i.matter_id == "10357"][0]
    assert alpr.title == "Automated License Plate Reader Contract"
    assert alpr.number == "6.1"
    assert alpr.attachments[0].name == "Council Report"
    assert alpr.attachments[0].url.endswith(".pdf")
    assert alpr.attachments[0].modified == datetime(2026, 2, 20, 0, 11, 28, 90000)
    # Items with no matter (section headings with a title) are kept, without a matter id.
    assert any(i.matter_id is None for i in meeting.items)
    # Agenda notes become item text.
    assert any(i.text for i in meeting.items)


def test_items_follow_agenda_order():
    event = load("legistar/mountainview_events.json")[0]
    rows = list(reversed(load("legistar/mountainview_eventitems_3354.json")))
    meeting = legistar.parse_meeting("mountainview", event, rows)
    numbers = [i.number for i in meeting.items]
    assert numbers.index("2.1") < numbers.index("6.1")


def test_matter_text_joins_agenda_note():
    event = load("legistar/mountainview_events.json")[0]
    rows = load("legistar/mountainview_eventitems_3354.json")
    meeting = legistar.parse_meeting("mountainview", event, rows, {10357: "Staff recommendation"})
    alpr = [i for i in meeting.items if i.matter_id == "10357"][0]
    assert alpr.text == "Staff recommendation"


def test_fetch_matter_text_uses_latest_version():
    fetcher = FakeFetcher({
        "/matters/15221/versions": load("legistar/sanjose_matter_15221_versions.json"),
        "/matters/15221/texts/15427": load("legistar/sanjose_matter_15221_text_15427.json"),
    })
    text = legistar.fetch_matter_text(fetcher, "sanjose", 15221)
    assert "civil immigration enforcement" in text
    assert "\r" not in text


def test_fetch_matter_text_without_versions():
    fetcher = FakeFetcher({"/matters/1/versions": []})
    assert legistar.fetch_matter_text(fetcher, "sanjose", 1) == ""


def test_fetch_events_pages_until_a_short_page():
    rows = [{"EventId": n} for n in range(5)]
    fetcher = FakeFetcher({"/events": lambda p: rows[p["$skip"]:p["$skip"] + p["$top"]]})
    got = legistar.fetch_events(fetcher, "sanjose", date(2026, 10, 12), date(2026, 11, 13), page_size=2)
    assert [r["EventId"] for r in got] == [0, 1, 2, 3, 4]
    assert [c[1]["$skip"] for c in fetcher.calls] == [0, 2, 4]
    params = fetcher.calls[0][1]
    assert params["$filter"] == ("EventDate ge datetime'2026-10-12' "
                                 "and EventDate lt datetime'2026-11-14'")
    assert params["$orderby"] == "EventDate,EventId"


def test_event_items_ask_for_notes_and_attachments_not_minutes():
    fetcher = FakeFetcher({"/events/3354/eventitems": []})
    legistar.fetch_event_items(fetcher, "mountainview", 3354)
    url, params = fetcher.calls[0]
    assert url == "https://webapi.legistar.com/v1/mountainview/events/3354/eventitems"
    assert params == {"AgendaNote": 1, "MinutesNote": 0, "Attachments": 1}


def test_matter_attachments_parse():
    rows = load("legistar/mountainview_matter_10357_attachments.json")
    att = legistar.parse_attachment(rows[0])
    assert att.name == "Council Report" and att.id == "30895"


def test_collect_skips_placeholders_and_out_of_window_events():
    good = load("legistar/mountainview_events.json")[0]
    placeholder = load("legistar/sunnyvaleca_placeholder_event.json")[0]
    unpublished_in_window = dict(placeholder, EventId=1, EventDate="2026-02-25T00:00:00")
    far_future = dict(good, EventId=2, EventDate="2027-12-31T00:00:00")
    fetcher = FakeFetcher({
        "/events": [good, placeholder, unpublished_in_window, far_future],
        "/events/3354/eventitems": load("legistar/mountainview_eventitems_3354.json"),
        "/matters/10189/versions": [],
        "/matters/10357/versions": [],
    })
    meetings, failures = legistar.collect(fetcher, ["mountainview"], today=date(2026, 2, 24))
    assert [m.id for m in meetings] == ["3354"]
    assert failures == {}
    assert fetcher.calls[0][1]["$filter"].startswith("EventDate ge datetime'2026-02-22'")


def test_collect_reports_a_failing_client_and_keeps_going():
    class Failing(FakeFetcher):
        def get_json(self, url, params=None):
            if "/sanjose/" in url:
                raise httpx.ConnectError("down")
            return super().get_json(url, params)

    fetcher = Failing({"/events": []})
    meetings, failures = legistar.collect(fetcher, ["sanjose", "cupertino"], today=date(2026, 10, 14))
    assert meetings == []
    assert list(failures) == ["sanjose"]


def test_matter_history_one_row_per_body_and_day():
    fetcher = FakeFetcher({"/matters/10357/histories": load("legistar/mountainview_matter_10357_histories.json")})
    history = legistar.matter_history(fetcher, "mountainview", "10357")

    assert history == [
        (date(2026, 2, 10), "Council Policy and Procedures Committee", "recommended for approval"),
        (date(2026, 2, 24), "City Council", "approved"),
    ]


def test_collect_window_can_be_narrowed():
    fetcher = FakeFetcher({"/events": []})
    legistar.collect(fetcher, clients=["mountainview"], today=date(2026, 10, 14),
                     with_text=False, days_back=0, days_ahead=3)

    query = fetcher.calls[0][1]["$filter"]
    assert "datetime'2026-10-14'" in query and "datetime'2026-10-18'" in query
