"""Active mining claims and oil & gas leases inside the watch area, from the
BLM MLRS national ArcGIS layers (verified live 2026-07-13).

Query strategy: one bounding-box query per layer (paginated, 2000 records per
page), then a precise local intersect test against the watch-area polygons
with shapely. The bbox spans both monuments so the raw pull includes claims in
the gap between them; the local filter trims those.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.context import EmptyPayload, RunContext
from core.models import Item

SOURCE = "mining_claims"
ZERO_ITEMS_OK = False   # there are always active claims in the watch area
PRIORITY_ON_NEW = True  # a case number never seen before is the core signal

PAGE_SIZE = 2000
MAX_PAGES = 25          # 50k features per layer is far beyond current reality

# MLRS serial case types, prefix-mapped (BLM case-type numbering).
CLAIM_TYPES = {"3841": "lode claim", "3842": "placer claim",
               "3843": "mill site", "3844": "tunnel site"}


def fetch(ctx: RunContext) -> list[Item]:
    if ctx.watch_area is None:
        raise RuntimeError("watch-area boundaries unavailable; cannot spatially filter")
    cfg = ctx.source_config(SOURCE)
    items: list[Item] = []
    items += _fetch_layer(ctx, cfg["claims_layer"], kind="claim", category="mining-claims")
    items += _fetch_layer(ctx, cfg["leases_layer"], kind="lease", category="leasing")
    return items


def _fetch_layer(ctx: RunContext, url: str, *, kind: str, category: str) -> list[Item]:
    bbox = ctx.watch_area.bbox
    features: list[dict] = []
    offset = 0
    for _ in range(MAX_PAGES):
        data = ctx.client.get_json(url, params={
            "geometry": f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        })
        page = data.get("features", [])
        features.extend(page)
        # ArcGIS may return fewer than the requested count even when more
        # remain; only its own flag (or an empty page) means we're done
        if not page or not (data.get("exceededTransferLimit")
                            or (data.get("properties") or {}).get("exceededTransferLimit")
                            or len(page) >= PAGE_SIZE):
            break
        offset += len(page)
    if not features:
        raise EmptyPayload(f"{kind} layer returned no features for the watch bbox")

    items: list[Item] = []
    seen: set[str] = set()
    for feat in features:
        geom = feat.get("geometry")
        if not geom or not ctx.watch_area.intersects(geom):
            continue
        attrs = feat.get("properties", {})
        # The claims layer is pre-filtered to not-closed, but the oil & gas
        # layer includes thousands of historic leases; only live ones signal.
        if kind == "lease" and (attrs.get("CSE_DISP") or "") == "Closed":
            continue
        case_nr = attrs.get("CSE_NR")
        if not case_nr or case_nr in seen:
            continue  # de-duplicate within source (multi-polygon cases repeat)
        seen.add(case_nr)
        items.append(_to_item(ctx, kind, category, case_nr, attrs, geom))
    return items


def _to_item(ctx: RunContext, kind: str, category: str, case_nr: str,
             attrs: dict, geom: dict) -> Item:
    name = (attrs.get("CSE_NAME") or "").strip()
    disp = (attrs.get("CSE_DISP") or "").strip()
    acres = attrs.get("RCRD_ACRS")
    type_nr = str(attrs.get("CSE_TYPE_NR") or "")
    type_label = next((v for k, v in CLAIM_TYPES.items() if type_nr.startswith(k)),
                      "mining claim" if kind == "claim" else "oil & gas lease")

    parts = [type_label, disp.lower()]
    if acres:
        parts.append(f"{acres} acres")
    commodity = (attrs.get("CMMDTY") or "").strip()
    if commodity:
        parts.append(commodity.lower())
    summary = ", ".join(p for p in parts if p)

    date = _best_date(attrs)
    tags = ctx.watch_area.monument_tags_for(geom)
    if disp:
        tags.append(disp.lower().replace(" ", "-"))

    # MLRS public record lookup by serial number
    url = f"https://mlrs.blm.gov/s/global-search/{case_nr}"
    title = f"{name} ({case_nr})" if name else case_nr

    # strip bulky/duplicative fields from raw; geometry is stored separately
    raw = {k: v for k, v in attrs.items() if k not in ("Shape__Length", "Shape__Area")}

    return Item(
        id=f"{SOURCE}:{kind}:{case_nr}",
        source=SOURCE,
        category=category,
        title=title,
        summary=summary,
        url=url,
        date=date,
        geometry=_round_coords(geom),
        tags=sorted(set(tags)),
        raw=raw,
    )


def _best_date(attrs: dict) -> str:
    """Prefer the lease effective / sale date, else the MLRS record creation
    date. Values are epoch milliseconds."""
    for field in ("EFF_DT", "SALE_DT", "Created"):
        ms = attrs.get(field)
        if ms:
            try:
                return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                continue
    return ""


def _round_coords(geom: dict, places: int = 5) -> dict:
    """~1 m precision; keeps the site geometry file small."""
    def rnd(x):
        if isinstance(x, (int, float)):
            return round(x, places)
        return [rnd(v) for v in x]
    return {"type": geom["type"], "coordinates": rnd(geom["coordinates"])}
