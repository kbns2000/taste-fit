# Taste-Fit — a brand-partnership agent built on Qloo Taste AI

**Will these two audiences actually click?** Give Taste-Fit two brands (or a brand and an artist, podcast, venue, destination…) and it asks the Qloo Insights API what their fans share, where they differ, and what fans of *both* over-index on. It then

1. scores the partnership with a printed formula built only from Qloo signals,
2. finds concrete activations (artists, podcasts, places) that the *joint* audience over-indexes on, with Qloo's explainability showing which side drives each pick,
3. maps where the joint audience concentrates in a city (Qloo heatmap),
4. writes a decision-ready brief in which **every claim cites the Qloo call that supports it**, and
5. verifies the brief against that evidence ledger before showing it.

A second mode, **Partner scout**, asks Qloo which brands fans of A over-index on and scores the top candidates.

![Fit check result](docs/screenshots/fit-check.png)

## Why this needs Qloo

Partnership and co-marketing decisions are usually made on vibes, follower counts or expensive panel surveys. None of those can answer "do the *fans* of Patagonia and the *fans* of Bon Iver share a taste core, and what else do they both like?" across brands, music, podcasts and places at once. Qloo's cross-domain affinity graph is the only input that can:

| Product question | Qloo capability used |
|---|---|
| How much does each audience like the other side? | `/v2/insights` with `filter.results.entities`, both directions: Qloo's affinity of B among A's fans and of A among B's fans |
| What do the two audiences share, and where do they differ? | `filter.type=urn:tag` insights per entity: what each audience over-indexes on, across domains |
| Are the two the same kind of thing on paper? | `/v2/analysis/compare` (catalog tags the entities share), shown next to the audience overlap |
| Are these the same people demographically? | `filter.type=urn:demographics` per entity |
| What would fans of **both** show up for? | joint-signal `/v2/insights` with `signal.interests.entities=A,B` and `feature.explainability=true` per target type |
| Where in the city should the activation be? | `filter.type=urn:heatmap` with `filter.location.query`, plus `urn:entity:place` with `filter.location.query` |
| Which partners should A even consider? | `urn:entity:brand` insights signalled by A, then a full score for each candidate |
| Free text → entity | `/search` with `types` |

The LLM in the loop does not invent any of this; it can only *ask for more Qloo evidence* through the same tools and must submit the brief through a strict schema. The verifier removes any entity the model names that Qloo did not return.

## The Taste-Fit score

All inputs are Qloo outputs, so each number traces back to a ledger entry:

```
shared_taste       = (mean(affinity of B among A's fans, affinity of A among B's fans) − 0.5) / 0.5 × 100   (clipped to 0..100)
audience_alignment = (cosine(demographic index A, demographic index B) + 1) / 2 × 100
fit                = 0.6·shared_taste + 0.4·audience_alignment
fresh_reach        = share of B's top-15 taste tags that are not in A's top-20 × 100   (shown, not scored)
```

≥70 strong · 55–69 workable · <55 weak. The cross-affinity comes from `filter.results.entities`; Qloo describes entity-to-entity affinity as a similarity of audience embeddings where about 0.5 is medium and 0.8+ very high, so medium maps to 0 and the maximum to 100. In live data (2026-10-24) Bon Iver scores 0.92 among Patagonia fans and McDonald's 0.67, so the scale separates related from unrelated partners. If Qloo returns no cross-affinity, shared taste falls back to the cosine of the two audiences' tag lists and the UI says so.

The tag lists still explain *what* the audiences share, and fresh reach shows what B adds. Two kinds of tag are left out because they say little about taste: venue and payment attributes (star ratings, card networks, price bands, "Identifies as women-owned", dishes a venue is known for), and generic tags. The generic list (`app/qloo/generic_tags.json`, 22 tags such as "friendship", "teenager", "Restaurant") was measured on a live panel of 16 brands, artists and a podcast with `scripts/calibrate_tags.py`: a tag counts as generic when it is in the top 50 of at least half the panel, or a quarter for venue categories. Music genres are never treated as generic, because "rock" or "indie" is exactly what two artists' audiences should be able to share. Spellings of one tag ("Hip Hop" / "hip hop", "K Pop" / "Kpop") are folded into one. A near-clone (shared taste 90+ and the same kind of entity) is flagged in the brief as a risk rather than penalised in the score.

## Agent design

```
UI (vanilla JS, SSE)  →  FastAPI  →  pipeline
                                      ├─ ToolBox over QlooClient  (live REST adapter | deterministic mock)
                                      ├─ Ledger  Q1..Qn  (endpoint, redacted params, counts, names, ms, cache)
                                      ├─ scoring.compute_fit
                                      ├─ writer: LLM agent loop (tools: recommend / heatmap / find_tags → finalize_brief, strict)
                                      │          or scripted writer (no key needed)
                                      └─ verifier (citations exist, entities exist in ledger, score/verdict consistency)
```

