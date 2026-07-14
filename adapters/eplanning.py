"""BLM NEPA projects for the watch-area offices, from the National NEPA
Register (the ePlanning replacement at eplanning.blm.gov).

Endpoint discovery (2026-07-13): the register is a Microsoft Power Pages
portal; its search grid loads from `POST /searchresults/` speaking the
DataTables server-side protocol with all parameters in the query string.
Quirk: the server returns rows only up to `filter_total_count`, so the
adapter first asks for the count (`get_total_count=true`), then re-requests
with that count to download every row. If BLM restructures the portal this
adapter fails loudly (red health row) rather than parsing garbage.

Status changes matter (a project entering "comment period open" is news even
if the project is old), so the NEPA status is part of the item ID: a status
change yields a new ID and therefore a new item.
"""
from __future__ import annotations

import html

from core.context import EmptyPayload, RunContext
from core.models import Item, monument_tags

SOURCE = "eplanning"
ZERO_ITEMS_OK = False   # these offices always have projects on file


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    items: dict[str, Item] = {}

    for office in cfg["offices"]:
        rows = _search_office(ctx, cfg, office)
        for row in rows:
            item = _to_item(row, cfg, monuments)
            if item and item.id not in items:
                items[item.id] = item

    if not items:
        raise EmptyPayload("NEPA register returned no projects for any office")
    return list(items.values())


def _search_office(ctx: RunContext, cfg: dict, office: str) -> list[dict]:
    base_params = {
        "states": cfg["state"],
        "offices": office,
        "order_attribute": "0",
        "order_direction": "desc",
    }
    # step 1: learn the filtered row count
    count_resp = ctx.client.post(cfg["search_url"], params={
        **base_params, "download": "true",
        "get_total_count": "true", "filter_total_count": "0",
    }, headers={"Accept": "application/json"}, cache=False)
    total = count_resp.json().get("recordsFiltered") or 0
    if not total:
        return []
    # step 2: download exactly that many rows
    rows_resp = ctx.client.post(cfg["search_url"], params={
        **base_params, "download": "true",
        "get_total_count": "false", "filter_total_count": str(total),
    }, headers={"Accept": "application/json"}, cache=False)
    return rows_resp.json().get("data", [])


def _to_item(row: dict, cfg: dict, monuments: dict) -> Item | None:
    project_id = row.get("projectid")
    nepa_number = row.get("nepanumber") or ""
    if not project_id and not nepa_number:
        return None
    # grid values arrive HTML-escaped ("Analysis &amp; Document Preparation")
    status = html.unescape(row.get("nepastatus") or "").strip()
    name = html.unescape(row.get("projectname") or "(unnamed project)").strip()
    office = html.unescape(row.get("leadoffice") or "").strip()
    program = html.unescape(row.get("program") or "").strip()
    doc_type = html.unescape(row.get("type") or "").strip()

    tags = monument_tags(name, monuments)
    status_slug = status.lower().replace(" ", "-")
    if "comment" in status.lower() or "protest" in status.lower():
        tags.append("comment-open")

    # status in the ID: a project moving to a new phase emits a new item
    stable_key = project_id or nepa_number
    return Item(
        id=f"{SOURCE}:project:{stable_key}:{status_slug}",
        source=SOURCE, category="planning-nepa",
        title=f"{nepa_number or 'NEPA project'}: {name}"
              + (f" — {status}" if status else ""),
        summary=", ".join(p for p in (doc_type, program, office) if p),
        url=f"{cfg['project_url_base']}{project_id}" if project_id
            else "https://eplanning.blm.gov/",
        date="",  # the register's grid carries no dates; first_seen orders the feed
        tags=sorted(set(tags)),
        raw=row,
    )
