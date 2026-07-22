"""The orchestrator's failure isolation and secret hygiene."""
from __future__ import annotations

import types
from datetime import datetime

from core.context import RunContext
from core.db import Database
from core.models import Item
from run import main, redact_secrets, run_source


def test_unknown_source_exits_cleanly():
    """Regression: a typo'd --source used to raise KeyError before the
    friendly validation ran."""
    assert main(["--source", "definitely-not-a-source"]) == 2


def test_api_keys_scrubbed_from_error_messages():
    msg = ("500 Server Error for url: https://api.congress.gov/v3/bill"
           "?api_key=SECRET123&format=json plus Token abc")
    scrubbed = redact_secrets(msg)
    assert "SECRET123" not in scrubbed
    assert "api_key=REDACTED" in scrubbed


def test_degradation_warning_reaches_health_note_scrubbed(tmp_path):
    """End-to-end: an adapter that salvages a partial outage reports it via
    ctx.warnings; the note must land on the health row (ok run, no error),
    key-scrubbed like any published text, and clear on the next healthy run."""
    db = Database(tmp_path / "t.db")
    ctx = RunContext(config={}, client=None)
    item = Item(id="testsrc:doc:1", source="testsrc", category="news",
                title="t", url="u", date="2099-01-01")

    def degraded_fetch(_):
        ctx.warnings["testsrc"] = ("degraded: feed https://x.gov/"
                                   "?api_key=SECRET123 failed")
        return [item]
    module = types.SimpleNamespace(SOURCE="testsrc", ZERO_ITEMS_OK=True,
                                   fetch=degraded_fetch)
    assert run_source("testsrc", module, ctx=ctx, db=db)["status"] == "ok"
    row = db.health()[0]
    assert row["note"].startswith("degraded:")
    assert "SECRET123" not in row["note"]
    assert row["last_error"] is None

    module.fetch = lambda _: [item]          # next run: fully healthy
    run_source("testsrc", module, ctx=ctx, db=db)
    assert db.health()[0]["note"] is None


def test_degraded_note_shows_yellow():
    """A partially-failed multi-feed source reports a degraded note; the
    health panel must show it yellow, not a falsely-clean green."""
    from core.sitegen import health_status
    row = {"note": "degraded: all 3 org feeds failed: boom", "last_error": None,
           "last_success": "2099-01-01T00:00:00Z", "item_count": 5}
    assert health_status(row, stale_after_days=3) == "yellow"


def test_priority_on_new_tags_after_baseline(tmp_path):
    """PRIORITY_ON_NEW sources: new items are tagged in the same transaction
    as their insert (no separate write to lose), and only once the source's
    baseline exists — the first fetch must not scream priority."""
    import json
    db = Database(tmp_path / "t.db")
    def mk(n):
        return Item(id=f"testsrc:doc:{n}", source="testsrc", category="news",
                    title="t", url="u", date="2099-01-01")
    module = types.SimpleNamespace(SOURCE="testsrc", ZERO_ITEMS_OK=True,
                                   PRIORITY_ON_NEW=True,
                                   fetch=lambda ctx: [mk(1)])
    run_source("testsrc", module, ctx=None, db=db)   # baseline run: no tag
    module.fetch = lambda ctx: [mk(1), mk(2)]
    run_source("testsrc", module, ctx=None, db=db)   # doc:2 is genuinely new
    rows = {r["id"]: json.loads(r["tags"])
            for r in db.conn.execute("SELECT id, tags FROM items")}
    assert "priority" not in rows["testsrc:doc:1"]
    assert "priority" in rows["testsrc:doc:2"]


def test_storage_failure_is_contained(tmp_path):
    """Regression: an adapter payload that fetches fine but fails to persist
    (unserializable raw) used to escape run_source and kill the whole run
    with no health record."""
    db = Database(tmp_path / "t.db")
    bad = Item(id="testsrc:doc:1", source="testsrc", category="news",
               title="t", url="u", date="",
               raw={"when": datetime(2026, 7, 13)})   # json.dumps -> TypeError
    module = types.SimpleNamespace(SOURCE="testsrc", ZERO_ITEMS_OK=True,
                                   fetch=lambda ctx: [bad])
    result = run_source("testsrc", module, ctx=None, db=db)
    assert result["status"] == "failed"
    health = db.health()[0]
    assert health["last_error"]           # the failure is on the record
    assert health["last_attempt"]         # and the attempt was logged
