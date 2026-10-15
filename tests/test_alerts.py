from datetime import date

from needle import alerts
from needle.models import Attachment, Body, Item, Match, Meeting, Source

TOWN = Source("fake", "town", "Townsville")


def sample(title="Approve agreement\nfor public safety technology", url="https://town.example/a.pdf"):
    meeting = Meeting(Body(TOWN, "1", "City Council"), "9", date(2026, 10, 20), "6:00 PM",
                      agenda_url="javascript:alert(1)", url="https://town.example/meeting")
    attachment = Attachment("5", "Staff Report", url)
    item = Item("1", title, number="7", attachments=[attachment])
    match = Match(meeting, item, "surveillance", "Flock", "attachment", 10,
                  "…install 12 <b>Flock</b> cameras…", page=3, attachment=attachment)
    return meeting, match


def test_alert_follows_the_scope_format():
    meeting, match = sample()
    alert = alerts.build(meeting, [match], "abc", "high", history=["Sep 30, 2026: Public Safety Committee"],
                         comment=("…email comments to clerk@town.example by 5 p.m. Monday…", "clerk@town.example"))
    subject, text, html = alerts.alert_email(alert)

    assert subject == "[Haystack Needle: surveillance] Townsville City Council, Tue Oct 20, Item 7"
    assert "Item 7: Approve agreement for public safety technology\n" in text
    assert 'Matched: "Flock" (surveillance) in Staff Report, p. 3' in text
    assert "Staff Report: https://town.example/a.pdf" in text
    assert "clerk@town.example by 5 p.m. Monday" in text
    assert "  - Sep 30, 2026: Public Safety Committee" in text
    assert "javascript:" not in text and "javascript:" not in html  # only http(s) links


def test_agenda_text_is_escaped_in_html():
    meeting, match = sample(title='<script>alert("x")</script> Item', url='https://x.example/"onmouseover=1')
    _, text, html = alerts.alert_email(alerts.build(meeting, [match], "abc", "high"))

    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>Flock</b>" not in html
    assert 'onmouseover' not in html  # a link with a quote in it is dropped, not escaped into an attribute


def test_comment_info_reads_the_agenda_notice():
    pages = ["CITY COUNCIL AGENDA", "To submit written public comment, email the City Clerk at "
             "clerk@town.example by 5:00 p.m. the day before the meeting. Questions: info@town.example"]
    found = alerts.comment_info(pages)

    assert found[1] == "clerk@town.example"
    assert "by 5:00 p.m. the day before" in found[0]
    assert "written public comment" in found[0]
    assert alerts.comment_info(["Contact us at info@town.example"]) is None


def test_digest_lists_alerts_and_boilerplate():
    meeting, match = sample()
    alert = alerts.build(meeting, [match], "abc", "normal")
    subject, text, html = alerts.digest_email([alert], ['"Flock" is on 5 of 6 agendas'], date(2026, 10, 15))

    assert subject == "[Haystack Needle] Morning digest, Thu Oct 15: 1 item"
    assert "Item 7:" in text and "Held back as likely boilerplate" in text
    assert "&#34;Flock&#34; is on 5 of 6" in html or "&quot;Flock&quot;" in html
