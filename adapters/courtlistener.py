"""Monument litigation: CourtListener v4 search across federal dockets, plus
docket-entry polling for cases the user lists in config as suits are filed.

API: https://www.courtlistener.com/help/api/rest/ (verified live 2026-07-13).
Works keyless at a lower rate limit; COURTLISTENER_API_TOKEN raises it.
"""
from __future__ import annotations

import re

from core.context import RunContext
from core.models import Item, monument_tags

SOURCE = "courtlistener"
ZERO_ITEMS_OK = True

SEARCH_API = "https://www.courtlistener.com/api/rest/v4/search/"
ENTRIES_API = "https://www.courtlistener.com/api/rest/v4/docket-entries/"
BASE = "https://www.courtlistener.com"


def _headers(ctx: RunContext) -> dict:
    token = ctx.api_key(SOURCE)
    return {"Authorization": f"Token {token}"} if token else {}


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    items: dict[str, Item] = {}

    # keyword search over RECAP dockets, newest filings first (first page —
    # 20 newest dockets per term is the recent-activity signal)
    for term in cfg["terms"]:
        data = ctx.client.get_json(SEARCH_API, params={
            "q": f'"{term}"', "type": "r", "order_by": "dateFiled desc",
        }, headers=_headers(ctx))
        for r in data.get("results", []):
            docket_id = r.get("docket_id") or _id_from_url(r.get("docket_absolute_url", ""))
            if not docket_id:
                continue
            item_id = f"{SOURCE}:docket:{docket_id}"
            title = r.get("caseName") or "(unnamed case)"
            court = r.get("court_citation_string") or r.get("court") or ""
            tags = sorted(set(monument_tags(f"{title} {term}", monuments)))
            if item_id in items:
                items[item_id].tags = sorted(set(items[item_id].tags + tags))
                continue
            items[item_id] = Item(
                id=item_id, source=SOURCE, category="litigation",
                title=f"{title} ({court})" if court else title,
                summary=f"Docket {r.get('docketNumber') or '?'} — "
                        f"filed {r.get('dateFiled') or '?'}"
                        + (f", cause: {r['cause']}" if r.get("cause") else ""),
                url=BASE + r.get("docket_absolute_url", ""),
                date=r.get("dateFiled") or "",
                tags=tags,
                raw={k: r.get(k) for k in ("caseName", "court", "docketNumber",
                                           "dateFiled", "docket_id", "cause")},
            )

    # docket-entry polling for cases the user is tracking
    for docket_id in cfg.get("dockets") or []:
        data = ctx.client.get_json(ENTRIES_API, params={
            "docket": docket_id, "order_by": "-date_filed",
        }, headers=_headers(ctx))
        for e in data.get("results", [])[:20]:
            desc = (e.get("description") or "").strip() or f"Entry {e.get('entry_number')}"
            items[f"{SOURCE}:entry:{e['id']}"] = Item(
                id=f"{SOURCE}:entry:{e['id']}", source=SOURCE, category="litigation",
                title=f"Docket {docket_id}: {desc[:120]}",
                summary=desc[:500],
                url=f"{BASE}/docket/{docket_id}/",
                date=(e.get("date_filed") or "")[:10],
                tags=["docket-watch"],
                raw={k: e.get(k) for k in ("id", "entry_number", "date_filed")},
            )
    return list(items.values())


def _id_from_url(url: str) -> str | None:
    m = re.search(r"/docket/(\d+)/", url)
    return m.group(1) if m else None
