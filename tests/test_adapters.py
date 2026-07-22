"""Each adapter's parser against saved fixtures of real fetched data
(captured live 2026-07-13) — except congress_bills.json, which is synthetic
(see test_congress). Every test also proves ID determinism by parsing twice
and comparing."""
from __future__ import annotations

import os

import pytest

from adapters import (blm_policy, congress, courtlistener, eplanning,
                      federal_register, lease_sales, mining_claims, news,
                      regulations_gov, sitla, utah_dogm)
from adapters.congress import _chamber_slug, _keyword_in
from tests.conftest import FIXTURES, FakeClient, make_ctx


def ids_twice(module, ctx_factory):
    a = sorted(i.id for i in module.fetch(ctx_factory()))
    b = sorted(i.id for i in module.fetch(ctx_factory()))
    assert a == b, "IDs must be identical across runs"
    return a


def test_federal_register(config):
    def ctx():
        return make_ctx(config, {"federalregister.gov": "federal_register.json"})
    ids = ids_twice(federal_register, ctx)
    assert ids and all(i.startswith("federal_register:doc:") for i in ids)
    items = federal_register.fetch(ctx())
    assert all(it.date and it.url for it in items)


def test_mining_claims(config, watch_area):
    routes = {
        "Mining_Claims_Not_Closed": "mlrs_claims.geojson",
        "Oil_and_Gas_Leases": "mlrs_claims.geojson",  # same shape, lease branch
    }
    def ctx():
        return make_ctx(config, routes, watch_area)
    ids = ids_twice(mining_claims, ctx)
    assert any(i.startswith("mining_claims:claim:UT") for i in ids)
    items = mining_claims.fetch(ctx())
    claim = next(i for i in items if ":claim:" in i.id)
    assert claim.geometry and claim.geometry["type"] == "Polygon"
    assert "bears-ears" in claim.tags          # fixture claims sit in that square
    assert claim.date                          # epoch ms Created -> ISO date


def test_mining_claims_needs_boundaries(config):
    ctx = make_ctx(config, {}, watch_area=None)
    with pytest.raises(RuntimeError):
        mining_claims.fetch(ctx)


def test_courtlistener(config):
    def ctx():
        return make_ctx(config, {"courtlistener.com": "courtlistener_search.json"})
    ids = ids_twice(courtlistener, ctx)
    assert ids and all(i.startswith("courtlistener:docket:") for i in ids)
    items = courtlistener.fetch(ctx())
    assert all(it.url.startswith("https://www.courtlistener.com/") for it in items)
    assert all(it.category == "litigation" for it in items)


def test_congress_keyword_matching():
    assert _keyword_in("bears ears", "A bill about Bears Ears National Monument".lower())
    assert _keyword_in("land exchange utah", "Providing for a land exchange in Utah".lower())
    assert not _keyword_in("land exchange utah", "A land exchange in Nevada".lower())
    assert _chamber_slug("HR") == "house-bill"
    assert _chamber_slug("s") == "senate-bill"


def test_congress(config, monkeypatch):
    """Parser against a synthetic fixture in the documented v3 /bill response
    shape (a live capture still needs a real API key — the shared DEMO_KEY was
    rate-limited at build time). Pins ID format, keyword filtering, category,
    URL construction, and the action-date choice."""
    monkeypatch.setenv("CONGRESS_API_KEY", "TEST_KEY")
    def ctx():
        return make_ctx(config, {"api.congress.gov": "congress_bills.json"})
    ids = ids_twice(congress, ctx)
    assert ids == ["congress:bill:119-hr-5005", "congress:bill:119-s-2200"]
    items = congress.fetch(ctx())
    assert all(i.category == "congress" for i in items)
    bears = next(i for i in items if "hr-5005" in i.id)
    assert bears.url == "https://www.congress.gov/bill/119th-congress/house-bill/5005"
    assert bears.date == "2026-07-09"
    assert "bears-ears" in bears.tags


def test_regulations_gov(config, monkeypatch):
    monkeypatch.setenv("REGS_API_KEY", "TEST_KEY")
    def ctx():
        return make_ctx(config, {"api.regulations.gov": "regulations_docs.json"})
    ids = ids_twice(regulations_gov, ctx)
    assert ids and all(i.startswith("regulations_gov:doc:") for i in ids)


