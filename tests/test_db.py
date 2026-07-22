"""Change detection: ID stability, new-item flagging, baseline semantics."""
from __future__ import annotations

import sqlite3

import pytest

from core.db import Database
from core.models import Item


def make_item(n: int, **kw) -> Item:
    defaults = dict(
        id=f"testsrc:doc:{n}", source="testsrc", category="news",
        title=f"item {n}", url=f"https://example.gov/{n}", date="",
    )
    defaults.update(kw)
    return Item(**defaults)


def fresh_db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


def test_second_upsert_yields_zero_new(tmp_path):
    db = fresh_db(tmp_path)
    items = [make_item(i) for i in range(5)]
    assert len(db.upsert_items(items)) == 5
    assert db.upsert_items(items) == []          # same IDs -> nothing new


def test_only_unseen_ids_flagged_new(tmp_path):
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(i) for i in range(3)])
    new = db.upsert_items([make_item(i) for i in range(4)])
    assert new == ["testsrc:doc:3"]


def test_update_refreshes_fields_but_keeps_first_seen(tmp_path):
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1, title="old title")])
    before = db.conn.execute("SELECT first_seen FROM items").fetchone()[0]
    db.upsert_items([make_item(1, title="new title")])
    row = db.conn.execute("SELECT title, first_seen FROM items").fetchone()
    assert row["title"] == "new title"
    assert row["first_seen"] == before


def test_undated_baseline_items_stay_out_of_feed(tmp_path):
    db = fresh_db(tmp_path)
    # first fetch of a source: hundreds of undated records = reference state
    db.upsert_items([make_item(i) for i in range(10)], baseline_run=True)
    assert db.items_for_site(feed_days=90, max_items=100) == []
    # a later run finds one more: that IS activity
    db.upsert_items([make_item(i) for i in range(11)])
    feed = db.items_for_site(feed_days=90, max_items=100)
    assert [i["id"] for i in feed] == ["testsrc:doc:10"]


def test_dated_items_use_their_own_date(tmp_path):
    db = fresh_db(tmp_path)
    db.upsert_items([
        make_item(1, date="2020-01-01"),            # old by its own date
        make_item(2, date="2099-01-01"),            # recent (future-proof test)
    ], baseline_run=True)
    ids = [i["id"] for i in db.items_for_site(feed_days=90, max_items=100)]
    assert ids == ["testsrc:doc:2"]


def test_priority_items_always_in_feed(tmp_path):
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1, date="2020-01-01", tags=["priority"])],
                    baseline_run=True)
    ids = [i["id"] for i in db.items_for_site(feed_days=90, max_items=100)]
    assert ids == ["testsrc:doc:1"]


def test_add_tag(tmp_path):
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1, tags=["a"])])
    db.add_tag(["testsrc:doc:1"], "priority")
    feed = db.items_for_site(feed_days=90, max_items=10)
    assert feed and set(feed[0]["tags"]) == {"a", "priority"}


def test_priority_tag_survives_refetch(tmp_path):
    """Regression: the UPDATE path used to overwrite tags with the adapter's
    list, silently deleting the db-added priority tag after one run."""
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1, tags=["active"])])
    db.add_tag(["testsrc:doc:1"], "priority")
    db.upsert_items([make_item(1, tags=["active", "bears-ears"])])   # re-fetch
    row = db.conn.execute("SELECT tags FROM items").fetchone()
    import json
    assert set(json.loads(row["tags"])) == {"active", "bears-ears", "priority"}


def test_skip_never_fabricates_success(tmp_path):
    """Regression: a key-missing skip used to be recorded as a full success,
    setting last_success and zeroing the item count from the last real run."""
    db = fresh_db(tmp_path)
    db.record_run("testsrc", ok=True, item_count=42, new_count=1)
    real_success = db.health()[0]["last_success"]
    db.record_run("testsrc", ok=True, skipped=True, note="add KEY to enable")
    row = db.health()[0]
    assert row["last_success"] == real_success
    assert row["item_count"] == 42
    assert row["note"] == "add KEY to enable"


def test_baseline_established_by_fetch_not_rows(tmp_path):
    """Regression: baseline used to mean 'has stored rows', so a
    legitimately-empty first fetch left the NEXT batch marked baseline and
    (if undated) permanently hidden from the feed."""
    db = fresh_db(tmp_path)
    assert not db.baseline_established("testsrc")
    db.record_run("testsrc", ok=True, skipped=True, note="no key")
    assert not db.baseline_established("testsrc")   # skips don't count
    db.record_run("testsrc", ok=True, item_count=0)  # real, legitimately empty
    assert db.baseline_established("testsrc")
    # the first real item after that empty fetch is activity, not baseline
    db.upsert_items([make_item(1)], baseline_run=False)
    feed = db.items_for_site(feed_days=90, max_items=100)
    assert [i["id"] for i in feed] == ["testsrc:doc:1"]


