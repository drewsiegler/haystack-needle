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


def test_waits_ten_seconds_between_requests_to_an_escribe_portal():
    fetcher, slept = make(lambda r: httpx.Response(200, content=b"x"))
    fetcher.get("https://pub-campbell.escribemeetings.com/Meeting.aspx?Id=1")
    fetcher.get("https://pub-campbell.escribemeetings.com/FileStream.ashx?DocumentId=2053")
    assert slept == [10.0]
