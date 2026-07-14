"""The normalized item every adapter emits. Consistency here is what makes the
unified feed and change detection work."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CATEGORIES = {
    "federal-register", "mining-claims", "leasing", "planning-nepa",
    "litigation", "congress", "state-lands", "policy", "news",
}


@dataclass
class Item:
    id: str          # deterministic, from the source's own identifiers
    source: str      # adapter name, e.g. "federal_register"
    category: str    # one of CATEGORIES
    title: str
    url: str
    date: str        # YYYY-MM-DD (the source's own date, not fetch time)
    summary: str = ""
    geometry: dict | None = None   # GeoJSON geometry when the source provides it
    tags: list[str] = field(default_factory=list)
    raw: dict | None = None        # source payload, kept in the DB, excluded from site JSON

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r} on item {self.id!r}")
        if not self.id or not self.id.startswith(self.source + ":"):
            raise ValueError(f"item id {self.id!r} must be prefixed with source {self.source!r}")


def url_hash(url: str) -> str:
    """Stable short hash for sources whose only identifier is a URL (news)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def monument_tags(text: str, monuments: dict[str, list[str]]) -> list[str]:
    """Tag an item with each monument whose place names appear in `text`."""
    lower = text.lower()
    return [tag for tag, names in monuments.items()
            if any(name.lower() in lower for name in names)]
