"""Monument Watch — run all source adapters, update the database, regenerate
the static dashboard, and print a summary of new items.

Usage:
    python run.py                  # run everything
    python run.py --source news    # run one adapter (debugging)
    python run.py --list           # show available sources
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

import yaml

from adapters import ADAPTERS
from core import sitegen
from core.context import EmptyPayload, RunContext, SkipSource
from core.db import Database
from core.geo import load_reduced_boundaries, load_watch_area
from core.http import PoliteClient

DB_PATH = "data/monitor.db"

log = logging.getLogger("monitor")


def load_env(path: str = ".env") -> None:
    """Tiny .env loader so users don't need another dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def load_config() -> dict:
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    contact = os.environ.get("CONTACT_EMAIL") or "contact-not-configured@example.com"
    config["user_agent"] = config["user_agent"].format(contact_email=contact)
    return config


def run_source(name: str, module, ctx: RunContext, db: Database) -> dict:
    """Run one adapter. Any exception is caught, logged, and recorded as a
    health failure — one broken source must never take down the run."""
    zero_ok = getattr(module, "ZERO_ITEMS_OK", True)
    # Whether a baseline exists BEFORE this run — on the very first fetch of a
    # source everything is "new", which shouldn't trigger priority alarms.
    baseline = db.source_has_items(name)
    try:
        items = module.fetch(ctx)
    except SkipSource as skip:
        db.record_run(name, ok=True, note=skip.note)
        return {"status": "skipped", "note": skip.note}
    except EmptyPayload as ep:
        db.record_run(name, ok=False, error=str(ep) or "empty payload — possible format change")
        return {"status": "failed", "error": str(ep)}
    except Exception as exc:
        log.error("source %s failed: %s", name, exc)
        log.debug("%s", traceback.format_exc())
        db.record_run(name, ok=False, error=f"{type(exc).__name__}: {exc}"[:500])
        return {"status": "failed", "error": str(exc)}

    if not items and not zero_ok:
        db.record_run(name, ok=False, error="0 items — possible format change",
                      note="0 items from a source that always has data; prior data kept")
        return {"status": "failed", "error": "0 items — possible format change"}

    new_ids = db.upsert_items(items, baseline_run=not baseline)
    # e.g. a mining-claim case number never seen before is the core signal —
    # tag it priority, but only once a baseline exists (not on first fetch).
    if new_ids and baseline and getattr(module, "PRIORITY_ON_NEW", False):
        db.add_tag(new_ids, "priority")
    db.record_run(name, ok=True, item_count=len(items), new_count=len(new_ids))
    return {"status": "ok", "items": len(items), "new": new_ids}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="run a single source adapter")
    parser.add_argument("--list", action="store_true", help="list available sources")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.list:
        for name in ADAPTERS:
            print(name)
        return 0

    load_env()
    config = load_config()
    db = Database(DB_PATH)
    client = PoliteClient(config["user_agent"])
    ctx = RunContext(config=config, client=client)

    # Watch-area boundaries: needed by the spatial filter and the map. A
    # download failure falls back to the cached copy; only a missing cache
    # disables the spatial sources.
    watch_geojson, boundary_note = {"type": "FeatureCollection", "features": []}, ""
    try:
        watch = load_watch_area(client, config["boundaries"])
        ctx.watch_area = watch
        watch_geojson, boundary_note = watch.geojson, watch.note
    except Exception as exc:
        log.error("boundaries unavailable: %s — spatial sources will report failure", exc)
        boundary_note = f"boundaries unavailable this run: {exc}"

    selected = {args.source: ADAPTERS[args.source]} if args.source else ADAPTERS
    if args.source and args.source not in ADAPTERS:
        print(f"unknown source {args.source!r}; try --list", file=sys.stderr)
        return 2

    summary: dict[str, dict] = {}
    for name, module in selected.items():
        if not config["sources"].get(name, {}).get("enabled", False):
            log.info("source %s disabled in config, skipping", name)
            continue
        log.info("running %s ...", name)
        summary[name] = run_source(name, module, ctx, db)

    reduced = load_reduced_boundaries(config["boundaries"])
    stats = sitegen.generate(db, config, watch_geojson, boundary_note, reduced)

    print("\n=== Monument Watch run summary ===")
    total_new = 0
    for name, result in summary.items():
        if result["status"] == "ok":
            new = result["new"]
            total_new += len(new)
            print(f"  {name:18s} ok      {result['items']:5d} items, {len(new)} new")
            for item_id in new[:5]:
                print(f"    + {item_id}")
            if len(new) > 5:
                print(f"    ... and {len(new) - 5} more")
        elif result["status"] == "skipped":
            print(f"  {name:18s} skipped  ({result['note']})")
        else:
            print(f"  {name:18s} FAILED  ({result['error']})")
    print(f"  {'':18s} ------")
    print(f"  total new items: {total_new}; feed items on site: {stats['items']};"
          f" map features: {stats['features']}")
    print("  open site/index.html in a browser to view the dashboard")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
