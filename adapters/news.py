"""News coverage: Google News RSS queries plus the watchdog orgs' own feeds.

Google News RSS requires an identifying User-Agent and the hl/gl/ceid params
(verified 2026-07-13 — the bare curl default UA gets an empty response; the
shared client's MonumentWatch UA works). Because a UA/IP block would otherwise
look like a permanently quiet green source, zero entries across every Google
query is surfaced: as a degraded-run warning (yellow) when the org feeds still
produced items, or as an outright failure when nothing was salvaged. The same
applies to all org feeds failing. Org feeds are ordinary WordPress RSS; posts
are kept only if they mention a watch keyword.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone

import feedparser

from core.context import EmptyPayload, RunContext
from core.models import Item, monument_tags, url_hash

log = logging.getLogger("monitor.news")

SOURCE = "news"
ZERO_ITEMS_OK = True

GOOGLE_RSS = "https://news.google.com/rss/search"


def fetch(ctx: RunContext) -> list[Item]:
    cfg = ctx.source_config(SOURCE)
    monuments = ctx.config["watch"]["monuments"]
    keywords = [k.lower() for k in ctx.config["watch"]["keywords"]]
    cutoff = date.today() - timedelta(days=cfg["max_age_days"])

    items: dict[str, Item] = {}

    # This source aggregates independent hosts, so one endpoint's outage must
    # not discard what the others returned (RSS windows are shallow — an
    # article that scrolls out during a multi-cycle outage is lost for good).
    # Trouble is therefore collected, not raised: everything salvageable is
    # fetched first, then whole-group failures are reported at the end — as a
    # degraded-run warning when items were saved, as a real failure when not.
    queries = cfg["google_news_queries"]
    google_kept = 0
    google_errors: list[Exception] = []
    for query in queries:
        try:
            resp = ctx.client.get(GOOGLE_RSS, params={
                "q": query, "hl": "en-US", "gl": "US", "ceid": "US:en",
            })
            entries = feedparser.parse(resp.text).entries
        except Exception as exc:
            log.warning("Google News query %r failed: %s", query, exc)
            google_errors.append(exc)
            continue
        google_kept += _collect(entries, items, monuments, cutoff, None, "Google News")

    org_attempted = 0
    org_errors: list[Exception] = []
    for feed_url in cfg["org_feeds"]:
        if not ctx.client.allowed_by_robots(feed_url):
            continue
        org_attempted += 1
        try:
            resp = ctx.client.get(feed_url)
            entries = feedparser.parse(resp.text).entries
        except Exception as exc:
            log.warning("org feed %s failed: %s", feed_url, exc)
            org_errors.append(exc)
            continue
        _collect(entries, items, monuments, cutoff, keywords, feed_url)

    # Every persistent problem must reach the health note — a partial failure
    # that only ever prints to a cron's log is a silently-green half-dead
    # source. Counting KEPT google items (not raw entries) also catches a
    # format change that keeps <item> stubs but breaks link/title extraction.
    problems = []
    if google_errors:
        problems.append(f"{len(google_errors)}/{len(queries)} Google News"
                        f" queries failed: {google_errors[-1]}")
    elif queries and google_kept == 0:
        # these queries always match something; silence means we're blocked
        # or the feed format changed — surface it instead of green-zero
        problems.append("Google News yielded no usable entries"
                        " (blocked, or format change?)")
    if org_errors:
        problems.append(f"{len(org_errors)}/{org_attempted} org feeds"
                        f" failed: {org_errors[-1]}")

    if problems:
        if not items:
            errors = google_errors + org_errors
            if errors and len(errors) == len(queries) + org_attempted:
                raise errors[-1]   # every endpoint errored: a real fetch failure
            # nothing usable but some endpoints DID answer: yellow, with the
            # whole story in the note (a red would claim the source is down)
            raise EmptyPayload("; ".join(problems))
        # partial coverage: keep what we got, but the health panel must show
        # yellow — a silently-green half-dead source is how monitoring dies
        ctx.warnings[SOURCE] = "degraded: " + "; ".join(problems)
    return list(items.values())


def _collect(entries, items: dict, monuments: dict, cutoff: date,
             require_keywords, origin: str) -> int:
    """Add usable entries to `items`; returns how many were usable."""
    kept = 0
    for entry in entries:
        try:
            item = _entry_to_item(entry, monuments, cutoff, require_keywords)
        except Exception as exc:
            # one malformed entry must not cost the run its other articles
            log.warning("skipping malformed entry from %s: %s", origin, exc)
            continue
        if item:
            kept += 1
            items.setdefault(item.id, item)
    return kept


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
