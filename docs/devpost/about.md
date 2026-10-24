## Inspiration
Brand partnerships, co-marketing deals and sponsorships are still decided on follower counts, gut feel or slow panel surveys. None of those answer the question that actually decides whether a collab lands: **do the fans of brand A and the fans of partner B share a taste core, who are they, and what would fans of both show up for?** Qloo's cross-domain taste graph is the only data that can answer that across brands, music, podcasts and places at once. If Taste-Fit ran without Qloo, it would have nothing to say. So we built the tool a partnerships lead would open before a pitch meeting.

## What it does
Give Taste-Fit two entities (brand × brand, brand × artist, brand × podcast, brand × venue…) and an optional city. It then:

1. **Scores the fit** with a printed formula built only from Qloo signals: shared taste (how strongly each audience over-indexes on the other side, Qloo's cross-affinity measured in both directions) and audience alignment (Qloo demographic index). The tags both audiences over-index on explain *what* they share, and fresh reach shows what B adds.
2. **Finds activations fans of *both* over-index on** — artists for a co-branded playlist, podcasts for host-reads, places for a pop-up — using joint-signal Qloo Insights with `feature.explainability`, so every pick shows whether both audiences drive it or mostly one side.
3. **Maps the joint audience** in the city with the Qloo heatmap and names the hot zones by the nearest recommended place.
4. **Writes a decision-ready brief** (verdict, why, segments, message angles, channels, activations, risks, next steps) in which every claim carries a footnote to the exact Qloo call behind it.
5. **Verifies the brief** against that evidence ledger: unknown citations are flagged, and any entity Qloo did not return is removed and listed.

**Partner scout** mode asks Qloo which brands fans of A already over-index on and scores the top candidates, so a team can shortlist partners before committing to one.

## How we built it
- **Qloo workflows:** `/search` to resolve names; `filter.results.entities` insights in both directions for how much each audience likes the other side; two `urn:tag` insights for what each audience over-indexes on, across domains; `/v2/analysis/compare` for the catalog tags the two entities share; `urn:demographics` per entity; joint-signal `/v2/insights` with `feature.explainability=true` per target type, with `signal.location.query` / `filter.location.query` for the city; `urn:heatmap` for the map; `urn:entity:brand` insights for partner scouting; `/v2/tags` as an agent tool.
- **Agent:** a scripted core gathers the evidence deterministically (about thirteen Qloo calls per run). The writer agent — Claude through the official Anthropic SDK with strict tool schemas, or any OpenAI-compatible endpoint — may call `recommend`, `heatmap` and `find_tags` for more evidence and must submit the brief through a strict `finalize_brief` tool. A scripted writer produces the same structure when no LLM key is set, so the product is complete end to end either way.
- **Evidence ledger:** every Qloo request is numbered (endpoint, redacted parameters, result count, names, duration, cache hit). The UI renders it as a table; footnotes in the brief jump to the row.
- **Quota and safety:** per-run call cap, daily budget, six-hour cache, bounded retries honouring `Retry-After`, per-visitor rate limit, and a labelled demo-data fallback. The key lives only in a server-side environment variable and the `X-Api-Key` header.
- **Stack:** Python 3.13, FastAPI with Server-Sent Events for live agent progress, a vanilla-JS UI with Leaflet, Docker on Render's free web service. A deterministic demo world shaped like Qloo responses powers 52 tests and offline runs. Open source under the MIT license.

## Challenges we ran into
- Making an LLM useful without letting it invent audience facts. We separated evidence gathering (scripted, logged) from writing (tool-gated, strict schema) and added a verifier that checks every citation and entity against the ledger.
- Turning affinities into a score a marketer trusts. The formula is short enough to print and every term maps to numbered Qloo calls in the ledger.
- Staying inside the event quota on a public demo: caps, cache, rate limit and a visible demo-data fallback.
- Live data corrected our model of the API. `/v2/analysis/compare` compares the catalog tags attached to the entities (what they are), not their audiences, so a brand and a band never overlap there. Compare became a feature instead: "different on paper, same fans". Tag affinities also came back at about 1.0 for an audience's whole top 50, so tag overlap could say *what* two audiences share but not *how much*. The score now uses Qloo's entity-to-entity affinity in both directions, which separates Bon Iver (0.92 among Patagonia fans) from McDonald's (0.67).

## Accomplishments that we're proud of
- A brief where every sentence has a footnote to a real API call, and a verifier that catches invented entities.
- Explainability turned into a product feature: the "both / mostly A / mostly B" split on every activation pick.
- The whole flow runs with or without an LLM, and with live or demo data, and always says which it used.

## What we learned
Qloo's joint-signal insights with explainability answer a question no single-brand dataset can. The hard part is presenting it so a non-analyst trusts it: a visible ledger, a printed formula and a verifier did more for trust than any amount of prose.

## What's next for Taste-Fit
More partner types in scout mode, multi-city comparison, saved runs with side-by-side candidates, trend signals (`/v2/trending`, `bias.trends`) to flag rising partners, and a one-click PDF of the brief and ledger for pitch meetings.