def test_priority_items_survive_the_feed_limit(tmp_path):
    """Regression: ORDER BY date LIMIT n could silently cut old priority
    items the query contract says always show."""
    db = fresh_db(tmp_path)
    items = [make_item(i, date="2099-01-01") for i in range(10)]
    items.append(make_item(99, date="2000-01-01", tags=["priority"]))
    db.upsert_items(items)
    feed = db.items_for_site(feed_days=90, max_items=5)
    assert "testsrc:doc:99" in [i["id"] for i in feed]


def test_failed_batch_rolls_back_entirely(tmp_path):
    """Regression: a mid-batch failure used to leave the earlier INSERTs
    pending on the shared connection, and record_run's next commit persisted
    them without their ids ever having been returned as new — permanently
    suppressing their alert."""
    db = fresh_db(tmp_path)
    bad = [make_item(1), make_item(2, raw={"unserializable": object()})]
    with pytest.raises(TypeError):
        db.upsert_items(bad)
    db.record_run("testsrc", ok=False, error="boom")   # commits the connection
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    # a clean retry must still flag every item in the batch as new
    assert db.upsert_items([make_item(1), make_item(2)]) == \
        ["testsrc:doc:1", "testsrc:doc:2"]


def test_baseline_flag_commits_with_the_baseline_items(tmp_path):
    """Regression: baseline_established was only written later by record_run;
    a failure between the two commits made the NEXT run a baseline run too,
    silently absorbing that cycle's genuinely-new items."""
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1)], baseline_run=True)
    # simulated crash: record_run never happens — the flag must already be set
    assert db.baseline_established("testsrc")


def test_migration_backfills_baseline(tmp_path):
    """An old-schema database (no baseline_established column) gains it on
    open, with sources that already have items counted as baselined."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE items (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, category TEXT NOT NULL,
            title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '', date TEXT NOT NULL DEFAULT '',
            first_seen TEXT NOT NULL, geometry TEXT,
            tags TEXT NOT NULL DEFAULT '[]', raw TEXT,
            baseline INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE source_health (
            source TEXT PRIMARY KEY, last_attempt TEXT, last_success TEXT,
            last_error TEXT, item_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0, note TEXT);
        CREATE TABLE run_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            source TEXT NOT NULL, ok INTEGER NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0, error TEXT);
        INSERT INTO items (id, source, category, title, first_seen)
            VALUES ('oldsrc:doc:1', 'oldsrc', 'news', 't', '2026-01-01T00:00:00Z');
        INSERT INTO source_health (source) VALUES ('oldsrc');
    """)
    conn.close()
    db = Database(path)
    assert db.baseline_established("oldsrc")
    assert not db.baseline_established("neverseen")


def test_failed_commit_also_rolls_back(tmp_path):
    """Regression: commit() sat outside the rollback guard, so a 'database is
    locked' at commit time left the batch staged for the next record_run
    commit to silently persist — with the ids never reported as new."""
    db = fresh_db(tmp_path)
    db.conn.execute("PRAGMA busy_timeout = 50")
    blocker = sqlite3.connect(tmp_path / "test.db")
    blocker.isolation_level = None
    try:
        blocker.execute("BEGIN")
        blocker.execute("SELECT COUNT(*) FROM items").fetchone()  # shared lock
        with pytest.raises(sqlite3.OperationalError):
            db.upsert_items([make_item(1)])   # staging works; COMMIT is blocked
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    db.record_run("testsrc", ok=False, error="database is locked")
    assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    # the retry must still flag the item as new
    assert db.upsert_items([make_item(1)]) == ["testsrc:doc:1"]


def test_tag_new_applied_atomically_only_to_inserts(tmp_path):
    """Priority tagging rides inside the insert transaction (a separate write
    after the commit could be lost mid-run); items that already exist must
    not pick the tag up on re-fetch."""
    import json
    db = fresh_db(tmp_path)
    db.upsert_items([make_item(1, tags=["a"])])
    new = db.upsert_items([make_item(1, tags=["a"]), make_item(2, tags=["b"])],
                          tag_new="priority")
    assert new == ["testsrc:doc:2"]
    rows = {r["id"]: set(json.loads(r["tags"]))
            for r in db.conn.execute("SELECT id, tags FROM items")}
    assert rows["testsrc:doc:1"] == {"a"}
    assert rows["testsrc:doc:2"] == {"b", "priority"}


def test_health_survives_failure_and_keeps_last_success(tmp_path):
    db = fresh_db(tmp_path)
    db.record_run("testsrc", ok=True, item_count=7, new_count=7)
    good = db.health()[0]
    db.record_run("testsrc", ok=False, error="boom")
    row = db.health()[0]
    assert row["last_error"] == "boom"
    assert row["last_success"] == good["last_success"]   # stale data still dated
    assert row["item_count"] == 7                        # last good count kept
