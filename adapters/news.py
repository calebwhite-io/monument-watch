"""News coverage: Google News RSS queries plus the watchdog orgs' own feeds.

Google News RSS requires an identifying User-Agent and the hl/gl/ceid params
(verified 2026-07-13 — the bare curl default UA gets an empty response; the
shared client's MonumentWatch UA works). Because a UA/IP block would otherwise
look like a permanently quiet green source, zero entries across every Google
query is treated as a failure. Org feeds are ordinary WordPress RSS; posts are
kept only if they mention a watch keyword.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

import feedparser

from core.context import EmptyPayload, RunContext
from core.models import Item, monument_tags, url_hash

SOURCE = "news"
ZERO_ITEMS_OK = True

GOOGLE_RSS = "https://news.google.com/rss/search"


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    keywords = [k.lower() for k in ctx.config["watch"]["keywords"]]
    cutoff = date.today() - timedelta(days=cfg["max_age_days"])

    items: dict[str, Item] = {}

    google_entries = 0
    for query in cfg["google_news_queries"]:
        resp = ctx.client.get(GOOGLE_RSS, params={
            "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
        })
        entries = feedparser.parse(resp.text).entries
        google_entries += len(entries)
        for entry in entries:
            item = _entry_to_item(entry, monuments, cutoff, require_keywords=None)
            if item:
                items.setdefault(item.id, item)
    if google_entries == 0:
        # these queries always match something; silence means we're blocked
        # or the feed format changed — surface it instead of green-zero
        raise EmptyPayload("Google News returned no entries for any query")

    for feed_url in cfg["org_feeds"]:
        if not ctx.client.allowed_by_robots(feed_url):
            continue
        resp = ctx.client.get(feed_url)
        parsed = feedparser.parse(resp.text)
        for entry in parsed.entries:
            item = _entry_to_item(entry, monuments, cutoff, require_keywords=keywords)
            if item:
                items.setdefault(item.id, item)

    return list(items.values())


def _entry_to_item(entry, monuments: dict, cutoff: date,
                   require_keywords: list[str] | None):
    link = getattr(entry, "link", None)
    title = getattr(entry, "title", "") or ""
    if not link or not title:
        return None
    summary = _clean(getattr(entry, "summary", "") or "")
    text = f"{title} {summary}"
    if require_keywords is not None and not any(k in text.lower() for k in require_keywords):
        return None

    published = getattr(entry, "published_parsed", None)
    if published:
        # feedparser's struct_time is UTC; timegm keeps it UTC (mktime would
        # reinterpret it in local time and shift dates near midnight)
        pub_date = datetime.fromtimestamp(calendar.timegm(published),
                                          tz=timezone.utc).date()
        if pub_date < cutoff:
            return None
        date_str = pub_date.isoformat()
    else:
        date_str = ""

    src = ""
    if getattr(entry, "source", None) and getattr(entry.source, "title", None):
        src = entry.source.title  # Google News carries the outlet name here
    return Item(
        id=f"{SOURCE}:article:{url_hash(link)}",
        source=SOURCE, category="news",
        title=title if not src else f"{title.rsplit(' - ', 1)[0]} ({src})",
        summary=summary[:400],
        url=link,
        date=date_str,
        tags=sorted(set(monument_tags(text, monuments))),
        raw=None,  # RSS entries add bulk and the link preserves everything
    )


def _clean(html: str) -> str:
    """Strip tags from RSS summaries (they're a soup of links and markup)."""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
