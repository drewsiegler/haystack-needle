import httpx

from needle.fetch import USER_AGENT, Fetcher


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
