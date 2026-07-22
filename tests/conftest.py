"""Shared test plumbing: a fake HTTP client that serves saved fixtures of
real fetched data (captured 2026-07-13 during the build; congress_bills.json
is synthetic — see test_congress), and a small watch area surrounding the
fixture mining claims."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.context import RunContext          # noqa: E402
from core.geo import WatchArea               # noqa: E402
from run import load_config                  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload: bytes):
        self.content = payload

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class FakeClient:
    """Routes requests to fixture files by URL substring. Records calls."""

    def __init__(self, routes: dict[str, str | bytes]):
        self.routes = routes
        self.calls: list[str] = []

    def _resolve(self, url: str) -> FakeResponse:
        self.calls.append(url)
        for pattern, target in self.routes.items():
            if pattern in url:
                if isinstance(target, bytes):
                    return FakeResponse(target)
                return FakeResponse((FIXTURES / target).read_bytes())
        raise AssertionError(f"no fixture route for {url}")

    def get(self, url, **kw):
        return self._resolve(url)

    def get_json(self, url, **kw):
        return self._resolve(url).json()

    def post(self, url, **kw):
        return self._resolve(url)

    def allowed_by_robots(self, url) -> bool:
        return True


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def watch_area():
    """Two squares standing in for the monuments; the 'Bears Ears' square
    covers the area of the fixture claims (~-110.3..-110.1, 37.55..37.65)."""
    def square(name, x0, y0, x1, y1):
        return {"type": "Feature",
                "properties": {"NCA_NAME": name},
                "geometry": {"type": "Polygon", "coordinates": [[
                    [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}
    gj = {"type": "FeatureCollection", "features": [
        square("Bears Ears", -110.6, 37.2, -109.4, 38.5),
        square("Grand Staircase-Escalante", -112.5, 37.0, -111.0, 38.0),
    ]}
    return WatchArea(gj, "test watch area")


def make_ctx(config, routes, watch_area=None) -> RunContext:
    return RunContext(config=config, client=FakeClient(routes), watch_area=watch_area)
