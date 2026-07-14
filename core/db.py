"""SQLite state layer. Every fetched item lands here; anything with an unseen
ID is flagged new. Per-source run metadata feeds the dashboard health panel."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    category   TEXT NOT NULL,
    title      TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    date       TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    geometry   TEXT,
    tags       TEXT NOT NULL DEFAULT '[]',
    raw        TEXT,
    -- 1 = inserted while establishing the source's baseline (first fetch).
    -- Baseline records are reference state, not activity: undated ones stay
    -- out of the feed instead of flooding it on day one.
    baseline   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen);

-- One row per source: current health, shown on the dashboard.
CREATE TABLE IF NOT EXISTS source_health (
    source        TEXT PRIMARY KEY,
    last_attempt  TEXT,
    last_success  TEXT,
    last_error    TEXT,
    item_count    INTEGER NOT NULL DEFAULT 0,
    new_count     INTEGER NOT NULL DEFAULT 0,
    note          TEXT
);

-- Append-only log of every fetch attempt, for debugging.
CREATE TABLE IF NOT EXISTS run_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    source     TEXT NOT NULL,
    ok         INTEGER NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    new_count  INTEGER NOT NULL DEFAULT 0,
    error      TEXT
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_items(self, items: list[Item], *, baseline_run: bool = False) -> list[str]:
        """Insert unseen items (they become `new`); refresh mutable fields on
        known items without touching first_seen. Returns the new IDs."""
        now = utcnow()
        new_ids: list[str] = []
        cur = self.conn.cursor()
        for it in items:
            row = cur.execute("SELECT 1 FROM items WHERE id = ?", (it.id,)).fetchone()
            geometry = json.dumps(it.geometry) if it.geometry else None
            tags = json.dumps(it.tags)
            raw = json.dumps(it.raw) if it.raw is not None else None
            if row is None:
                cur.execute(
                    "INSERT INTO items (id, source, category, title, summary, url,"
                    " date, first_seen, geometry, tags, raw, baseline)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (it.id, it.source, it.category, it.title, it.summary, it.url,
                     it.date, now, geometry, tags, raw, int(baseline_run)))
                new_ids.append(it.id)
            else:
                cur.execute(
                    "UPDATE items SET title = ?, summary = ?, url = ?, date = ?,"
                    " geometry = ?, tags = ?, raw = ? WHERE id = ?",
                    (it.title, it.summary, it.url, it.date, geometry, tags, raw, it.id))
        self.conn.commit()
        return new_ids

    def source_has_items(self, source: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM items WHERE source = ? LIMIT 1",
                                (source,)).fetchone()
        return row is not None

    def add_tag(self, ids: list[str], tag: str) -> None:
        cur = self.conn.cursor()
        for item_id in ids:
            row = cur.execute("SELECT tags FROM items WHERE id = ?", (item_id,)).fetchone()
            if row:
                tags = json.loads(row["tags"])
                if tag not in tags:
                    tags.append(tag)
                    cur.execute("UPDATE items SET tags = ? WHERE id = ?",
                                (json.dumps(sorted(tags)), item_id))
        self.conn.commit()

    def record_run(self, source: str, *, ok: bool, item_count: int = 0,
                   new_count: int = 0, error: str | None = None,
                   note: str | None = None) -> None:
        now = utcnow()
        cur = self.conn.cursor()
        cur.execute("INSERT INTO run_log (ts, source, ok, item_count, new_count, error)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (now, source, int(ok), item_count, new_count, error))
        cur.execute("INSERT INTO source_health (source) VALUES (?)"
                    " ON CONFLICT(source) DO NOTHING", (source,))
        if ok:
            cur.execute("UPDATE source_health SET last_attempt = ?, last_success = ?,"
                        " last_error = NULL, item_count = ?, new_count = ?, note = ?"
                        " WHERE source = ?",
                        (now, now, item_count, new_count, note, source))
        else:
            # keep last_success and item_count from the last good run visible
            cur.execute("UPDATE source_health SET last_attempt = ?, last_error = ?,"
                        " new_count = 0, note = ? WHERE source = ?",
                        (now, error, note, source))
        self.conn.commit()

    def health(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        return [dict(r) for r in rows]

    def items_for_site(self, *, feed_days: int, max_items: int) -> list[dict]:
        """Feed items: recent by the item's own date; undated items only when
        they appeared after their source's baseline was established (a
        baseline import of hundreds of old records is reference state, not
        activity). Priority and open-comment items always show. `raw` excluded."""
        cutoff = f"-{feed_days} days"
        rows = self.conn.execute(
            "SELECT id, source, category, title, summary, url, date, first_seen,"
            " geometry IS NOT NULL AS has_geometry, tags FROM items"
            " WHERE (CASE WHEN date != '' THEN date >= date('now', ?)"
            "        ELSE first_seen >= datetime('now', ?) AND baseline = 0 END)"
            "    OR tags LIKE '%\"priority\"%'"
            "    OR tags LIKE '%\"comment-open\"%'"
            " ORDER BY COALESCE(NULLIF(date, ''), substr(first_seen, 1, 10)) DESC, first_seen DESC"
            " LIMIT ?",
            (cutoff, cutoff, max_items)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"])
            d["has_geometry"] = bool(d["has_geometry"])
            out.append(d)
        return out

    def geometry_features(self, sources: list[str]) -> list[dict]:
        """GeoJSON features for the map, one per item that has geometry."""
        marks = ",".join("?" * len(sources))
        rows = self.conn.execute(
            f"SELECT id, source, category, title, url, date, first_seen, geometry, tags"
            f" FROM items WHERE geometry IS NOT NULL AND source IN ({marks})",
            sources).fetchall()
        feats = []
        for r in rows:
            feats.append({
                "type": "Feature",
                "geometry": json.loads(r["geometry"]),
                "properties": {
                    "id": r["id"], "source": r["source"], "category": r["category"],
                    "title": r["title"], "url": r["url"], "date": r["date"],
                    "first_seen": r["first_seen"], "tags": json.loads(r["tags"]),
                },
            })
        return feats

    def close(self) -> None:
        self.conn.close()