* **Evidence ledger.** Every Qloo request is numbered. The brief's footnotes, the activation cards and the map all point at ledger rows; the UI shows the request parameters (entity ids shortened) so the request-to-result path is visible without exposing payloads or the key.
* **Writer.** With `ANTHROPIC_API_KEY`, Claude (`claude-opus-5-5` by default, via the official SDK with strict tools and server-side fallbacks) writes the brief and may spend up to `LLM_MAX_TOOL_CALLS` extra Qloo calls. Any OpenAI-compatible endpoint (`OPENAI_BASE_URL`) works too, so free tiers are fine. With neither, the scripted writer produces the same structure from the evidence, so the product is complete without an LLM.
* **Verifier.** Unknown citations and invented entities are flagged; invented entities are removed from activations and listed as "removed (not in Qloo evidence)".
* **Quota guard.** Per-run call cap (`QLOO_MAX_CALLS_PER_RUN`, default 16), per-process daily budget (`QLOO_DAILY_BUDGET`), a 6-hour response cache, bounded retries honouring `Retry-After`, and a per-visitor rate limit. If live Qloo is unavailable the run reruns on demo data and says so.
* **Demo data.** `app/qloo/fixtures.json` is a small deterministic world (74 entities, 54 tags, 5 cities) shaped exactly like Qloo responses, so tests, CI and offline demos exercise the full flow. The UI badge always says which source was used.

## Run it

```bash
git clone https://github.com/kbns2000/taste-fit && cd taste-fit
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                               # fill QLOO_API_KEY (and optionally an LLM key)
set -a; . ./.env; set +a                           # or export the variables your own way
uvicorn app.main:app --reload --port 7860          # open http://127.0.0.1:7860
python -m pytest                                   # 49 tests, mock data, no network
```

Without `QLOO_API_KEY` the app runs on demo data and the header badge says so. Hackathon keys use `https://hackathon.api.qloo.com` (the default); production keys set `QLOO_BASE_URL=https://api.qloo.com`.

`python scripts/smoke_live.py` exercises every live call shape once a key exists (about 10 requests) and prints the cross-affinity both ways plus the shapes of the compare and explainability responses.

### Docker / Render (free)

```bash
docker build -t taste-fit . && docker run -p 7860:7860 -e QLOO_API_KEY=... taste-fit
```

The hosted demo runs on Render's free web service from `render.yaml` (Blueprint): Render dashboard → **New → Blueprint** → this repo. Render asks for `QLOO_API_KEY` once and stores it as a service secret; nothing is committed. The container listens on Render's `PORT` and answers `/healthz`. Free services sleep after 15 minutes without traffic, so `.github/workflows/keepalive.yml` pings `/healthz` every 10 minutes when the repo variable `DEMO_URL` is set.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `QLOO_API_KEY` | – | Qloo key; absent → demo data |
| `QLOO_BASE_URL` | `https://hackathon.api.qloo.com` | API host |
| `QLOO_MODE` | `auto` | `live` / `mock` to force a source |
| `QLOO_MAX_CALLS_PER_RUN` | `16` | per-run cap incl. LLM tool calls |
| `QLOO_DAILY_BUDGET` | `800` | per-process daily cap |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` / `ANTHROPIC_EFFORT` | – / `claude-opus-5-5` / `medium` | Claude writer |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | – | any OpenAI-compatible writer |
| `LLM_MODE` | `auto` | `anthropic` / `openai` / `off` |
| `LLM_MAX_TOOL_CALLS` | `12` | extra Qloo calls the writer may make |
| `LLM_DEADLINE_S` | `120` | writer time limit before the scripted writer takes over |
| `RUNS_PER_IP_PER_10MIN` | `8` | demo rate limit for agent runs |
| `LOOKUPS_PER_IP_PER_10MIN` | `60` | demo rate limit for type-ahead lookups |
| `MAX_ACTIVE_RUNS` | `12` | concurrent runs per process; more → HTTP 503 |

## API

| Route | Purpose |
|---|---|
| `GET /api/health` | source (live/mock), writer, quota status; never secrets |
| `GET /api/presets` | example pairs |
| `POST /api/resolve` | `{"query": "pata"}` → Qloo entity candidates |
| `POST /api/runs` | `{"mode":"fit"\|"scout","a","b","city"}` → `run_id` |
| `GET /api/runs/{id}` | state, events, result (fit, picks, zones, brief, verification, ledger) |
| `GET /api/runs/{id}/events` | Server-Sent Events of the agent's progress |

## Safe use and limits

* Qloo affinities are aggregate audience signals. Taste-Fit never sends or stores anything about individuals; inputs are public brand/artist names and a city.
* The key lives only in a server-side environment variable and only travels in the `X-Api-Key` header. It is never logged, never in the ledger, never in the browser.
* The score is a heuristic for prioritising conversations, not a forecast of campaign lift. Validate with a pilot.
* Demo-data runs are labelled; the demo world is small, so unknown names return "not found" rather than a guess.
* `/v2/analysis/compare` compares the catalog tags attached to the entities (genre, category), not audience affinities, so a brand and a band rarely share any. Shared taste comes from the cross-affinity; compare is shown as context ("different on paper, same fans").

## Repository layout

```
app/config.py            settings from env (never prints secrets)
app/qloo/client.py       live Qloo REST adapter + shared QlooClient contract + CallRecord
app/qloo/mock.py         deterministic mock with the same contract
app/qloo/fixtures.json   demo world (built by scripts/build_fixtures.py)
app/scoring.py           Taste-Fit formula
app/agent/ledger.py      evidence ledger
app/agent/tools.py       tool box over the client, LLM tool schemas
app/agent/writer.py      scripted writer + LLM agent loop
app/agent/verifier.py    brief-vs-ledger checks
app/agent/pipeline.py    fit check / partner scout orchestration
app/agent/llm/           Anthropic SDK provider, OpenAI-compatible provider
app/main.py              FastAPI app, SSE, rate limiting
web/                     UI (index.html, styles.css, app.js, Leaflet map)
tests/                   49 tests (scoring, mock, live adapter with fake transport, API, agent loop, review regressions)
render.yaml              Render Blueprint for the hosted demo
```

Built for the Qloo Agentic Hackathon · MIT License.