def test_regulations_gov_skips_without_key(config, monkeypatch):
    monkeypatch.delenv("REGS_API_KEY", raising=False)
    from core.context import SkipSource
    with pytest.raises(SkipSource):
        regulations_gov.fetch(make_ctx(config, {}))


def test_news(config):
    routes = {"news.google.com": "google_news.xml", "suwa.org": "org_feed.xml",
              "grandcanyontrust.org": "org_feed.xml",
              "bearsearscoalition.org": "org_feed.xml"}
    def ctx():
        return make_ctx(config, routes)
    ids = ids_twice(news, ctx)
    assert ids and all(i.startswith("news:article:") for i in ids)
    items = news.fetch(ctx())
    tagged = [i for i in items if "bears-ears" in i.tags]
    assert tagged, "Google News fixture is a Bears Ears query; tags expected"


NEWS_ROUTES = {"news.google.com": "google_news.xml", "suwa.org": "org_feed.xml",
               "grandcanyontrust.org": "org_feed.xml",
               "bearsearscoalition.org": "org_feed.xml"}
EMPTY_RSS = b"<?xml version='1.0'?><rss version='2.0'><channel><title>q</title></channel></rss>"


def test_news_partial_google_failure_keeps_going_but_is_reported(config):
    """Regression: one throttled Google query used to abort the adapter; each
    query is independent and the others' items must survive. A persistently
    failing query must still be visible on the health panel, not only in a
    cron log nobody reads."""
    class FirstGoogleCallFails(FakeClient):
        def get(self, url, **kw):
            if "news.google.com" in url and not getattr(self, "_failed", False):
                self._failed = True
                raise ConnectionError("throttled")
            return super().get(url, **kw)

    ctx = make_ctx(config, NEWS_ROUTES)
    ctx.client = FirstGoogleCallFails(NEWS_ROUTES)
    items = news.fetch(ctx)
    assert items and all(i.id.startswith("news:article:") for i in items)
    assert "1/3 Google News queries failed" in ctx.warnings["news"]


def test_news_single_org_feed_outage_is_reported(config):
    """Org feeds are disjoint hosts with shallow RSS windows: one feed dying
    permanently (DNS change, moved URL) must show on the health panel — the
    note names the count so the operator can notice, and it self-clears on
    the next fully-healthy run."""
    class SuwaDown(FakeClient):
        def get(self, url, **kw):
            if "suwa.org" in url:
                raise ConnectionError("dns failure")
            return super().get(url, **kw)

    ctx = make_ctx(config, NEWS_ROUTES)
    ctx.client = SuwaDown(NEWS_ROUTES)
    items = news.fetch(ctx)
    assert items
    assert "1/3 org feeds failed" in ctx.warnings["news"]


def test_news_org_outage_keeps_items_and_degrades(config):
    """Regression: an org feed's outage used to crash the whole adapter and
    discard the other hosts' items; total org failure must also be visible on
    the health panel (yellow), not a silent green."""
    class OrgHostsDown(FakeClient):
        def get(self, url, **kw):
            if "news.google.com" not in url:
                raise ConnectionError("org host unreachable")
            return super().get(url, **kw)

    ctx = make_ctx(config, NEWS_ROUTES)
    ctx.client = OrgHostsDown(NEWS_ROUTES)
    items = news.fetch(ctx)
    assert items and all(i.id.startswith("news:article:") for i in items)
    assert "org feeds failed" in ctx.warnings["news"]


def test_news_google_block_keeps_org_items_and_degrades(config):
    """Regression: an all-queries Google block used to abort before the org
    feeds were fetched — during a multi-day block their shallow RSS windows
    scrolled away unrecorded. Org items must be kept, block shown as yellow."""
    class GoogleBlocked(FakeClient):
        def get(self, url, **kw):
            if "news.google.com" in url:
                raise ConnectionError("blocked")
            return super().get(url, **kw)

    ctx = make_ctx(config, NEWS_ROUTES)
    ctx.client = GoogleBlocked(NEWS_ROUTES)
    items = news.fetch(ctx)
    assert items and all(i.id.startswith("news:article:") for i in items)
    assert "Google News" in ctx.warnings["news"]


