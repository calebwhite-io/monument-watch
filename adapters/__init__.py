"""Adapter registry. Order matters: Tier 1 (documented APIs) first, so the
dashboard is useful even when a Tier 2 scrape breaks."""
from adapters import (blm_policy, congress, courtlistener, eplanning,
                      federal_register, lease_sales, mining_claims, news,
                      regulations_gov, sitla, utah_dogm)

ADAPTERS = {
    # Tier 1 — documented/confirmed public APIs
    federal_register.SOURCE: federal_register,
    mining_claims.SOURCE: mining_claims,
    courtlistener.SOURCE: courtlistener,
    congress.SOURCE: congress,
    regulations_gov.SOURCE: regulations_gov,
    news.SOURCE: news,
    # Tier 2 — undocumented backends and scrape-and-diff
    eplanning.SOURCE: eplanning,
    lease_sales.SOURCE: lease_sales,
    blm_policy.SOURCE: blm_policy,
    utah_dogm.SOURCE: utah_dogm,
    sitla.SOURCE: sitla,
}
