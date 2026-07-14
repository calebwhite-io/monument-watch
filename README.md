# Monument Watch

A self-hosted dashboard that watches for **new development activity on the
lands cut from Bears Ears and Grand Staircase-Escalante National Monuments**
by the July 13, 2026 proclamations — mining claims, oil & gas leasing, drilling
permits, NEPA planning, policy changes, lawsuits, bills, state land sales, and
news — aggregated from a dozen public government and watchdog sources into one
page with change detection.

The watch area is the land inside the **pre-reduction (2021) boundaries** of
both monuments: everywhere activity matters, including land still inside the
shrunken monuments.

**No server required.** A Python script fetches everything, stores it in a
small local database, and regenerates a static web page. Run it by hand or
let a free GitHub Action run it every 6 hours and publish the page for you.

---

## Quick start (10 minutes)

You need [Python 3.11+](https://www.python.org/downloads/) installed
(on Windows, tick "Add python.exe to PATH" during install).

Get the code either way:

- **No git?** On the repository's GitHub page click **Code → Download ZIP**,
  unzip it anywhere, and open a terminal in that folder.
- **With git:** `git clone <this-repository-url>` and `cd` into it.

Then:

```bash
pip install -r requirements.txt
python run.py
```

The first run takes a few minutes (it is deliberately gentle on government
servers). When it finishes, open **`site/index.html`** in your browser —
that's the dashboard. Run `python run.py` again any time to refresh it.

> **First run note:** the first fetch of each source records the *baseline* —
> the thousands of claims, wells, and projects that already exist. Those are
> drawn on the map and stored, but they don't flood the activity feed. From
> then on, anything **new** is flagged — that's the signal this tool exists
> to catch. The red banner on your first visit lists prior state-land sales
> in the watch counties; review them once and it clears.

## The two free API keys (optional but recommended)

Two sources stay dormant (yellow in the health panel) until you add free keys.
Both signups take about a minute and need only an email address.

| Key | Enables | Where to get it |
|---|---|---|
| `CONGRESS_API_KEY` | Bills in Congress touching the monuments, the Antiquities Act, or Utah land deals | <https://api.congress.gov/sign-up/> — instant, free |
| `REGS_API_KEY` | Open federal comment periods on DOI/BLM actions | <https://open.gsa.gov/api/regulationsgov/> — click "Request an API key", free |
| `COURTLISTENER_API_TOKEN` *(optional)* | Higher rate limit for the litigation tracker (works without it) | free account at <https://www.courtlistener.com> |

Copy `.env.example` to a file named `.env` and paste your keys in. Also set
`CONTACT_EMAIL` — it goes in this tool's User-Agent so agencies can email you
instead of blocking you if traffic ever looks odd.

## Run it automatically (GitHub Actions + Pages)

1. Push this repository to your own GitHub account (public repo = free Actions
   minutes and free Pages hosting).
2. In the repo: **Settings → Pages → Source: "GitHub Actions"**.
3. **Settings → Secrets and variables → Actions → New repository secret** —
   add `CONGRESS_API_KEY`, `REGS_API_KEY`, `CONTACT_EMAIL` (and optionally
   `COURTLISTENER_API_TOKEN`).
4. Open the **Actions** tab, enable workflows, select **Monument Watch
   monitor**, and press **Run workflow** once to verify.

From then on it runs every 6 hours, commits its updated database and site
data back to the repo (that's how it remembers what it has already seen), and
publishes the dashboard at `https://<your-username>.github.io/<repo-name>/`.

Want an email whenever something new appears? Open
`.github/workflows/monitor.yml` and follow the comments on the
"Email notification" step.

## What each source watches, and why

| Source (health panel name) | What it watches | Why it matters |
|---|---|---|
| `federal_register` | Federal Register documents mentioning the monuments; BLM/Interior notices on wilderness study areas and mineral withdrawals in Utah | Boundary changes, land-use rules, and withdrawals are legally announced here first |
| `mining_claims` | BLM MLRS mining-claim records (polygons) inside the watch area | **A claim case number never seen before is the core early signal** — staking a claim is the first legal step toward a mine. New ones are tagged `priority` and drawn red on the map |
| `mining_claims` (leases) | Live federal oil & gas lease parcels in the watch area | Leases are how drilling rights are sold |
| `courtlistener` | Federal court dockets mentioning the monuments, plus any specific case you list in `config.yaml` | The proclamations are being litigated; docket activity moves fast |
| `congress` | Bills touching the monuments, the Antiquities Act, or Utah land exchanges | Legislation could lock changes in (or reverse them) |
| `regulations_gov` | DOI/BLM documents with open comment periods | Comment windows are short — this catches them while you can still act |
| `news` | Google News searches plus SUWA, Grand Canyon Trust, and Bears Ears Coalition feeds | Journalists and watchdogs often surface activity before databases do |
| `eplanning` | Every BLM NEPA project at the Monticello & Kanab field offices and the GSENM office, with status tracking | Roads, drilling, grazing, and mining all require NEPA review — this is where projects first appear. A project entering a new phase shows up as a new item |
| `lease_sales` | BLM Utah quarterly lease-sale page (sale notices, parcel lists, EAs) | Utah parcel lists reveal exactly which lands are up for leasing |
| `blm_policy` | New BLM Instruction Memoranda matching watch keywords | National policy changes (leasing rules, land-use planning) land here quietly |
| `utah_dogm` | Utah oil/gas well records in San Juan, Kane & Garfield counties, with status tracking | Drilling permits (APDs) and status changes are state-recorded; live wells inside the watch area appear on the map |
| `sitla` | Utah Trust Lands auctions, sales, and board news | **The only watched source where land can actually change ownership** — watch-county items are tagged `priority` |

**Manual checks panel** — the three county recorder offices (San Juan, Kane,
Garfield) and NFLSS have no reliable machine interface; the dashboard links
them with a note about what to look for. Federal MLRS records duplicate the
county mining-claim filings, so nothing critical is automation-blind.

## Reading the dashboard

- **Header** — new items since your last visit (remembered by your browser),
  totals for 30 days, and source health at a glance. A **red banner** means
  new `priority` items: new mining claims, new lease-sale documents, or state
  land sales in the watch counties.
- **Map** — dashed line: 2021 (pre-reduction) monument boundaries, the watch
  area. Red shapes: claims/wells first seen in the last 30 days. Orange:
  last 180 days. Blue: older. Click anything for its record link. When BLM
  publishes the reduced-boundary GeoJSON, save it as
  `data/geo/boundaries_reduced.geojson` and set `reduced_published: true` in
  `config.yaml` — it will overlay in green with no code change.
- **Feed** — newest first, filterable by category, monument, or text.
- **Source health** — green: fetched fine. Yellow: waiting on an API key, ran
  but empty, or data is stale (older than 3 days). Red: last fetch failed;
  the error is shown and the last good data stays on the page with its date —
  stale data is never silently presented as current.

## Tuning (no code needed)

Everything lives in [`config.yaml`](config.yaml):

- **Add a lawsuit** — when a new monument case is filed, put its CourtListener
  docket number in `sources.courtlistener.dockets`; every new filing becomes
  an item.
- **Add keywords** — `watch.keywords` and per-source term lists.
- **Disable a source** — set its `enabled: false`.
- **Swap boundaries** — see the map note above.

## Troubleshooting

- **A source shows red for days** — government sites change. Open the error
  in the health panel; if a URL moved, it's usually fixable in `config.yaml`.
  Everything else keeps running regardless.
- **`python run.py --source <name>`** runs one source for debugging;
  `--list` shows names; `-v` prints request detail.
- **Start over** — delete `data/monitor.db` and run again; the next run
  rebuilds the baseline (your feed resets too).
- **Tests** — `python -m pytest tests/` (uses saved real responses, no
  network needed).

## Being a good citizen

This tool identifies itself in every request, obeys `robots.txt`, waits ≥2
seconds between requests to the same host (10 s where a site asks), caches
within a run, and only stores public records. Please keep the 6-hour schedule
— it's more than fresh enough for these sources, and aggressive polling gets
IPs blocked, which kills your monitoring.
