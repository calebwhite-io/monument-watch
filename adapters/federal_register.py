"""Federal Register documents mentioning the monuments, plus BLM/Interior
policy documents (wilderness study areas, mineral withdrawals in Utah).

API: https://www.federalregister.gov/developers/documentation/api/v1
(documented public API, no key; verified live 2026-07-13).
"""
from __future__ import annotations

from datetime import date, timedelta

from core.context import RunContext
from core.models import Item, monument_tags

SOURCE = "federal_register"
ZERO_ITEMS_OK = True   # a quiet window with no matching documents is normal

API = "https://www.federalregister.gov/api/v1/documents.json"
MAX_PAGES_PER_TERM = 3   # 300 docs/term within the lookback window is plenty


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    since = (date.today() - timedelta(days=cfg["lookback_days"])).isoformat()
    monuments = ctx.config["watch"]["monuments"]

    items: dict[str, Item] = {}
    searches = [(f'"{t}"', "federal-register") for t in cfg["terms"]]
    # policy terms are quoted-phrase + Utah, restricted to Interior/BLM
    searches += [(f'"{t.rsplit(" ", 1)[0]}" Utah', "policy") for t in cfg["policy_terms"]]

    for term, category in searches:
        params = {
            "conditions[term]": term,
            "conditions[publication_date][gte]": since,
            "order": "newest",
            "per_page": 100,
        }
        if category == "policy":
            params["conditions[agencies][]"] = ["interior-department", "land-management-bureau"]
        url, page = API, 0
        while url and page < MAX_PAGES_PER_TERM:
            data = ctx.client.get_json(url, params=params if page == 0 else None)
            for doc in data.get("results", []):
                item = _to_item(doc, category, monuments)
                if item.id in items:
                    items[item.id].tags = sorted(set(items[item.id].tags + item.tags))
                else:
                    items[item.id] = item
            url, page = data.get("next_page_url"), page + 1
    return list(items.values())


def _to_item(doc: dict, category: str, monuments: dict) -> Item:
    title = doc.get("title") or "(untitled)"
    abstract = doc.get("abstract") or ""
    tags = monument_tags(f"{title} {abstract}", monuments)
    if doc.get("type"):
        tags.append(doc["type"].lower())
    return Item(
        id=f"{SOURCE}:doc:{doc['document_number']}",
        source=SOURCE,
        category=category,
        title=title,
        summary=abstract[:500],
        url=doc.get("html_url") or "",
        date=doc.get("publication_date") or "",
        tags=sorted(set(tags)),
        raw=doc,
    )
