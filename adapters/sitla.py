"""Utah Trust Lands Administration (SITLA) — auction, sale, and board news.

This is the only watched source where land can actually change ownership, so
posts mentioning a watch county are tagged `priority`.

Approach note (2026-07-13): trustlands.utah.gov is WordPress and its REST API
(/wp-json/wp/v2/posts) is open — a real JSON backend, so no HTML scraping.
The site 403s non-browser User-Agents and its robots.txt asks for a 10 s
crawl delay; the shared client honors both.
"""
from __future__ import annotations

import html

from bs4 import BeautifulSoup

from core.context import RunContext
from core.models import Item, monument_tags

SOURCE = "sitla"
ZERO_ITEMS_OK = True

# the WP API rejects the default python-requests style agents
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MonumentWatch/1.0)"}


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    counties = ctx.config["watch"]["counties"]
    monuments = ctx.config["watch"]["monuments"]
    if not ctx.client.allowed_by_robots(cfg["api_url"]):
        raise RuntimeError("robots.txt disallows the SITLA API")

    items: dict[str, Item] = {}
    for term in cfg["search_terms"]:
        posts = ctx.client.get_json(cfg["api_url"], params={
            "search": term, "per_page": 50, "orderby": "date", "order": "desc",
        }, headers=BROWSER_HEADERS)
        for p in posts:
            post_id = p.get("id")
            if not post_id or f"{SOURCE}:post:{post_id}" in items:
                continue
            title = html.unescape((p.get("title") or {}).get("rendered", "")).strip()
            excerpt = BeautifulSoup((p.get("excerpt") or {}).get("rendered", ""),
                                    "html.parser").get_text(" ", strip=True)
            text = f"{title} {excerpt}"
            tags = monument_tags(text, monuments)
            county_hits = [c for c in counties if c.lower() in text.lower()]
            if county_hits:
                # land in a watch county could change hands — the top signal
                tags += ["priority"] + [f"{c.lower().replace(' ', '-')}-county"
                                        for c in county_hits]
            items[f"{SOURCE}:post:{post_id}"] = Item(
                id=f"{SOURCE}:post:{post_id}",
                source=SOURCE, category="state-lands",
                title=title or "(untitled post)",
                summary=excerpt[:400],
                url=p.get("link") or "https://trustlands.utah.gov/",
                date=(p.get("date") or "")[:10],
                tags=sorted(set(tags)),
                raw={"id": post_id, "slug": p.get("slug"), "search_term": term},
            )
    return list(items.values())
