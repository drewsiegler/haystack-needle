import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeFetcher:
    """Answers get_json from a {url-suffix: response} map and records every call."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response(params) if callable(response) else response
        raise AssertionError(f"no fixture for {url}")


@pytest.fixture
def fixture():
    return load
