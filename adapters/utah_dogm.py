"""Utah Division of Oil, Gas and Mining well records for the watch counties.

Source: the division's machine-readable data dump (Wells.zip -> Wells.csv,
refreshed daily; verified 2026-07-13). This is preferred over the Live Data
Search JSF app, which needs stateful ViewState postbacks. The CSV carries
every well statewide with county, status, operator, lease and coordinates.

A well's status is part of the item ID, so a permit turning into a drilling
well (or a well un-plugging) emits a new item.
"""
from __future__ import annotations

import csv
import io
import zipfile

from core.context import EmptyPayload, RunContext
from core.models import Item

SOURCE = "utah_dogm"
ZERO_ITEMS_OK = False   # San Juan county alone always has wells on file

WELL_STATUS = {
    "APD": "approved permit (not yet drilled)", "LA": "location abandoned",
    "DRL": "drilling", "OPS": "drilling operations suspended",
    "P": "producing", "S": "shut-in", "PA": "plugged & abandoned",
    "TA": "temporarily abandoned", "A": "active", "I": "inactive",
    "NEW": "new permit application", "RET": "returned APD",
}
WELL_TYPE = {
    "OW": "oil well", "GW": "gas well", "OGW": "oil & gas well", "D": "dry hole",
    "WI": "water injection", "WD": "water disposal", "GI": "gas injection",
    "GS": "gas storage", "WS": "water supply", "TW": "test well",
}


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    counties = {c.upper() for c in cfg["counties"]}
    resp = ctx.client.get(cfg["wells_url"], timeout=180)

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise EmptyPayload("Wells.zip contained no CSV — export format changed?")
        with z.open(csv_names[0]) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
            rows = [r for r in reader if (r.get("CountyName") or "").upper() in counties]

    if not rows:
        raise EmptyPayload("0 wells in the watch counties — column names changed?")
    return [item for item in (_to_item(ctx, r) for r in rows) if item]


def _to_item(ctx: RunContext, r: dict) -> Item | None:
    api = (r.get("API") or "").strip()
    if not api:
        return None
    status = (r.get("wellstatus") or "").strip()
    wtype = (r.get("welltype") or "").strip()
    name = (r.get("WellName") or "(unnamed well)").strip()
    operator = (r.get("Operator") or "").strip().strip('"')
    county = (r.get("CountyName") or "").strip().title()

    status_label = WELL_STATUS.get(status, status or "unknown status")
    type_label = WELL_TYPE.get(wtype, wtype or "well")
    summary = f"{type_label}, {status_label} — {operator}, {county} County"
    lease = (r.get("LeaseNumber") or "").strip()
    if lease and lease != "NULL":
        summary += f", lease {lease}"

    # point geometry, kept only when the well is inside the watch area AND
    # not permanently dead — the map shows live/pending wells, not the
    # hundreds of historic plugged holes (those stay in the feed/DB)
    geometry = None
    tags = [county.lower().replace(" ", "-") + "-county"]
    try:
        lon, lat = float(r.get("Longitude") or ""), float(r.get("Latitude") or "")
        point = {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}
        if ctx.watch_area is not None and ctx.watch_area.intersects(point):
            tags += ctx.watch_area.monument_tags_for(point)
            if status not in ("PA", "LA", "RET"):
                geometry = point
    except (TypeError, ValueError):
        pass

    date = ""
    first_prod = (r.get("FirstProdDate") or "").strip()
    if first_prod and first_prod != "NULL":
        date = first_prod[:10]

    return Item(
        id=f"{SOURCE}:well:{api}:{status.lower() or 'none'}",
        source=SOURCE, category="leasing",
        title=f"{name} ({api}) — {status_label}",
        summary=summary,
        url="https://oilgas.ogm.utah.gov/oilgasweb/live-data-search/lds-well/well-lu.xhtml",
        date=date,
        geometry=geometry,
        tags=sorted(set(tags)),
        raw={k: r.get(k) for k in ("WellName", "API", "Operator", "FieldName",
                                   "CountyName", "wellstatus", "welltype",
                                   "LeaseNumber", "LeaseType", "SurfaceOwner")},
    )
