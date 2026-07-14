# Build Prompt: "Monument Watch" — Utah Monuments Land-Activity Monitoring Dashboard

You are a senior full-stack engineer building a public-lands watchdog tool. Your goal is a working, self-hostable monitoring dashboard — not a mockup — that a single non-technical user can run on a schedule to detect new development activity on lands removed from Bears Ears and Grand Staircase-Escalante National Monuments by the July 13, 2026 proclamations.

<context>
On July 13, 2026, presidential proclamations reduced Grand Staircase-Escalante NM from ~1.87M acres to ~181,500 acres and Bears Ears NM from ~1.36M acres to ~121,100 acres. The excluded ~3M acres remain federal land but lose monument protections, opening them to mining claims, mineral leasing, road proposals, and grazing changes. The user wants to detect any such activity early. Every meaningful action leaves a public paper trail across roughly a dozen government and NGO sources; this tool aggregates those trails into one dashboard with change detection.

The watch area is the land inside the *pre-reduction* (2021) boundaries of both monuments — that is the full footprint where activity matters, including the parts still inside the shrunken monuments. Relevant Utah counties: San Juan (Bears Ears), Kane and Garfield (Grand Staircase). Relevant BLM offices: Monticello Field Office, Kanab Field Office, Paria River District, and the two Monument offices.

The user is a private citizen, not a developer. They will run this via a single command or an automated schedule and read the dashboard in a browser. Optimize for zero-maintenance operation and graceful degradation: government websites change, and one broken scraper must never take down the dashboard.
</context>

<architecture>
Build a three-layer pipeline. Keep it boring and dependable.

1. **Fetch layer** — Python 3.11+, one adapter module per source (`adapters/<source>.py`), each implementing a common interface: `fetch() -> list[Item]`. Adapters are independent; an exception in one is caught, logged, and recorded as a source-health failure without stopping the run.
2. **State layer** — SQLite database (`data/monitor.db`). Every fetched item is normalized to a common record (schema below) with a stable unique ID. On each run, diff against stored items: anything with an unseen ID is flagged `new`. Store per-source run metadata (last success, last attempt, item count, error message) for the health panel.
3. **Dashboard layer** — a fully static site (`site/index.html` + JS/CSS + generated JSON files in `site/data/`). Regenerated on every fetch run. No backend server required; the user opens the HTML file or hosts it on GitHub Pages. Use Leaflet (CDN) for the map and vanilla JS or a single lightweight library for the feed. All fetched data is embedded as static JSON the page loads locally.

Provide:
- `python run.py` — runs all adapters, updates the DB, regenerates the site, prints a summary of new items.
- `python run.py --source <name>` — runs one adapter (for debugging).
- A ready-to-use GitHub Actions workflow (`.github/workflows/monitor.yml`) that runs `run.py` on a cron schedule (default: every 6 hours), commits updated `site/` output, and publishes via GitHub Pages. Include commented-out steps for optional email notification of new items.
- `config.yaml` — all keywords, county lists, RSS URLs, and API keys' env-var names live here, so the user can tune monitoring without touching code.
- `.env.example` and a README section explaining which keys are needed, which are free, and exactly where to get them.
</architecture>

<normalized_item_schema>
Every adapter emits items in this shape. Consistency here is what makes the unified feed and change detection work.

```json
{
  "id": "courtlistener:docket:68123456",
  "source": "courtlistener",
  "category": "litigation",
  "title": "Hopi Tribe v. Trump — new docket entry",
  "summary": "Motion for preliminary injunction filed.",
  "url": "https://www.courtlistener.com/docket/...",
  "date": "2026-07-20",
  "first_seen": "2026-07-20T14:05:00Z",
  "geometry": null,
  "tags": ["bears-ears", "injunction"],
  "raw": { }
}
```

- `id` must be deterministic and derived from the source's own identifiers (docket ID, FR document number, MLRS case number, bill ID, article URL hash) — never from array position or fetch time, because change detection depends on ID stability across runs.
- `category` is one of: `federal-register`, `mining-claims`, `leasing`, `planning-nepa`, `litigation`, `congress`, `state-lands`, `policy`, `news`.
- `geometry` is GeoJSON when the source provides it (mining claims, lease parcels), otherwise null.
- `raw` preserves the source payload for debugging; exclude it from the site JSON to keep the page light.
</normalized_item_schema>

<data_sources>
Implement adapters in this order. Tier 1 sources have confirmed public APIs — build and verify these first so the dashboard is useful even if Tier 2 takes longer. Before wiring any adapter, verify the endpoint with a live `curl` request and inspect the actual response shape; do not code against an assumed schema. If an endpoint listed here has moved, find the current one from the agency's site rather than stubbing fake data.

