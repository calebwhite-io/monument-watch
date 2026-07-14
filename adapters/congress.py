"""Bills touching the monuments / Antiquities Act / Utah land deals.

API: https://api.congress.gov/ (v3; free key; response shape verified live
2026-07-13 with DEMO_KEY). The v3 API has no full-text search endpoint, so
this adapter pulls recently-updated bills and keyword-matches their titles
client-side. A 6-hour cron easily keeps up with the update stream.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.context import RunContext
from core.models import Item, monument_tags

SOURCE = "congress"
ZERO_ITEMS_OK = True

API = "https://api.congress.gov/v3/bill"
PAGE_SIZE = 250
MAX_PAGES = 8   # 2000 most recently updated bills per run


def fetch(ctx: RunContext) -> list[Item]:
    key = ctx.require_api_key(SOURCE)
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    keywords = [t.lower() for t in cfg["terms"]]
    since = (datetime.now(timezone.utc)
             - timedelta(days=cfg["lookback_days"])).strftime("%Y-%m-%dT00:00:00Z")

    items: dict[str, Item] = {}
    offset = 0
    for _ in range(MAX_PAGES):
        data = ctx.client.get_json(API, params={
            "api_key": key, "format": "json", "limit": PAGE_SIZE,
            "offset": offset, "sort": "updateDate desc",
            "fromDateTime": since,
        })
        bills = data.get("bills", [])
        for b in bills:
            title = b.get("title") or ""
            matched = [k for k in keywords if _keyword_in(k, title)]
            if not matched:
                continue
            bill_key = f"{b.get('congress')}-{b.get('type', '').lower()}-{b.get('number')}"
            action = b.get("latestAction") or {}
            items[bill_key] = Item(
                id=f"{SOURCE}:bill:{bill_key}",
                source=SOURCE, category="congress",
                title=f"{b.get('type', '')} {b.get('number', '')}: {title}",
                summary=(f"Latest action ({action.get('actionDate', '?')}): "
                         f"{action.get('text', '')}")[:500],
                url=f"https://www.congress.gov/bill/{b.get('congress')}th-congress/"
                    f"{_chamber_slug(b.get('type', ''))}/{b.get('number')}",
                date=action.get("actionDate") or b.get("updateDate", "")[:10],
                tags=sorted(set(monument_tags(title, monuments) + ["bill"])),
                raw=b,
            )
        if len(bills) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return list(items.values())


def _keyword_in(keyword: str, title: str) -> bool:
    """Multi-word config terms like 'land exchange Utah' match when all their
    words appear; single phrases match as substrings."""
    low = title.lower()
    words = keyword.split()
    if len(words) > 2:
        return all(w in low for w in words)
    return keyword in low


def _chamber_slug(bill_type: str) -> str:
    return {"hr": "house-bill", "s": "senate-bill", "hres": "house-resolution",
            "sres": "senate-resolution", "hjres": "house-joint-resolution",
            "sjres": "senate-joint-resolution", "hconres": "house-concurrent-resolution",
            "sconres": "senate-concurrent-resolution"}.get(bill_type.lower(), "bill")
