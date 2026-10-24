# Submission notes — Taste-Fit (Qloo Agentic Hackathon)

Structured after the hackathon kit's submission guide: problem, Qloo workflows and why, a redacted request-to-result trail, credential-free demo, clean-environment setup, known limitations.

## Problem

Brand partnerships, co-marketing and sponsorships are decided on follower counts, gut feel or slow panel research. None of those answer the question that actually matters: **do the fans of A and the fans of B share a taste core, who are they, and what would fans of both show up for?** Taste-Fit answers it in under a minute, with every claim traceable to a Qloo call.

## Qloo workflows used, and why each one

| Step | Qloo call | Why |
|---|---|---|
| Resolve free text | `GET /search?query=&types=` | Turn "Bon Iver" into a Qloo entity id; type-ahead in the UI uses the same call |
| How much each audience likes the other | `GET /v2/insights?filter.type=urn:entity:{type of B}&signal.interests.entities=A&filter.results.entities=B` and the reverse | Qloo's audience-embedding affinity in both directions: the input of *shared taste* (0.5 = medium → 0, 1.0 → 100). Partner scout batches all candidates into one A → B call |
| Audience A vs B | `GET /v2/insights?filter.type=urn:tag&signal.interests.entities=` (once per entity, take 50) | What each audience over-indexes on, across domains: explains *what* is shared and gives *fresh reach* |
| Same kind of thing? | `GET /v2/analysis/compare?a.signal.interests.entities=&b.signal.interests.entities=` | Catalog tags attached to the two entities. Shown next to the audience overlap: a brand and a band share no catalog tags, so any overlap is in the fans |
| Who they are | `GET /v2/insights?filter.type=urn:demographics&signal.interests.entities=` (per entity) | Age/gender index → *audience alignment*; the UI shows both audiences side by side |
| What fans of **both** over-index on | `GET /v2/insights?filter.type=urn:entity:{artist,podcast,place,…}&signal.interests.entities=A,B&feature.explainability=true` (+ `signal.location.query` / `filter.location.query` for a city) | Activation ideas that neither brand could find alone; explainability says which audience drives each pick ("both", "mostly A", "mostly B") |
| Where in the city | `GET /v2/insights?filter.type=urn:heatmap&filter.location.query=&signal.interests.entities=A,B` | Hot zones for pop-ups and OOH, named by the nearest recommended place |
| Partner scout | `GET /v2/insights?filter.type=urn:entity:brand&signal.interests.entities=A&filter.exclude.entities=A`, one batched cross-affinity call, then the full score per candidate | Shortlist partners Qloo says A's fans already over-index on |
| Agent extras | `recommend`, `heatmap`, `find_tags` (`GET /v2/tags?filter.query=`) exposed as LLM tools | The writer can fetch more evidence but cannot invent any |

## Request-to-result trail (redacted, from a demo-data run of Patagonia × Bon Iver in New York)

| # | Endpoint | Purpose | Key params | Result |
|---|---|---|---|---|
| Q1 | `/search` | resolve 'Patagonia' | `query=Patagonia` `take=5` | 1 (Patagonia) |
| Q2 | `/search` | resolve 'Bon Iver' | `query=Bon Iver` `take=5` | 1 (Bon Iver) |
| Q3 | `/v2/insights` | taste tags for Patagonia | `filter.type=urn:tag` `signal.interests.entities=CD7C38CF…` `take=50` | 11 (Sustainability, Outdoor Lifestyle, Hiking…) |
| Q4 | `/v2/insights` | taste tags for Bon Iver | `filter.type=urn:tag` `signal.interests.entities=0D108220…` `take=50` | 8 (Indie Folk, Outdoor Lifestyle, Craft Coffee…) |
| Q5 | `/v2/insights` | affinity of Bon Iver among fans of Patagonia | `filter.type=urn:entity:artist` `signal.interests.entities=CD7C38CF…` `filter.results.entities=0D108220…` `take=1` | 1 (Bon Iver) |
| Q6 | `/v2/insights` | affinity of Patagonia among fans of Bon Iver | `filter.type=urn:entity:brand` `signal.interests.entities=0D108220…` `filter.results.entities=CD7C38CF…` `take=1` | 1 (Patagonia) |
| Q7 | `/v2/insights` | demographics for Patagonia | `filter.type=urn:demographics` `signal.interests.entities=CD7C38CF…` | 1 |
| Q8 | `/v2/insights` | demographics for Bon Iver | `filter.type=urn:demographics` `signal.interests.entities=0D108220…` | 1 |
| Q9 | `/v2/analysis/compare` | compare catalog tags Patagonia vs Bon Iver | `a.signal.interests.entities=CD7C38CF…` `b.signal.interests.entities=0D108220…` | 14 (2 in common: Sustainability, Outdoor Lifestyle) |
| Q10 | `/v2/insights` | artists for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:artist` `signal.interests.entities=CD7C38CF…,0D108220…` `take=6` `signal.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 6 (Fleet Foxes, ODESZA, Phoebe Bridgers…) |
| Q11 | `/v2/insights` | podcasts for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:podcast` `signal.interests.entities=CD7C38CF…,0D108220…` `take=5` `signal.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 5 (The Dirtbag Diaries, Song Exploder, How I Built This…) |
| Q12 | `/v2/insights` | places for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:place` `signal.interests.entities=CD7C38CF…,0D108220…` `take=6` `filter.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 6 (Brooklyn Boulders, Prospect Park, Brooklyn Bowl…) |
| Q13 | `/v2/insights` | heatmap of Patagonia + Bon Iver fans in New York | `filter.type=urn:heatmap` `filter.location.query=New York` `signal.interests.entities=CD7C38CF…,0D108220…` | 81 |