**Tier 1 — documented or confirmed public APIs**

1. `federal_register` — GET `https://www.federalregister.gov/api/v1/documents.json` with `conditions[term]` searches for each keyword in config (default: "Bears Ears", "Grand Staircase-Escalante", "Kaiparowits", plus agency-filtered searches for BLM/Interior documents mentioning "wilderness study area" or "mineral withdrawal" in Utah). No key required. Category: `federal-register` (or `policy` for WSA/withdrawal items).

2. `mining_claims` — Query the BLM MLRS ArcGIS FeatureServer layer "BLM Natl MLRS Mining Claims Not Closed" (services directory: `https://gis.blm.gov/nlsdb/rest/services/`, HUB folder). Use the layer's `query` endpoint with `f=geojson`, a spatial filter (`geometry` + `spatialRel=esriSpatialRelIntersects`) against the watch-area polygons, and pagination via `resultOffset` (MaxRecordCount is 2000). Each claim case number becomes an item ID; a case number never seen before is exactly the signal this whole tool exists to catch, so tag new ones `priority`. Also query the companion oil & gas lease case layers in the same services directory for category `leasing`.

3. `courtlistener` — REST API v4 (`https://www.courtlistener.com/api/rest/v4/search/`) keyword searches ("Bears Ears", "Grand Staircase") across dockets, plus docket-entry polling for any docket IDs listed in `config.yaml` (the user will add case dockets as suits are filed). Free API token via env var raises rate limits; the adapter works keyless at lower volume. Category: `litigation`.

4. `congress` — `https://api.congress.gov/v3/bill` search for config keywords plus "Antiquities Act", "land conveyance Utah", "land exchange Utah". Free API key (env var `CONGRESS_API_KEY`, obtained from api.congress.gov). Category: `congress`.

5. `regulations_gov` — `https://api.regulations.gov/v4/documents` filtered to agencies DOI/BLM and config keywords, to catch open comment periods. Free key (`REGS_API_KEY`). Category: `planning-nepa`.

6. `news` — Google News RSS (`https://news.google.com/rss/search?q=<query>`) for each configured query, plus direct RSS feeds for watchdog orgs (try `/feed/` on suwa.org, grandcanyontrust.org, grandstaircasepartners.org, bearsearscoalition.org and keep whichever respond with valid RSS; record the working list in config). De-duplicate by canonical URL. Category: `news`.

7. `boundaries` (not a feed — map layers) — Fetch GeoJSON for: (a) pre-2026 monument boundaries, (b) post-proclamation boundaries once BLM publishes them. Look in BLM's ArcGIS services/hub for National Monument boundary layers; cache locally in `data/geo/`. If the 2026 boundaries aren't published yet, ship with the 2021 boundaries plus a visible "reduced boundaries pending publication" note, and make the layer file swappable via config. These polygons are also the spatial filter for the `mining_claims` adapter; provide a simplified fallback (county-based query for San Juan, Kane, Garfield) if polygon queries prove unreliable.

**Tier 2 — public websites without documented APIs (scrape + diff)**

8. `eplanning` — BLM ePlanning (`https://eplanning.blm.gov`). The search frontend is backed by JSON endpoints; discover them by inspecting the site's network calls, and prefer them over HTML parsing. Pull projects for the Monticello FO, Kanab FO, Paria River District, and both monument offices, plus keyword searches. Track project status changes (a project moving to "comment period open" is a new item even if the project ID was seen before — include status in the ID hash or emit status-change items). Category: `planning-nepa`. This is the single highest-value Tier 2 source; invest effort here.

9. `lease_sales` — BLM Utah lease sale pages and the National Fluid Lease Sale System (nflss.blm.gov). Scrape upcoming sale notices and parcel lists; diff for Utah parcels in the three counties. Category: `leasing`.

10. `blm_policy` — Scrape the BLM Instruction Memoranda index (blm.gov policy pages) for new IMs matching config keywords (wilderness study area, monument, land use planning). Category: `policy`.

11. `utah_dogm` — Utah Division of Oil, Gas and Mining publishes permit/application data (oilgas.ogm.utah.gov data downloads; minerals program pages). Pull whichever machine-readable exports exist (CSV preferred) and filter to the three counties. Category: `leasing`.

12. `sitla` — Utah Trust Lands Administration (trustlands.utah.gov): scrape auction/sale listings and board-meeting agendas; filter for the three counties. This is the only source where land can actually change ownership, so tag county-matching items `priority`. Category: `state-lands`.

**Tier 3 — link-out only (no adapter)**

