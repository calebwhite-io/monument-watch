"""Regenerates site/data/*.json from the database after every run. The
dashboard is fully static: these files are all it loads."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import mapping, shape

from core.db import Database, utcnow

log = logging.getLogger("monitor.sitegen")

SITE_DATA = Path("site/data")

# Sources whose items carry map geometry.
GEOMETRY_SOURCES = ["mining_claims", "utah_dogm"]


def health_status(row: dict, stale_after_days: int) -> str:
    """green = last attempt succeeded with data; yellow = empty payload
    (possible format change), a degraded run (part of a multi-feed source
    failed — the note says which), stale data, or waiting on an API key;
    red = last attempt errored outright. Stale/failed sources keep showing
    their last good data date so old information is never silently presented
    as current."""
    note = (row.get("note") or "").lower()
    if "to enable" in note or "api key" in note or "api_key" in note:
        return "yellow"   # waiting on a key: not broken, but not fetching either
    if "format change" in note:
        return "yellow"   # answered 200 but empty — per spec, yellow not red
    if note.startswith("degraded"):
        return "yellow"   # run stored items, but part of the source failed
    if row["last_error"]:
        return "red"
    if not row["last_success"]:
        return "yellow"
    age = datetime.now(timezone.utc) - datetime.strptime(
        row["last_success"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if age > timedelta(days=stale_after_days):
        return "yellow"
    if row["item_count"] == 0 and (row.get("note") or "").startswith("0 items"):
        return "yellow"
    return "green"


def generate(db: Database, config: dict, watch_geojson: dict, boundary_note: str,
             reduced_geojson: dict | None) -> dict:
    site_cfg = config["site"]
    SITE_DATA.mkdir(parents=True, exist_ok=True)

    items = db.items_for_site(feed_days=site_cfg["feed_days"],
                              max_items=site_cfg["max_feed_items"])
    health = []
    for row in db.health():
        row["status"] = health_status(row, site_cfg["stale_after_days"])
        health.append(row)

    features = db.geometry_features(GEOMETRY_SOURCES)
    priority_new = [i for i in items if "priority" in i["tags"]
                    and i["first_seen"] >= (datetime.now(timezone.utc) - timedelta(days=7))
                    .strftime("%Y-%m-%dT%H:%M:%SZ")]

    meta = {
        "generated_at": utcnow(),
        "boundary_note": boundary_note,
        "reduced_boundaries_available": reduced_geojson is not None,
        "priority_new_count": len(priority_new),
        "manual_checks": config.get("manual_checks", []),
    }

    boundaries = _simplify_collection(watch_geojson)
    feature_collection = {"type": "FeatureCollection", "features": features}

    _write("items.json", {"generated_at": meta["generated_at"], "items": items})
    _write("health.json", health)
    _write("meta.json", meta)
    _write("map_features.geojson", feature_collection)
    _write("boundaries.geojson", boundaries)
    if reduced_geojson:
        _write("boundaries_reduced.geojson", reduced_geojson)

    # Single-script bundle so the dashboard also works opened straight from
    # disk (browsers block fetch() on file:// but allow <script src>).
    bundle = {"meta": meta, "items": items, "health": health,
              "boundaries": boundaries, "features": feature_collection,
              "reduced": reduced_geojson}
    (SITE_DATA / "data.js").write_text(
        "window.MW_DATA = " + json.dumps(bundle, ensure_ascii=False,
                                         separators=(",", ":")) + ";",
        encoding="utf-8")

    log.info("site data written: %d feed items, %d map features", len(items), len(features))
    return {"items": len(items), "features": len(features), "priority_new": len(priority_new)}


def _simplify_collection(geojson: dict, tolerance: float = 0.0005) -> dict:
    """Lighten boundary polygons for the page (~50 m tolerance — invisible at
    monument scale). The full-resolution copy in data/geo/ still drives the
    spatial filter."""
    out = {"type": "FeatureCollection", "features": []}
    for feat in geojson.get("features", []):
        try:
            geom = mapping(shape(feat["geometry"]).simplify(tolerance, preserve_topology=True))
        except Exception:
            geom = feat["geometry"]
        out["features"].append({"type": "Feature", "geometry": geom,
                                "properties": feat.get("properties", {})})
    return out


def _write(name: str, payload) -> None:
    (SITE_DATA / name).write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
