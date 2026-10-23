/* Taste-Fit UI: vanilla JS, talks to the FastAPI backend, streams agent progress over SSE. */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pct = (v) => Math.max(0, Math.min(100, Number(v) || 0));

  const state = { mode: "fit", source: "mock", run: null, es: null, map: null, layers: [] };

  // ---------- boot ----------
  fetch("/api/health").then((r) => r.json()).then((h) => {
    state.source = h.qloo_source;
    const badge = $("#source-badge");
    badge.textContent = h.qloo_source === "live" ? "live Qloo data" : "demo data (no Qloo key)";
    badge.className = "badge " + (h.qloo_source === "live" ? "badge-live" : "badge-mock");
    badge.title = h.qloo_source === "live" ? "Signals come from the Qloo Insights API" : "Deterministic demo world shaped like Qloo responses";
    $("#foot-meta").textContent = `v${h.version} · Qloo: ${h.qloo_source} · writer: ${h.llm_provider === "none" ? "scripted" : h.llm_model}`;
  }).catch(() => {});
  fetch("/api/presets").then((r) => r.json()).then((p) => {
    const box = $("#presets");
    box.innerHTML = '<span class="muted small" style="align-self:center">Try:</span>' + p.presets.map((x, i) =>
      `<button type="button" data-i="${i}">${esc(x.label)}</button>`).join("");
    box.addEventListener("click", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const x = p.presets[+b.dataset.i];
      setMode(x.mode); $("#in-a").value = x.a; $("#in-b").value = x.b || ""; $("#in-city").value = x.city || "";
      $("#run-form").requestSubmit();
    });
  }).catch(() => {});
  $("#repo-link").href = (document.querySelector('meta[name="repo"]') || {}).content || "https://github.com/kbns2000/taste-fit";

  // ---------- mode ----------
  function setMode(mode) {
    state.mode = mode;
    $$(".mode").forEach((b) => { const on = b.dataset.mode === mode; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
    $("#field-b").style.display = mode === "scout" ? "none" : "";
    $("#in-b").required = mode === "fit";
    $("#run-btn").textContent = mode === "scout" ? "Find partners" : "Run Taste-Fit";
    $("#form-hint").textContent = mode === "scout"
      ? "Qloo proposes brands that fans of A over-index on; the top three get a full Taste-Fit score."
      : "Scores the pair, finds what fans of both over-index on, maps the joint audience in the city, and writes a cited brief.";
  }
  $$(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  setMode("fit");

  // ---------- type-ahead ----------
  function attachSuggest(input, box) {
    let timer = null, last = "";
    input.addEventListener("input", () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) { box.hidden = true; return; }
      timer = setTimeout(async () => {
        if (q === last || document.activeElement !== input) return; last = q;
        try {
          const r = await fetch("/api/resolve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
          if (!r.ok) return;
          const d = await r.json();
          if (!d.results.length) { box.hidden = true; return; }
          box.innerHTML = d.results.slice(0, 6).map((e) =>
            `<button type="button" data-name="${esc(e.name)}"><span>${esc(e.name)}</span><small>${esc(e.type)}${e.disambiguation ? " · " + esc(e.disambiguation) : ""}</small></button>`).join("");
          box.hidden = false;
        } catch (_) { /* ignore */ }
      }, 260);
    });
    box.addEventListener("mousedown", (e) => {
      const b = e.target.closest("button"); if (!b) return;
      e.preventDefault(); input.value = b.dataset.name; box.hidden = true; last = input.value;
    });
    input.addEventListener("blur", () => setTimeout(() => { box.hidden = true; }, 120));
  }
  attachSuggest($("#in-a"), $("#sug-a"));
  attachSuggest($("#in-b"), $("#sug-b"));

  // ---------- run ----------
  $("#run-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = { mode: state.mode, a: $("#in-a").value.trim(), b: $("#in-b").value.trim(), city: $("#in-city").value.trim() };
    if (!body.a || (state.mode === "fit" && !body.b)) return;
    $$(".suggest").forEach((b) => { b.hidden = true; });
    resetViews();
    $("#run-btn").disabled = true;
    try {
      const r = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "could not start the run");
      state.run = d.run_id;
      listen(d.run_id);
    } catch (err) {
      showError(err.message); $("#run-btn").disabled = false;
    }
  });

  function resetViews() {
    $("#error").hidden = true; $("#results").hidden = true; $("#fit-view").hidden = true; $("#scout-view").hidden = true;
    $("#progress").hidden = false; $("#steps").innerHTML = ""; setStatus("running", "badge-muted");
    if (state.es) { state.es.close(); state.es = null; }
    $("#progress").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function setStatus(text, cls) { const s = $("#progress-status"); s.textContent = text; s.className = "badge " + cls; }
  function showError(text) { const el = $("#error"); el.hidden = false; el.textContent = text; }

  function addStep(text, cls, t) {
    $$("#steps li.active").forEach((li) => li.classList.replace("active", "done"));
    const li = document.createElement("li");
    li.className = cls; li.innerHTML = `<span>${esc(text)}</span><span class="t">${t != null ? t.toFixed(1) + "s" : ""}</span>`;
    $("#steps").appendChild(li);
  }

  function listen(id) {
    const es = new EventSource(`/api/runs/${id}/events`);
    state.es = es;
    const on = (kind, fn) => es.addEventListener(kind, (ev) => fn(JSON.parse(ev.data)));
    on("step", (d) => addStep(d.text, "active", d.t));
    on("resolved", (d) => addStep(`Resolved: ${d.a.name} (${d.a.type})${d.b ? " and " + d.b.name + " (" + d.b.type + ")" : ""}`, "done", d.t));
    on("fit", (d) => addStep(`Taste-Fit ${d.fit.fit}/100 (${d.fit.band}) from ${d.cites.join(", ")}`, "done", d.t));
    on("picks", (d) => addStep(`${d.items.length} ${d.type}${d.items.length === 1 ? "" : "s"}: ${d.items.slice(0, 3).map((p) => p.name).join(", ")}…`, "done", d.t));
    on("heatmap", (d) => addStep(`Heatmap: ${d.cells.length} cells, ${d.zones.length} hot zones`, "done", d.t));
    on("candidates", (d) => addStep(`Qloo candidates: ${d.items.slice(0, 5).map((p) => p.name).join(", ")}`, "done", d.t));
    on("scored", (d) => addStep(`${d.name}: ${d.fit}/100 (${d.band})`, "done", d.t));
    on("agent_tool", (d) => addStep(`agent called ${d.tool}(${Object.values(d.input).filter(Boolean).join(", ")})`, "tool", d.t));
    on("warn", (d) => addStep(d.text, "warn", d.t));
    on("error", (d) => { addStep(d.text, "warn", d.t); showError(d.text); });
    on("done", (d) => { $$("#steps li.active").forEach((li) => li.classList.replace("active", "done")); });
    on("end", async (d) => {
      es.close(); state.es = null; $("#run-btn").disabled = false;
      setStatus(d.status === "done" ? "complete" : "failed", d.status === "done" ? "badge-live" : "badge-weak");
      if (d.status !== "done") return;
      const r = await fetch(`/api/runs/${id}`); const st = await r.json();
      render(st.result);
    });
    es.onerror = () => { /* the browser retries; the end event closes it */ };
  }

  // ---------- render ----------
  function render(res) {
    $("#results").hidden = false;
    renderLedger(res);
    if (res.mode === "scout") { renderScout(res); return; }
    renderFit(res);
  }

  function renderFit(res) {
    $("#fit-view").hidden = false;
    const f = res.fit, a = res.a, b = res.b;
    $("#pair-title").textContent = `${a.name} × ${b.name}${res.city ? " · " + res.city : ""}`;
    const band = $("#band-badge"); band.textContent = f.band + " fit"; band.className = "badge badge-" + f.band;
    $("#score-num").textContent = Math.round(f.fit);
    const ring = $("#ring-fg");
    ring.style.stroke = f.band === "strong" ? "var(--both)" : f.band === "workable" ? "var(--warn)" : "var(--bad)";
    requestAnimationFrame(() => { ring.style.strokeDashoffset = String(326.7 * (1 - pct(f.fit) / 100)); });
    const set = (id, v) => { $("#bar-" + id).style.width = pct(v) + "%"; $("#val-" + id).textContent = Math.round(v); };
    set("shared", f.shared_taste); set("align", f.audience_alignment); set("balance", f.balance);
    $("#formula").textContent = `fit = 0.5×${f.shared_taste} + 0.3×${f.audience_alignment} + 0.2×${f.balance} = ${f.fit}   ·   balance = 100 − |fresh_reach ${f.fresh_reach}% − 40| × 2.5`;
    $("#fresh-note").textContent = `${f.fresh_reach}% of ${b.name}'s top taste tags are new to ${a.name}'s audience. Around 40% is the sweet spot: enough new reach without losing the shared core. Sources: ${res.fit_cites.join(", ")}.`;

    // demographics
    const dc = $("#demo-chart"); dc.innerHTML = "";
    if (f.demographics.length) {
      const maxv = Math.max(0.3, ...f.demographics.map((d) => Math.max(Math.abs(d.a), Math.abs(d.b))));
      f.demographics.forEach((d) => {
        const row = document.createElement("div"); row.className = "demo-row";
        const w = (v) => (Math.abs(v) / maxv) * 50;
        const pos = (v) => (v >= 0 ? `left:50%;width:${w(v)}%` : `right:50%;width:${w(v)}%`);
        row.innerHTML = `<span class="lab">${esc(d.label)}</span><div class="demo-bars" title="${esc(a.name)} ${d.a.toFixed(2)} · ${esc(b.name)} ${d.b.toFixed(2)}"><i class="a" style="${pos(d.a)}"></i><i class="b" style="${pos(d.b)}"></i></div>`;
        dc.appendChild(row);
      });
      dc.insertAdjacentHTML("beforeend", `<div class="legend"><i class="dot a"></i>${esc(a.name)} <i class="dot b"></i>${esc(b.name)}</div>`);
      $("#demo-note").textContent = (f.agreeing_segments.length ? `Both over-index on ${f.agreeing_segments.join(", ")}. ` : "No segment where both over-index. ")
        + (f.diverging_segments.length ? `They pull apart on ${f.diverging_segments.join(", ")}.` : "");
    } else {
      dc.innerHTML = '<p class="muted small">Qloo returned no demographic index for this pair.</p>';
    }

    // overlap
    const ov = $("#overlap");
    const tagRow = (t) => `<div class="tagrow"><span>${esc(t.name)}</span><div class="mini" title="${esc(a.name)} ${t.a_affinity} · ${esc(b.name)} ${t.b_affinity}"><i class="a" style="width:${pct(t.a_affinity * 100)}%"></i><i class="b" style="width:${pct(t.b_affinity * 100)}%"></i></div></div>`;
    ov.innerHTML = `
      <div><h3>Shared (${f.shared_tags.length})</h3>${f.shared_tags.map(tagRow).join("") || '<p class="muted small">No shared tags.</p>'}</div>
      <div><h3>Only ${esc(a.name)}</h3>${f.a_only_tags.map((t) => `<span class="chip a">${esc(t.name)}</span>`).join("") || '<p class="muted small">—</p>'}</div>
      <div><h3>Only ${esc(b.name)} <small>(fresh reach)</small></h3>${f.b_only_tags.map((t) => `<span class="chip b">${esc(t.name)}</span>`).join("") || '<p class="muted small">—</p>'}</div>`;

    // picks
    const pk = $("#picks"); pk.innerHTML = "";
    const kinds = Object.keys(res.picks);
    if (!kinds.length) pk.innerHTML = '<p class="muted small">No joint recommendations were returned.</p>';
    kinds.forEach((kind) => {
      const items = res.picks[kind]; if (!items.length) return;
      const g = document.createElement("div"); g.className = "pick-group";
      g.innerHTML = `<h3>${esc(kind.replace("_", " "))}s ${res.city && kind === "place" ? "in " + esc(res.city) : ""} <span class="cite" data-cite="${esc((res.pick_cites[kind] || []).join(","))}">${esc((res.pick_cites[kind] || []).join(" "))}</span></h3>` +
        items.slice(0, 5).map((p) => {
          const ea = p.explainability && p.explainability[a.entity_id] != null ? p.explainability[a.entity_id] : null;
          const eb = p.explainability && p.explainability[b.entity_id] != null ? p.explainability[b.entity_id] : null;
          const split = ea != null && eb != null ? `<div class="split" title="explainability: ${esc(a.name)} ${Math.round(ea * 100)}% · ${esc(b.name)} ${Math.round(eb * 100)}%"><i class="a" style="width:${pct(ea * 100)}%"></i><i class="b" style="width:${pct(eb * 100)}%"></i></div>` : "";
          return `<div class="pick"><div class="row"><b>${esc(p.name)}</b><span class="aff">affinity ${p.affinity != null ? p.affinity.toFixed(2) : "–"}</span></div>${split}<div class="meta"><span class="driver ${esc(p.driver)}">${esc(driverLabel(p.driver, a, b))}</span>${p.locality ? `<span>${esc(p.locality)}</span>` : ""}<span>${esc(p.format)}</span></div></div>`;
        }).join("");
      pk.appendChild(g);
    });

    // map
    renderMap(res);

    // brief
    renderBrief(res);
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function driverLabel(d, a, b) {
    return d === "both" ? "both audiences drive it" : d === "mostly_a" ? "mostly " + a.name + " fans" : d === "mostly_b" ? "mostly " + b.name + " fans" : "joint signal";
  }

  function renderMap(res) {
    const card = $("#map-card");
    const hasMap = (res.heatmap && res.heatmap.length) || (res.picks.place && res.picks.place.length);
    if (!hasMap) { card.hidden = true; return; }
    card.hidden = false;
    $("#map-title").textContent = `Where the joint audience lives · ${res.city}`;
    $("#zones").innerHTML = (res.zones || []).map((z, i) => `<li>Zone ${i + 1}${z.near_area ? " · " + esc(z.near_area) : ""}${z.near ? " (near " + esc(z.near) + ")" : ""} — affinity ${z.affinity}, popularity ${z.popularity} <span class="cite" data-cite="${esc(res.zone_cites.join(","))}">${esc(res.zone_cites.join(" "))}</span></li>`).join("");
    if (typeof L === "undefined") { $("#map").innerHTML = '<p class="muted small" style="padding:1rem">Map library could not load; hot zones are listed below.</p>'; return; }
    if (!state.map) {
      state.map = L.map("map", { scrollWheelZoom: false });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { maxZoom: 18, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>' }).addTo(state.map);
    }
    state.layers.forEach((l) => state.map.removeLayer(l)); state.layers = [];
    const bounds = [];
    (res.heatmap || []).forEach((c) => {
      const op = 0.08 + 0.55 * pct(c.affinity * 100) / 100;
      const l = L.circle([c.lat, c.lon], { radius: 420, stroke: false, fillColor: "#5b8def", fillOpacity: op }).addTo(state.map);
      state.layers.push(l); bounds.push([c.lat, c.lon]);
    });
    (res.zones || []).forEach((z, i) => {
      const l = L.circleMarker([z.lat, z.lon], { radius: 10, color: "#2fa36b", weight: 3, fillColor: "#fff", fillOpacity: 1 }).addTo(state.map)
        .bindTooltip(`Hot zone ${i + 1}${z.near ? " · near " + z.near : ""} · affinity ${z.affinity}`);
      state.layers.push(l); bounds.push([z.lat, z.lon]);
    });
    (res.picks.place || []).forEach((p) => {
      if (p.lat == null || p.lon == null) return;
      const l = L.marker([p.lat, p.lon]).addTo(state.map).bindPopup(`<b>${esc(p.name)}</b><br>${esc(p.address || p.locality || "")}<br>affinity ${p.affinity != null ? p.affinity.toFixed(2) : "–"}`);
      state.layers.push(l); bounds.push([p.lat, p.lon]);
    });
    if (bounds.length) state.map.fitBounds(bounds, { padding: [20, 20] });
    setTimeout(() => state.map.invalidateSize(), 100);
  }

  function cites(list) { return (list || []).map((c) => `<a class="cite" data-cite="${esc(c)}" href="#ledger">${esc(c)}</a>`).join(""); }

  function renderBrief(res) {
    const br = res.brief, v = res.verification;
    const vb = $("#verdict-badge"); vb.textContent = br.verdict.replace(/_/g, " "); vb.className = "badge badge-" + br.verdict;
    const ok = $("#verify-badge");
    const errs = v.issues.filter((i) => i.level === "error").length, warns = v.issues.length - errs;
    ok.textContent = v.passed ? `verified · ${v.checked.citations} citations, ${v.checked.entities} entities` : `verifier flagged ${errs} issue${errs === 1 ? "" : "s"}`;
    ok.className = "badge " + (v.passed ? "badge-live" : "badge-weak");
    const det = $("#verify-details"); det.hidden = !v.issues.length;
    $("#verify-list").innerHTML = v.issues.map((i) => `<li class="${esc(i.level)}"><b>${esc(i.where)}</b>: ${esc(i.text)}</li>`).join("");
    const w = res.writer || {};
    $("#writer-note").textContent = w.provider === "scripted" || w.fallback
      ? "Written by the scripted writer from the evidence" + (w.fallback ? ` (LLM unavailable: ${w.error || "error"})` : "") + "."
      : `Written by ${w.model} with ${w.tool_calls || 0} extra Qloo tool call${w.tool_calls === 1 ? "" : "s"}, then verified against the ledger.`;
    const list = (arr) => `<ul>${(arr || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
    $("#brief").innerHTML = `
      <p class="verdict">${esc(br.headline)}</p>
      <h3>Why</h3><ul>${br.why.map((w) => `<li>${esc(w.text)} ${cites(w.cites)}</li>`).join("")}</ul>
      <h3>Target segments</h3><p>${br.target_segments.map((s) => `<span class="chip">${esc(s)}</span>`).join("")}</p>
      <h3>Message angles</h3>${list(br.message_angles)}
      <h3>Channels</h3>${list(br.channels)}
      <h3>Activations</h3>${br.activations.map((x) => `<div class="act"><b>${esc(x.title)}</b><span class="fmt">${esc(x.format)}</span><p>${esc(x.rationale)} ${cites(x.cites)}</p>${x.entities.length ? `<p>${x.entities.map((e) => `<span class="chip">${esc(e)}</span>`).join("")}</p>` : ""}${x.unverified_entities && x.unverified_entities.length ? `<p class="unverified">Removed (not in Qloo evidence): ${esc(x.unverified_entities.join(", "))}</p>` : ""}</div>`).join("")}
      <h3>Risks</h3>${list(br.risks)}
      <h3>Next steps</h3>${list(br.next_steps)}`;
  }

  function renderScout(res) {
    $("#scout-view").hidden = false;
    $("#scout-title").textContent = `Partners for ${res.a.name}${res.city ? " · " + res.city : ""}`;
    $("#scout-sub").textContent = `Qloo proposed ${res.ranked.length + res.more_candidates.length} ${res.partner_type}s that fans of ${res.a.name} over-index on (${res.candidate_cites.join(", ")}); the top ${res.ranked.length} were scored.`;
    $("#scout-list").innerHTML = res.ranked.map((r, i) => `
      <div class="scout-item">
        <div class="num">${Math.round(r.fit.fit)}<small>${esc(r.fit.band)}</small></div>
        <div><b>${i + 1}. ${esc(r.entity.name)}</b> <span class="muted small">${esc(r.entity.type)} · Qloo affinity ${r.qloo_affinity != null ? r.qloo_affinity.toFixed(2) : "–"}</span>
          <div class="subs"><span>shared taste ${Math.round(r.fit.shared_taste)}</span><span>alignment ${Math.round(r.fit.audience_alignment)}</span><span>fresh reach ${Math.round(r.fit.fresh_reach)}%</span><span>${cites(r.fit_cites)}</span></div>
          <div>${r.fit.shared_tags.slice(0, 5).map((t) => `<span class="chip">${esc(t.name)}</span>`).join("")}</div>
        </div>
        <button type="button" data-b="${esc(r.entity.name)}">Full fit check →</button>
      </div>`).join("");
    $("#scout-more").textContent = res.more_candidates.length ? "Also proposed by Qloo (not scored): " + res.more_candidates.map((m) => m.entity.name).join(", ") : "";
    state.scout = res;
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  }
  $("#scout-list").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b || !state.scout) return;
    setMode("fit"); $("#in-a").value = state.scout.a.name; $("#in-b").value = b.dataset.b; $("#in-city").value = state.scout.city || "";
    $("#run-form").requestSubmit();
  });

  function renderLedger(res) {
    const tb = $("#ledger tbody");
    tb.innerHTML = res.ledger.map((e) => `
      <tr id="row-${esc(e.id)}">
        <td><b>${esc(e.id)}</b></td>
        <td><code>${esc(e.endpoint)}</code><br><span class="src-${esc(e.source)} small">${esc(e.source)}${e.cached ? " · cached" : ""}</span></td>
        <td>${esc(e.purpose)}</td>
        <td><code>${esc(formatParams(e.params))}</code></td>
        <td>${e.status === "error" ? `<span class="src-mock">${esc(e.error || "error")}</span>` : `${e.count}${e.names && e.names.length ? "<br><span class=\"muted\">" + esc(e.names.slice(0, 4).join(", ")) + (e.names.length > 4 ? "…" : "") + "</span>" : ""}`}</td>
        <td>${e.duration_ms}</td>
      </tr>`).join("");
    $("#ledger-summary").textContent = `${res.qloo_calls} Qloo call${res.qloo_calls === 1 ? "" : "s"} · ${res.source} data · ${(res.duration_ms / 1000).toFixed(1)}s${res.fallback_reason ? " · fell back to demo data: " + res.fallback_reason : ""}`;
  }

  function formatParams(p) {
    return Object.entries(p || {}).map(([k, v]) => `${k}=${Array.isArray(v) ? v.map(shortId).join(",") : shortId(v)}`).join("\n");
  }
  function shortId(v) { const s = String(v); return /^[0-9A-F]{8}-[0-9A-F]{4}-/i.test(s) ? s.slice(0, 8) + "…" : s; }

  document.addEventListener("click", (e) => {
    const c = e.target.closest(".cite"); if (!c) return;
    e.preventDefault();
    const ids = (c.dataset.cite || "").split(",").filter(Boolean);
    $$("#ledger tr.hl").forEach((r) => r.classList.remove("hl"));
    ids.forEach((id) => { const row = $("#row-" + id); if (row) row.classList.add("hl"); });
    const first = ids.length && $("#row-" + ids[0]);
    if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
  });
})();
