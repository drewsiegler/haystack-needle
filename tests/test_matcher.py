from datetime import date
from pathlib import Path

import pytest

from needle.matcher import (DEEP_SCORE, compile_terms, flag_boilerplate, load_terms, match_item,
                            match_meeting)
from needle.models import Attachment, Body, Item, Meeting, Source
from needle.pdftext import pdf_pages
from pdfs import make_pdf

WATCHLISTS = Path(__file__).parents[1] / "watchlists.yaml"
BODY = Body(Source("legistar", "sanjose", "San José"), "1", "City Council")


def meeting(*items, id="1", body=BODY):
    return Meeting(body, id, date(2026, 3, 10), items=list(items))


def terms_for(*terms, case_sensitive=()):
    return compile_terms({"w": {"terms": list(terms), "case_sensitive": list(case_sensitive)}})


def hits(text, *terms, case_sensitive=()):
    m = meeting(Item("1", text))
    return [x.term for x in match_item(m, m.items[0], terms_for(*terms, case_sensitive=case_sensitive))]


@pytest.mark.parametrize("text", [
    "Automated License Plate Readers Data Usage Protocol Update",
    "automated license plate reader contract",
    "AUTOMATED LICENSE PLATE READER",
    "Automated\nLicense  Plate Reader",
])
def test_any_case_and_plural_last_word(text):
    assert hits(text, "license plate reader") == ["license plate reader"]


def test_plural_only_on_the_last_word():
    assert hits("License Plates Reader", "license plate reader") == []


def test_y_plural():
    assert hits("Surveillance Technologies Ordinance", "surveillance technology")


def test_plural_term_does_not_match_singular():
    assert hits("Tenant Protection Program", "tenant protections") == []


def test_whole_words_only():
    assert hits("current rental car policy", "rent") == []
    assert hits("Flocking birds", "Flock") == []
    assert hits("Sheriff's Office contract", "sheriff") == ["sheriff"]


def test_case_sensitive_term():
    assert hits("ICE agents at City Hall", "ICE", case_sensitive=["ICE"]) == ["ICE"]
    assert hits("Sharks Ice rink lease", "ICE", case_sensitive=["ICE"]) == []
    assert hits("Office of police services", "ICE", case_sensitive=["ICE"]) == []


@pytest.mark.parametrize("text", [
    "Los Gatos-Monte Sereno Police", "Los Gatos - Monte Sereno Police",
    "Los Gatos -Monte Sereno", "Los Gatos Monte Sereno", "Los Gatos – Monte Sereno",
])
def test_hyphen_counts_as_space(text):
    assert hits(text, "Los Gatos-Monte Sereno")


def test_term_with_punctuation():
    assert hits("Section 287(g) agreements", "287(g)") == ["287(g)"]
    assert hits("real time crime center", "real-time crime center")


def test_watchlist_file_catches_the_dry_run_misses():
    terms = load_terms(WATCHLISTS)
    for title, watchlist in [
        ("Receive presentation on License Plate Recognition", "surveillance"),
        ("Restricting Civil Immigration Enforcement Activities on Certain City Properties", "immigration"),
        ("Ordinance Relating to the Residential Tenant Protections Program", "tenants"),
        ("Study Session - Tenant Anti-Harassment & Retaliation Protections", "tenants"),
        ("Internal Controls for Automatic License Plate Readers (Mayor, Foley)", "surveillance"),
    ]:
        m = meeting(Item("1", title))
        assert watchlist in {x.watchlist for x in match_item(m, m.items[0], terms)}, title


def test_title_beats_item_text_beats_attachment():
    att = Attachment("a", "Staff Report", "https://x/a.pdf")
    item = Item("1", "Flock contract", text="Lease cameras from Flock Safety", attachments=[att])
    pages = pdf_pages(make_pdf(["Flock Safety ALPR"]))
    m = meeting(item)
    best = {x.term: x for x in match_item(m, item, terms_for("Flock", "Flock Safety", "ALPR"),
                                          lambda a: pages)}
    assert best["Flock"].location == "title"
    assert best["Flock Safety"].location == "item_text"
    assert best["ALPR"].location == "attachment"
    assert best["ALPR"].page == 1 and best["ALPR"].attachment is att
    assert best["Flock"].score > best["Flock Safety"].score > best["ALPR"].score


def test_every_location_when_not_best_only():
    item = Item("1", "Flock contract", text="Flock Safety lease")
    m = meeting(item)
    found = match_item(m, item, terms_for("Flock"), best_only=False)
    assert [x.location for x in found] == ["title", "item_text"]


def test_deep_attachment_pages_score_low():
    pages = ["cover"] * 11 + ["the ShotSpotter contract"]
    item = Item("1", "Budget", attachments=[Attachment("a", "Packet", "u")])
    [match] = match_item(meeting(item), item, terms_for("ShotSpotter"), lambda a: pages)
    assert match.page == 12 and match.score == DEEP_SCORE


def test_snippet_shows_context():
    item = Item("1", "x " * 100 + "Flock Safety cameras" + " y" * 100)
    [match] = match_item(meeting(item), item, terms_for("Flock Safety"))
    assert "Flock Safety cameras" in match.snippet
    assert match.snippet.startswith("…") and match.snippet.endswith("…")


def test_boilerplate_needs_more_than_half_of_a_bodys_meetings():
    terms = terms_for("sanctuary", "Flock")
    meetings = [meeting(Item(str(n), "Sanctuary city statement"), id=str(n)) for n in range(4)]
    meetings.append(meeting(Item("9", "Flock contract"), id="9"))
    other = Body(BODY.source, "2", "Rules Committee")
    meetings.append(meeting(Item("10", "Sanctuary policy"), id="10", body=other))
    matches = [m for mt in meetings for m in match_meeting(mt, terms)]
    flag_boilerplate(meetings, matches)
    flagged = {(m.meeting.body.name, m.term): m.boilerplate for m in matches}
    assert flagged == {("City Council", "sanctuary"): True, ("City Council", "Flock"): False,
                       ("Rules Committee", "sanctuary"): False}  # 1 meeting: too few to judge
