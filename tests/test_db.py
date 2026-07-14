"""Change detection: ID stability, new-item flagging, baseline semantics."""
from __future__ import annotations

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


def test_health_survives_failure_and_keeps_last_success(tmp_path):
    db = fresh_db(tmp_path)
    db.record_run("testsrc", ok=True, item_count=7, new_count=7)
    good = db.health()[0]
    db.record_run("testsrc", ok=False, error="boom")
    row = db.health()[0]
    assert row["last_error"] == "boom"
    assert row["last_success"] == good["last_success"]   # stale data still dated
    assert row["item_count"] == 7                        # last good count kept
