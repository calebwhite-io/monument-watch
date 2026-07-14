"""The orchestrator's failure isolation and secret hygiene."""
from __future__ import annotations

import types
from datetime import datetime

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