Score: fit 81.4 (strong) = 0.6×70.0 + 0.4×98.5; shared taste = (mean(0.855, 0.845) − 0.5) / 0.5 × 100; fresh reach 37.5% (shown, not scored)
Brief headline: Patagonia x Bon Iver: strong fit (81.4/100) with a clear shared taste core.
First claim: Qloo scores the affinity of Bon Iver among Patagonia fans at 86 and of Patagonia among Bon Iver fans at 85 on a 0-100 scale where 50 is medium (shared taste 70.0/100). ['Q5', 'Q6']

Entity ids are shortened; no key, no raw payloads. The same ledger is rendered at the bottom of every run in the UI, and the brief's footnotes (Q3, Q8, …) link to it.

## Demo

* Hosted app: see the Devpost submission (Render free web service, Docker). The header badge shows whether the run used live Qloo data or demo data.
* Screenshots: `docs/screenshots/home.png`, `docs/screenshots/fit-check.png`, `docs/screenshots/partner-scout.png`.
* No credentials are needed to use the demo. The key is a Render service secret on the server.

## Setup in a clean environment

```bash
git clone https://github.com/kbns2000/taste-fit && cd taste-fit
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export QLOO_API_KEY=...                     # optional; without it the app runs on demo data and says so
uvicorn app.main:app --port 7860            # http://127.0.0.1:7860
python -m pytest                            # 51 tests, no network
```

## Safe-use checklist (from the kit)

* Key only in a server-side env var and the `X-Api-Key` header; never in the repo, logs, ledger, browser or screenshots.
* Smallest requests that answer the question: bounded `take`, explainability only on joint-signal calls, demographics and tag profiles per entity once per run (cached and re-cited, so partner scout fetches A only once).
* Bounded retries (2) with `Retry-After` on 429; 404 treated as "no results"; auth errors surface as a redacted message.
* Per-run cap (16 calls), per-process daily budget, 6-hour cache, per-visitor rate limit; demo-data fallback with a visible banner.
* Aggregate signals only: inputs are public entity names and a city; no personal data is sent or stored.
* Source and limits are shown in the UI ("live Qloo data" vs "demo data"; verifier notes on the brief).

## Known limitations

* `/v2/analysis/compare` returns the catalog tags of each entity (`results.a`, `results.b`, common ones in `results.tags`), not audience affinities, so it cannot score shared taste across domains. Shared taste uses the cross-affinity instead; compare is context. `scripts/smoke_live.py` prints the live shapes.
* Live tag-insight affinities sit at about 0.99–1.0 across an audience's whole top 50 (2026-10-24), and unrelated audiences share generic tags. That is why the score uses cross-affinity. The overlap lists drop venue and payment attributes and 22 generic tags measured on a 16-audience live panel (`scripts/calibrate_tags.py`; "friendship" is in 13 of 16 top-50 lists, "Identifies as women-owned" in 15; music genres are never dropped). After that, two audiences' top-50 lists share a median of three tags and 15 of the 120 panel pairs share none, so the overlap card explains the score rather than drives it; Qloo's ranking order breaks ties.
* The score is a heuristic for prioritising partnership conversations, not a prediction of campaign lift.
* Heatmap cells have no neighbourhood names from the API; zones are named by the nearest recommended place when one is within ~2 km.
* `filter.location.query` can resolve a city to a wider area ("New York" returned the whole state, 3,557 cells, on 2026-10-24). Hot zones and the map keep the cells within 30 km of the city centre and the UI states how many were dropped.
* Demo data covers 74 entities in 5 cities; outside it the app says "not found" rather than guessing.
* LLM writing is optional; without a key the scripted writer produces the same structure.
