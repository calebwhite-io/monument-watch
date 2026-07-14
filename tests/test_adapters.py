"""Each adapter's parser against saved fixtures of real fetched data
(captured live 2026-07-13). Every test also proves ID determinism by parsing
twice and comparing."""
from __future__ import annotations

import os

import pytest

from adapters import (blm_policy, courtlistener, eplanning, federal_register,
                      lease_sales, mining_claims, news, regulations_gov, sitla,
                      utah_dogm)
from adapters.congress import _chamber_slug, _keyword_in
from tests.conftest import FIXTURES, make_ctx


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
