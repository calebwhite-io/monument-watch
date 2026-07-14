"""Watch-area geometry: downloads and caches the monument boundary polygons,
and provides the spatial filter used by the mining_claims adapter.

Strategy: ArcGIS layers are queried by bounding box (cheap, reliable), then
each returned feature is precisely tested against the watch-area polygons
locally with shapely. This avoids shipping a huge polygon in every query URL
and gives us the county-free precise filter the spec asks for.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.prepared import prep

log = logging.getLogger("monitor.geo")


def _tag_for_name(name: str) -> str:
    if "bears ears" in name.lower():
        return "bears-ears"
    if "grand staircase" in name.lower():
        return "grand-staircase"
    return name.lower().replace(" ", "-")


class WatchArea:
    def __init__(self, geojson: dict, note: str):
        self.geojson = geojson
        self.note = note
        shapes = [shape(f["geometry"]) for f in geojson["features"]]
        self._union = unary_union(shapes)
        self._prepared = prep(self._union)
        self.bbox = self._union.bounds  # (minx, miny, maxx, maxy)
        # per-monument prepared shapes, for tagging items by location
        self._monuments = [
            (_tag_for_name(f["properties"].get("NCA_NAME", "")), prep(shape(f["geometry"])))
            for f in geojson["features"]
        ]

    def intersects(self, geometry: dict) -> bool:
        try:
            return self._prepared.intersects(shape(geometry))
        except Exception:
            return False

    def monument_tags_for(self, geometry: dict) -> list[str]:
        try:
            geom = shape(geometry)
        except Exception:
            return []
        return [tag for tag, prepared in self._monuments if prepared.intersects(geom)]


def load_watch_area(client, cfg: dict) -> WatchArea:
    """Return the watch-area polygons, downloading and caching on first run.

    The watch area is the *pre-reduction* (2021) footprint of both monuments.
    If `reduced_published` is true the reduced file is ALSO loaded for the map,
    but the watch/filter area stays the full 2021 footprint — activity on
    excluded land is the whole point of this tool.
    """
    cache = Path(cfg["cache_file"])
    if not cache.exists():
        log.info("downloading monument boundaries to %s", cache)
        names = cfg["monument_names"]
        where = " OR ".join(f"NCA_NAME LIKE '%{n}%'" for n in names)
        resp = client.get(cfg["layer_query_url"], params={
            "where": where, "outFields": "NCA_NAME,NLCS_ID", "f": "geojson",
        })
        gj = resp.json()
        if not gj.get("features"):
            raise RuntimeError("boundary layer returned no features; cannot establish watch area")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(gj), encoding="utf-8")
    gj = json.loads(cache.read_text(encoding="utf-8"))
    note = ("2021 (pre-reduction) boundaries — the full watch area"
            if not cfg.get("reduced_published")
            else "2021 boundaries (watch area); reduced boundaries overlaid")
    return WatchArea(gj, note)


def load_reduced_boundaries(cfg: dict) -> dict | None:
    """The post-proclamation boundaries, once the user drops the file in."""
    if cfg.get("reduced_published"):
        path = Path(cfg["reduced_file"])
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        log.warning("reduced_published is true but %s does not exist", path)
    return None
