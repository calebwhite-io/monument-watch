"""Open comment periods and rulemaking documents from regulations.gov,
filtered to DOI/BLM and the watch keywords.

API: https://open.gsa.gov/api/regulationsgov/ (v4; free key; response shape
verified live 2026-07-13 with DEMO_KEY).
"""
from __future__ import annotations

from datetime import date, timedelta

from core.context import RunContext
from core.models import Item, monument_tags

SOURCE = "regulations_gov"
ZERO_ITEMS_OK = True

API = "https://api.regulations.gov/v4/documents"


def fetch(ctx: RunContext) -> list[Item]:
    key = ctx.require_api_key(SOURCE)
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    since = (date.today() - timedelta(days=cfg["lookback_days"])).isoformat()

    items: dict[str, Item] = {}
    for term in cfg["terms"]:
        for agency in cfg["agencies"]:
            data = ctx.client.get_json(API, params={
                "filter[searchTerm]": f'"{term}"',
                "filter[agencyId]": agency,
                "filter[postedDate][ge]": since,
                "sort": "-postedDate",
                "page[size]": 250,
                "api_key": key,
            })
            for doc in data.get("data", []):
                a = doc.get("attributes", {})
                doc_id = doc.get("id")
                if not doc_id or doc_id in items:
                    continue
                title = a.get("title") or "(untitled)"
                tags = monument_tags(title, monuments)
                open_comment = a.get("openForComment") or a.get("withinCommentPeriod")
                if open_comment:
                    tags.append("comment-open")
                summary = f"{a.get('documentType') or 'document'} in docket {a.get('docketId') or '?'}"
                if a.get("commentEndDate"):
                    summary += f" — comments close {a['commentEndDate'][:10]}"
                items[doc_id] = Item(
                    id=f"{SOURCE}:doc:{doc_id}",
                    source=SOURCE, category="planning-nepa",
                    title=title,
                    summary=summary,
                    url=f"https://www.regulations.gov/document/{doc_id}",
                    date=(a.get("postedDate") or "")[:10],
                    tags=sorted(set(tags)),
                    raw=a,
                )
    return list(items.values())
