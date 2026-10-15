import json

import httpx

import pytest

from needle.fetch import USER_AGENT, BotCheck, Deferred, Fetcher


def make(handler, **kwargs):
    clock = {"now": 0.0}
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(client=client, sleep=sleep, clock=lambda: clock["now"], **kwargs)
    return fetcher, slept


def test_sends_the_honest_user_agent():
    seen = []
    fetcher, _ = make(lambda r: seen.append(r.headers["User-Agent"]) or httpx.Response(200, json=[]))
    assert fetcher.get_json("https://webapi.legistar.com/v1/sanjose/events") == []
    assert seen == [USER_AGENT]
    assert USER_AGENT.startswith("Mozilla/5.0 (compatible; HaystackNeedle/")


def test_waits_a_second_between_requests_to_the_same_host_only():
    fetcher, slept = make(lambda r: httpx.Response(200, content=b"x"))
    fetcher.get("https://a.example/1")
    fetcher.get("https://b.example/1")
    fetcher.get("https://a.example/2")
    assert slept == [1.0]


def test_honors_a_hosts_crawl_delay():
    fetcher, slept = make(lambda r: httpx.Response(200, content=b"x"))
    fetcher.get("https://sccgov.iqm2.com/1")
    fetcher.get("https://santaclaravta.iqm2.com/1")  # a different host doesn't wait
    fetcher.get("https://webapi.legistar.com/1")
    fetcher.get("https://webapi.legistar.com/2")
    fetcher.get("https://sccgov.iqm2.com/2")
    assert slept == [1.0, 60.0 - 1.0]


def test_leaves_slow_requests_for_the_next_run_once_its_time_is_up():
    sent = []
    fetcher, slept = make(lambda r: sent.append(str(r.url)) or httpx.Response(200, content=b"x"),
                          slow_budget=90)
    fetcher.get("https://sccgov.iqm2.com/1")
    fetcher.get("https://sccgov.iqm2.com/2")  # waits 60 s: inside the 90
    with pytest.raises(Deferred):
        fetcher.get("https://sccgov.iqm2.com/3")  # would wait until 120 s
    fetcher.get("https://webapi.legistar.com/1")  # fast hosts carry on
    fetcher.sleep(60)  # a minute of other work...
    fetcher.get("https://sccgov.iqm2.com/4")  # ...and a slow host's turn needs no wait
    assert slept == [60.0, 60]
    assert sent[-1].endswith("/4") and len(sent) == 4 and fetcher.deferred == 1


def test_keeps_the_query_already_in_a_url():
    seen = []
    fetcher, _ = make(lambda r: seen.append(str(r.url)) or httpx.Response(200, content=b"x"))
    fetcher.get("https://sccgov.iqm2.com/Citizens/Detail_Meeting.aspx?ID=17578")
    fetcher.get("https://webapi.legistar.com/v1/x/events?a=1", {"$top": 2})
    assert seen == ["https://sccgov.iqm2.com/Citizens/Detail_Meeting.aspx?ID=17578",
                    "https://webapi.legistar.com/v1/x/events?a=1&%24top=2"]


def test_retries_server_errors():
    answers = iter([httpx.Response(503), httpx.Response(200, content=b"ok")])
    fetcher, slept = make(lambda r: next(answers))
    assert fetcher.get("https://a.example/") == b"ok"
    assert fetcher.requests == 2


def test_cache_answers_without_the_network(tmp_path):
    calls = []
    fetcher, _ = make(lambda r: calls.append(r) or httpx.Response(200, content=b"pdf"), cache_dir=tmp_path)
    assert fetcher.get("https://a.example/f", {"q": 1}) == b"pdf"
    assert fetcher.get("https://a.example/f", {"q": 1}) == b"pdf"
    assert len(calls) == 1


def test_post_json_sends_the_body_with_the_same_user_agent_rate_and_retries(tmp_path):
    seen = []
    answers = iter([httpx.Response(503), httpx.Response(200, json={"d": [1]}),
                    httpx.Response(200, json={"d": [2]})])

    def handler(request):
        seen.append((request.method, request.headers["User-Agent"], json.loads(request.content)))
        return next(answers)

    fetcher, slept = make(handler, cache_dir=tmp_path)
    url = "https://pub-campbell.escribemeetings.com/MeetingsCalendarView.aspx/GetCalendarMeetings"
    assert fetcher.post_json(url, {"calendarStartDate": "2026-10-12"}) == {"d": [1]}
    assert fetcher.post_json(url, {"calendarStartDate": "2026-10-12"}) == {"d": [1]}  # cached
    assert fetcher.post_json(url, {"calendarStartDate": "2026-11-01"}) == {"d": [2]}  # another body isn't
    assert seen == [("POST", USER_AGENT, {"calendarStartDate": "2026-10-12"})] * 2 + [
        ("POST", USER_AGENT, {"calendarStartDate": "2026-11-01"})]
    # The retry's back-off, the rest of the host's 10 seconds before the retry, then
    # 10 seconds before the next request (the cached one sends nothing).
    assert slept == [5, 5.0, 10.0]


