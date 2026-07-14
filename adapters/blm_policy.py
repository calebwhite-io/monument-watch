"""New BLM Instruction Memoranda (national policy directives) matching the
watch keywords.

Approach note (2026-07-13): the index moved to /policy/instruction-memorandum
(the URL in older documentation, /policy/instruction-memorandums, 404s). The
page is server-rendered Drupal: each IM is a `div.views-row` holding the IM
number in <strong> and a linked title — no JSON backend was found, so this
parses that markup and fails loudly if it disappears.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from core.context import EmptyPayload, RunContext
from core.models import Item

SOURCE = "blm_policy"
ZERO_ITEMS_OK = True   # keyword filter may legitimately match nothing new


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    keywords = [k.lower() for k in cfg["keywords"]]
    base = cfg["index_url"]
    if not ctx.client.allowed_by_robots(base):
        raise RuntimeError(f"robots.txt disallows {base}")

    items: list[Item] = []
    rows_seen = 0
    for page in range(cfg.get("pages", 2)):
        url = base if page == 0 else f"{base}?page={page}"
        soup = BeautifulSoup(ctx.client.get(url).text, "html.parser")
        rows = soup.select("div.views-row")
        rows_seen += len(rows)
        for row in rows:
            link = row.find("a", href=True)
            number_el = row.find("strong")
            if not link or not number_el:
                continue
            number = number_el.get_text(" ", strip=True)   # e.g. "IM 2026-018"
            title = link.get_text(" ", strip=True)
            if not any(k in title.lower() for k in keywords):
                continue
            slug = link["href"].rstrip("/").rsplit("/", 1)[-1]  # e.g. im-2026-018
            items.append(Item(
                id=f"{SOURCE}:im:{slug}",
                source=SOURCE, category="policy",
                title=f"{number}: {title}",
                summary=f"BLM policy directive {number}",
                url="https://www.blm.gov" + link["href"] if link["href"].startswith("/")
                    else link["href"],
                date="",  # listing carries no dates; first_seen orders the feed
                tags=["instruction-memorandum"],
                raw={"number": number, "title": title},
            ))
    if rows_seen == 0:
        raise EmptyPayload("no views-row entries parsed — IM index moved again?")
    return items
