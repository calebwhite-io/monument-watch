"""BLM Utah quarterly oil & gas lease sale page — scrape and diff.

Approach note (2026-07-13): the National Fluid Lease Sale System
(nflss.blm.gov) is a Salesforce Lightning SPA whose data API needs a browser
session to reverse-engineer, so it is NOT scraped here (it's linked in the
manual-checks panel instead). The blm.gov Utah lease-sale page is
server-rendered Drupal and mirrors every sale: press releases, environmental
assessments (which link into ePlanning), sale notices, and parcel lists.
A brand-new link on this page = a new sale document = a new item.

Depends on: Drupal content region markup with h2/h3 sale-period headings and
plain <a> document links beneath them.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from core.context import EmptyPayload, RunContext
from core.models import Item, url_hash

SOURCE = "lease_sales"
ZERO_ITEMS_OK = False   # the page always lists past sales
PRIORITY_ON_NEW = True  # a new Utah sale document is always significant

EA_PATTERN = re.compile(r"DOI-BLM-\S+")
DOC_WORDS = ("lease", "parcel", "ncls", "sale", "errata", "protest")


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    url = cfg["page_url"]
    if not ctx.client.allowed_by_robots(url):
        raise RuntimeError(f"robots.txt disallows {url}")
    soup = BeautifulSoup(ctx.client.get(url).text, "html.parser")

    main = soup.find("main") or soup
    items: dict[str, Item] = {}
    heading = ""
    for el in main.descendants:
        if getattr(el, "name", None) in ("h2", "h3", "h4"):
            heading = el.get_text(" ", strip=True)
            continue
        if getattr(el, "name", None) != "a" or not el.get("href"):
            continue
        label = el.get_text(" ", strip=True)
        href = el["href"]
        if not label or not _is_sale_link(label, href):
            continue
        if href.startswith("/"):
            href = "https://www.blm.gov" + href
        item_id = f"{SOURCE}:doc:{url_hash(href)}"
        if item_id in items:
            continue
        title = f"{heading}: {label}" if heading and heading.lower() not in label.lower() else label
        items[item_id] = Item(
            id=item_id, source=SOURCE, category="leasing",
            title=title[:200],
            summary=f"Document linked from the BLM Utah lease sale page ({label[:120]})",
            url=href,
            date="",  # the page carries no per-link dates; first_seen orders it
            tags=["utah-lease-sale"],
            raw={"heading": heading, "label": label},
        )
    if not items:
        raise EmptyPayload("no sale document links parsed — page structure changed?")
    return list(items.values())


def _is_sale_link(label: str, href: str) -> bool:
    low = label.lower() + " " + href.lower()
    if EA_PATTERN.search(label):
        return True
    if ("press-release" in href or "announcement" in href) and "lease" in low:
        return True
    return any(w in label.lower() for w in DOC_WORDS) and (
        "eplanning" in href or "/sites/default/files" in href)