def test_leaves_a_host_alone_for_the_run_once_it_answers_with_a_bot_check():
    sent = []

    def handler(request):
        sent.append(str(request.url))
        if "FileStream" in str(request.url):
            return httpx.Response(403, headers={"cf-mitigated": "challenge"}, text="Verifying your browser")
        return httpx.Response(200, content=b"%PDF")

    fetcher, slept = make(handler)
    portal = "https://pub-campbell.escribemeetings.com"
    assert fetcher.get(f"{portal}/Meeting.aspx?Id=1") == b"%PDF"
    with pytest.raises(BotCheck, match="bot check"):
        fetcher.get(f"{portal}/FileStream.ashx?DocumentId=2053")
    with pytest.raises(Deferred):  # not sent: no second try, no retry
        fetcher.get(f"{portal}/Meeting.aspx?Id=2")
    assert fetcher.get("https://webapi.legistar.com/v1/x/events") == b"%PDF"  # other hosts carry on
    assert len(sent) == 3 and fetcher.requests == 3 and fetcher.deferred == 0


@pytest.mark.parametrize("status, mitigated", [(429, "challenge"), (403, "deny")])
def test_takes_vercels_challenge_or_block_as_a_bot_check(status, mitigated):
    sent = []

    def handler(request):
        sent.append(str(request.url))
        return httpx.Response(status, headers={"x-vercel-mitigated": mitigated}, text="Vercel Security Checkpoint")

    fetcher, slept = make(handler)
    site = "https://site.boardable.com/foothill-de-anza-community-college-district"
    with pytest.raises(BotCheck, match="bot check"):
        fetcher.get(site)
    with pytest.raises(Deferred):  # not sent
        fetcher.get(f"{site}/meetings")
    assert len(sent) == 1 and slept == [] and fetcher.refused == {"site.boardable.com"}  # no retry of the 429


# Imperva's block pages, cut down. Both come as ordinary HTML with a 200 status.
IMPERVA_PARDON = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pardon Our Interruption</title>
</head>
<body>
<h1>Pardon Our Interruption</h1>
<p>As you were browsing something about your browser made us think you were a bot.</p>
<script src="/_Incapsula_Resource?SWJIYLWA=719d34d31c8e3a6e6fffd425f7e032f3"></script>
</body>
</html>"""
INCAPSULA_UNSUCCESSFUL = """<html style="height:100%"><head><META NAME="ROBOTS" CONTENT="NOINDEX, NOFOLLOW"></head>
<body style="margin:0px;height:100%"><iframe id="main-iframe" src="/_Incapsula_Resource?CWUDNSAI=24"
frameborder=0 width="100%" height="100%">Request unsuccessful. Incapsula incident ID: 0-0</iframe></body></html>"""
# A normal page on an Imperva host also loads the _Incapsula_Resource script.
SIMBLI_LISTING = """<!DOCTYPE html>
<html><head><title>Meeting Listing</title>
<script src="/_Incapsula_Resource?SWJIYLWA=719d34d31c8e3a6e6fffd425f7e032f3"></script></head>
<body><table><tr><td><a href="javascript:ViewMeeting('36030528','84410','1')">10/14/2026 - 06:30 PM</a></td>
<td>Regular Meeting</td></tr></table></body></html>"""
SIMBLI = "https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx"


@pytest.mark.parametrize("block_page", [IMPERVA_PARDON, INCAPSULA_UNSUCCESSFUL])
def test_takes_impervas_block_page_as_a_bot_check(block_page):
    sent = []

    def handler(request):
        sent.append(str(request.url))
        if request.url.host == "simbli.eboardsolutions.com":
            return httpx.Response(200, html=block_page)
        return httpx.Response(200, json=[])

    fetcher, _ = make(handler)
    with pytest.raises(BotCheck, match="bot check"):
        fetcher.get(SIMBLI, {"S": "36030309"})
    with pytest.raises(Deferred):  # not sent
        fetcher.get(SIMBLI, {"S": "36030816"})
    assert fetcher.get_json("https://webapi.legistar.com/v1/x/events") == []  # other hosts carry on
    assert len(sent) == 2 and fetcher.refused == {"simbli.eboardsolutions.com"}


def test_a_normal_page_on_an_imperva_host_is_not_a_bot_check():
    answers = iter([httpx.Response(200, html=SIMBLI_LISTING), httpx.Response(200, html=SIMBLI_LISTING),
                    # Only HTML is read for the block page, not a JSON answer that quotes it.
                    httpx.Response(200, json={"Title": "<title>Pardon Our Interruption</title>"})])
    fetcher, _ = make(lambda r: next(answers))
    assert b"ViewMeeting" in fetcher.get(SIMBLI, {"S": "36030528"})
    assert b"ViewMeeting" in fetcher.get(SIMBLI, {"S": "36030683"})
    assert fetcher.get_json("https://simbli.eboardsolutions.com/Services/api/GetMeeting/")["Title"]
    assert fetcher.refused == set() and fetcher.requests == 3


def test_waits_ten_seconds_between_requests_to_an_escribe_portal():
    fetcher, slept = make(lambda r: httpx.Response(200, content=b"x"))
    fetcher.get("https://pub-campbell.escribemeetings.com/Meeting.aspx?Id=1")
    fetcher.get("https://pub-campbell.escribemeetings.com/FileStream.ashx?DocumentId=2053")
    assert slept == [10.0]
