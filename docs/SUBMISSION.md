# Submission notes — Taste-Fit (Qloo Agentic Hackathon)

Structured after the hackathon kit's submission guide: problem, Qloo workflows and why, a redacted request-to-result trail, credential-free demo, clean-environment setup, known limitations.

## Problem

Brand partnerships, co-marketing and sponsorships are decided on follower counts, gut feel or slow panel research. None of those answer the question that actually matters: **do the fans of A and the fans of B share a taste core, who are they, and what would fans of both show up for?** Taste-Fit answers it in under a minute, with every claim traceable to a Qloo call.

## Qloo workflows used, and why each one

| Step | Qloo call | Why |
|---|---|---|
| Resolve free text | `GET /search?query=&types=` | Turn "Bon Iver" into a Qloo entity id; type-ahead in the UI uses the same call |
| Audience A vs B | `GET /v2/analysis/compare?a.signal.interests.entities=&b.signal.interests.entities=` | Tag affinities for both audiences in one call: the raw material of *shared taste* and *fresh reach*. Fallback: two `GET /v2/insights?filter.type=urn:tag&signal.interests.entities=` calls |
| Who they are | `GET /v2/insights?filter.type=urn:demographics&signal.interests.entities=` (per entity) | Age/gender index → *audience alignment*; the UI shows both audiences side by side |
| What fans of **both** over-index on | `GET /v2/insights?filter.type=urn:entity:{artist,podcast,place,…}&signal.interests.entities=A,B&feature.explainability=true` (+ `signal.location.query` / `filter.location.query` for a city) | Activation ideas that neither brand could find alone; explainability says which audience drives each pick ("both", "mostly A", "mostly B") |
| Where in the city | `GET /v2/insights?filter.type=urn:heatmap&filter.location.query=&signal.interests.entities=A,B` | Hot zones for pop-ups and OOH, named by the nearest recommended place |
| Partner scout | `GET /v2/insights?filter.type=urn:entity:brand&signal.interests.entities=A&filter.exclude.entities=A` then the full score per candidate | Shortlist partners Qloo says A's fans already over-index on |
| Agent extras | `recommend`, `heatmap`, `find_tags` (`GET /v2/tags?filter.query=`) exposed as LLM tools | The writer can fetch more evidence but cannot invent any |

## Request-to-result trail (redacted, from a demo-data run of Patagonia × Bon Iver in New York)

| # | Endpoint | Purpose | Key params | Result |
|---|---|---|---|---|
| Q1 | `/search` | resolve 'Patagonia' | `query=Patagonia` `take=5` | 1 (Patagonia) |
| Q2 | `/search` | resolve 'Bon Iver' | `query=Bon Iver` `take=5` | 1 (Bon Iver) |
| Q3 | `/v2/analysis/compare` | compare audiences Patagonia vs Bon Iver | `a.signal.interests.entities=CD7C38CF…` `b.signal.interests.entities=0D108220…` | 14 (Outdoor Lifestyle, Indie Folk, Sustainability…) |
| Q4 | `/v2/insights` | demographics for Patagonia | `filter.type=urn:demographics` `signal.interests.entities=CD7C38CF…` | 1 |
| Q5 | `/v2/insights` | demographics for Bon Iver | `filter.type=urn:demographics` `signal.interests.entities=0D108220…` | 1 |
| Q6 | `/v2/insights` | artists for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:artist` `signal.interests.entities=CD7C38CF…,0D108220…` `take=6` `signal.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 6 (Fleet Foxes, ODESZA, Phoebe Bridgers…) |
| Q7 | `/v2/insights` | podcasts for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:podcast` `signal.interests.entities=CD7C38CF…,0D108220…` `take=5` `signal.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 5 (The Dirtbag Diaries, Song Exploder, How I Built This…) |
| Q8 | `/v2/insights` | places for fans of Patagonia + Bon Iver in New York | `filter.type=urn:entity:place` `signal.interests.entities=CD7C38CF…,0D108220…` `take=6` `filter.location.query=New York` `filter.exclude.entities=CD7C38CF…,0D108220…` `feature.explainability=True` | 6 (Brooklyn Boulders, Prospect Park, Brooklyn Bowl…) |
| Q9 | `/v2/insights` | heatmap of Patagonia + Bon Iver fans in New York | `filter.type=urn:heatmap` `filter.location.query=New York` `signal.interests.entities=CD7C38CF…,0D108220…` | 81 |

Score: fit 75.6 (strong) = 0.5×54.6 + 0.3×98.5 + 0.2×93.8; fresh reach 37.5%
Brief headline: Patagonia x Bon Iver: strong fit (75.6/100) with a clear shared taste core.
First claim: Fans of Patagonia and Bon Iver share a taste core around Outdoor Lifestyle, Photography, Indie Folk, Sustainability (shared taste 54.6/100). ['Q3']

Entity ids are shortened; no key, no raw payloads. The same ledger is rendered at the bottom of every run in the UI, and the brief's footnotes (Q3, Q6, …) link to it.

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
python -m pytest                            # 38 tests, no network
```

## Safe-use checklist (from the kit)

* Key only in a server-side env var and the `X-Api-Key` header; never in the repo, logs, ledger, browser or screenshots.
* Smallest requests that answer the question: bounded `take`, explainability only on joint-signal calls, demographics per entity once per run (cached), compare instead of two tag calls where available.
* Bounded retries (2) with `Retry-After` on 429; 404 treated as "no results"; auth errors surface as a redacted message.
* Per-run cap (16 calls), per-process daily budget, 6-hour cache, per-visitor rate limit; demo-data fallback with a visible banner.
* Aggregate signals only: inputs are public entity names and a city; no personal data is sent or stored.
* Source and limits are shown in the UI ("live Qloo data" vs "demo data"; verifier notes on the brief).

## Known limitations

* The exact response shape of `/v2/analysis/compare` is not documented; the adapter parses tag rows defensively and falls back to two tag-insight calls. `scripts/smoke_live.py` prints the raw shape to confirm.
* The score is a heuristic for prioritising partnership conversations, not a prediction of campaign lift.
* Heatmap cells have no neighbourhood names from the API; zones are named by the nearest recommended place when one is within ~2 km.
* Demo data covers 74 entities in 5 cities; outside it the app says "not found" rather than guessing.
* LLM writing is optional; without a key the scripted writer produces the same structure.