13. County recorder offices (San Juan, Kane, Garfield) have inconsistent or offline portals. Render a static "manual checks" panel on the dashboard with direct links and a one-line description of what to look for at each. Federal MLRS records cover the same mining claims, so automation here is redundant anyway.
</data_sources>

<dashboard_requirements>
Single-page layout, readable by a non-technical user:

1. **Header stats** — count of new items since last visit (persist "last visit" in localStorage), total items in the last 30 days, and a red banner if any `priority`-tagged item is new.
2. **Map panel** (Leaflet) — old boundaries (outline), new boundaries when available (fill), mining-claim polygons color-coded by first-seen date (new = red), lease parcels if geometry exists. Clicking a feature shows its item card.
3. **Activity feed** — reverse-chronological item cards grouped by day, filterable by category and by monument tag, each showing source badge, title, date, summary, and outbound link. "New" items get a visible marker.
4. **Source health panel** — one row per adapter: last successful fetch, item count, and a green/yellow/red status. A failing scraper shows red with its error message and the date of last good data, so stale information is never silently presented as current.
5. **Manual checks panel** — the Tier 3 link-outs.
6. Plain, information-dense styling; no build step for the frontend (no React/webpack) so the user can host it anywhere as static files.
</dashboard_requirements>

<constraints>
- Respect each site's robots.txt and add a descriptive User-Agent with a contact placeholder. Rate-limit scrapers (≥2s between requests to the same host) and cache HTTP responses during a run. This tool must be a polite citizen of government infrastructure, both ethically and because aggressive scraping gets IP-blocked and kills the user's monitoring.
- Show real data or a clearly-labeled failure state — never placeholder, sample, or fabricated records. A watchdog tool that invents data is worse than no tool. If an adapter can't be verified working during the build, mark it disabled in config with a comment explaining what's blocking it.
- Store only public-record data. No login-gated sources, no CAPTCHA circumvention.
- Keep all secrets in env vars; commit `.env.example`, never `.env`.
- Pin dependencies in `requirements.txt`; keep the dependency list small (requests, feedparser, beautifulsoup4, pyyaml, shapely for point-in-polygon checks — avoid heavyweight GIS stacks like GDAL, since shapely covers the spatial filtering needed and GDAL breaks installs).
- Write the README for a non-developer: install, get the two free API keys, run once locally, then enable the GitHub Action. Include a "what each source watches for and why" table drawn from <data_sources>.
</constraints>

<build_process>
1. Scaffold the repo, config, DB schema, and the adapter interface with one working reference adapter (`federal_register`) end-to-end: fetch → DB → site JSON → rendered page. Verify in-browser output before writing more adapters.
2. Implement remaining Tier 1 adapters, live-verifying each endpoint with curl first. Run the full pipeline after each adapter and confirm items render.
3. Implement Tier 2 adapters. For each, spend up to ~20 minutes finding the JSON backend before falling back to HTML parsing; note in code comments which approach was used and what page structure it depends on.
4. Build the map layers and spatial filtering; verify a known existing mining claim inside the old boundaries appears on the map (there are active uranium claims in the Bears Ears region, e.g. around the Daneros mine area — a correct spatial query will find pre-existing claims, which is your ground truth that the filter works).
5. Write unit tests for: ID stability across runs, new-item detection, and each adapter's parser against a saved fixture of real fetched data (save fixtures during development).
6. Write the GitHub Actions workflow and README last, then do a full clean-clone test run.
</build_process>

<edge_cases>
- An endpoint returns 200 with an empty or restructured payload: treat as a fetch failure for health purposes (yellow status, "0 items — possible format change"), keep prior data visible.
- The 2026 reduced-boundary GeoJSON isn't published yet: proceed with 2021 boundaries and the pending-publication note; structure config so dropping in the new file requires no code change.
- Duplicate coverage (the same lease sale appearing via Federal Register, ePlanning, and news): do not attempt cross-source de-duplication — show all, since each links to a different document. De-duplicate only within a source.
- Rate-limited or keyless operation: adapters requiring keys skip cleanly with a health-panel note ("add CONGRESS_API_KEY to enable") rather than erroring.
- Government shutdown / site outage: red health status with last-good-data date; the run still completes.
</edge_cases>

<success_criteria>
Done means: a clean clone + `pip install -r requirements.txt` + two free API keys + `python run.py` produces a browsable `site/index.html` showing live items from at least all Tier 1 sources and at least ePlanning from Tier 2, with the map rendering boundaries and real existing mining claims, all tests passing, and the GitHub Action ready to enable. Running `run.py` twice in a row produces zero new items the second time (proving ID stability).
</success_criteria>