def test_news_total_outage_is_a_failure(config):
    """When nothing at all was salvaged, the real exception must surface (red
    on the health panel), never a quiet empty result."""
    class AllDown(FakeClient):
        def get(self, url, **kw):
            raise ConnectionError("everything unreachable")

    ctx = make_ctx(config, NEWS_ROUTES)
    ctx.client = AllDown(NEWS_ROUTES)
    with pytest.raises(ConnectionError):
        news.fetch(ctx)


def test_news_quiet_google_still_detected(config):
    """The docstring's core concern: a UA/IP block that answers with valid but
    empty feeds must not look green. With org items salvaged it degrades to
    yellow; with nothing salvaged it is an EmptyPayload failure."""
    from core.context import EmptyPayload
    routes = dict(NEWS_ROUTES, **{"news.google.com": EMPTY_RSS})
    ctx = make_ctx(config, routes)
    items = news.fetch(ctx)
    assert items                                     # org items kept
    assert "no usable entries" in ctx.warnings["news"]
    config["sources"]["news"]["org_feeds"] = []      # nothing to salvage
    with pytest.raises(EmptyPayload):
        news.fetch(make_ctx(config, routes))


def test_news_org_only_config_works(config):
    """An operator may empty google_news_queries (e.g. Google blocks their
    region); org-feed-only monitoring must still function, quietly green."""
    config["sources"]["news"]["google_news_queries"] = []
    ctx = make_ctx(config, NEWS_ROUTES)
    items = news.fetch(ctx)
    assert items and all(i.id.startswith("news:article:") for i in items)
    assert "news" not in ctx.warnings


def test_eplanning(config):
    def ctx():
        # one fixture serves both the count call and the row download
        return make_ctx(config, {"eplanning.blm.gov": "eplan_results3.json"})
    ids = ids_twice(eplanning, ctx)
    assert len(ids) > 100
    assert all(i.startswith("eplanning:project:") for i in ids)
    items = eplanning.fetch(ctx())
    # status is baked into the ID so a phase change emits a new item
    assert all(i.id.count(":") >= 3 for i in items)
    assert not any("&amp;" in i.id or "&amp;" in i.title for i in items)


def test_lease_sales(config):
    def ctx():
        return make_ctx(config, {"blm.gov": "blm_ut_leases.html"})
    ids = ids_twice(lease_sales, ctx)
    assert len(ids) > 20
    items = lease_sales.fetch(ctx())
    assert any("DOI-BLM-UT" in i.title for i in items)
    assert all(i.category == "leasing" for i in items)


def test_blm_policy(config):
    def ctx():
        return make_ctx(config, {"blm.gov": "blm_im.html"})
    ids = ids_twice(blm_policy, ctx)
    assert ids, "fixture page contains leasing/mineral IMs that match keywords"
    items = blm_policy.fetch(ctx())
    assert all(i.id.startswith("blm_policy:im:") for i in items)
    assert all(i.category == "policy" for i in items)


def test_utah_dogm(config, watch_area):
    wells = (FIXTURES / "wells_mini.zip").read_bytes()
    def ctx():
        return make_ctx(config, {"oilgas.ogm.utah.gov": wells}, watch_area)
    ids = ids_twice(utah_dogm, ctx)
    items = utah_dogm.fetch(ctx())
    # fixture has 4 wells each in SAN JUAN / KANE / GARFIELD / WASHINGTON;
    # Washington County is outside the watch and must be filtered out
    assert len(items) == 12
    assert all(":well:" in i.id for i in items)
    # well status is part of the ID so a status change emits a new item
    assert all(i.id.rsplit(":", 1)[1] for i in items)


def test_sitla(config):
    def ctx():
        return make_ctx(config, {"trustlands.utah.gov": "sitla_posts.json"})
    ids = ids_twice(sitla, ctx)
    assert ids and all(i.startswith("sitla:post:") for i in ids)
    items = sitla.fetch(ctx())
    assert all(i.category == "state-lands" for i in items)
    assert not any("&#8211;" in i.title for i in items)   # entities unescaped


def test_raw_never_reaches_site_json(config, tmp_path):
    """`raw` stays in the DB for debugging but must not bloat the page."""
    from core.db import Database
    from core.models import Item
    db = Database(tmp_path / "t.db")
    db.upsert_items([Item(id="testsrc:doc:1", source="testsrc", category="news",
                          title="t", url="u", date="2099-01-01",
                          raw={"secret_bulk": "x" * 1000})])
    feed = db.items_for_site(feed_days=90, max_items=10)
    assert "raw" not in feed[0]
