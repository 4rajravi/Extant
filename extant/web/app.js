// Extant's page. It shows what the API returns and computes nothing: dates, counts, scores,
// quotes and order are displayed exactly as the response gives them. The only text this file
// adds is labels: names for graph nodes, refusal reasons, caveats and drop reasons.
"use strict";

// ------------------------------------------------------------------ labels (copy, not facts)

const NODE_LABELS = {
  parse: "Reading the question",
  refuse_early: "Declining before retrieval",
  resolve_reference: "Looking up the cited circular",
  retrieve: "Searching the directions in force",
  expand: "Adding neighbouring paragraphs",
  gate: "Supersession and entity gate",
  authority: "Checking each paragraph's authority",
  compose: "Drafting claims from the paragraphs",
  verify: "Checking every quote, date and number",
  judge: "Second reading of paraphrased claims",
  retry: "Redrafting once after a failed check",
  finalize: "Assembling the response",
};

const REASONS = {
  out_of_scope: "Outside what Extant covers",
  computation_requested: "Extant does not calculate or advise",
  entity_required: "Which kind of entity is asking?",
  no_support_found: "No provision supports an answer",
  superseded_only: "Only superseded provisions were found",
  non_binding_support_only: "Only non-binding material was found",
  verification_failed: "The draft did not pass verification",
  cap_reached: "Today's answers are used up",
};

const CAVEATS = {
  not_legal_advice:
    "The question asks what you should do. This answer reports what the directions say; applying them to your facts is a professional judgement.",
  non_binding_cited:
    "The answer cites binding directions and non-binding material together. The binding flag on each citation says which is which.",
  as_of_assumed:
    "No date was given, so the answer holds on the date shown, today in India.",
  amendment_pending:
    "A cited paragraph stops applying on a later date. Expand the citation to see its end date.",
  degraded_retrieval:
    "Retrieval ran in a reduced mode, so this answer may have missed a provision.",
};

const DEGRADED = {
  lexical_only: "Search used keywords only: the embedding service could not be reached.",
  retrieval_only: "A model could not be reached, so the provisions are shown without prose.",
  cached_examples: "No live answer was possible. Cached examples are offered instead.",
  stale_examples: "The cached examples were answered on an earlier build.",
  static_page: "No live answer was possible and no examples are available.",
};

const DROP_REASONS = {
  superseded: "Superseded on the date asked",
  out_of_entity_scope: "Does not apply to this entity type",
  not_yet_effective: "Not yet in force on the date asked",
  non_binding_only: "Not binding",
};

const OUTCOME_TITLES = {
  answer: "Answer",
  retrieval_only: "Provisions found, no composed answer",
  unavailable: "Extant cannot answer right now",
};

// ------------------------------------------------------------------ state

const page = {
  build: null, // the index_version this service runs, as the API reports it
  examples: null, // the GET /examples response
  entityLabels: new Map(), // code -> label, from GET /entity_types
  controller: null, // the request in flight
  paragraphs: new Map(), // para_id -> Promise of the GET /paragraph response
  lastSteps: new Map(), // node -> its row in the current attempt
};

const $ = (id) => document.getElementById(id);

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const shown = (value, none) => (value === null || value === undefined ? none : String(value));
const absent = (value) => value === null || value === undefined;
const code = (text) => h("code", { text });

// ------------------------------------------------------------------ start

document.addEventListener("DOMContentLoaded", () => {
  $("ask-form").addEventListener("submit", onSubmit);
  loadExamples();
  loadEntityTypes();
});

async function loadExamples() {
  const list = $("examples-list");
  const status = $("examples-status");
  try {
    const res = await fetch("/examples", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(String(res.status));
    const body = await res.json();
    page.examples = body;
    setBuild(body.index_version);
    if (body.stale || body.examples.length === 0) status.textContent = body.message;
    list.replaceChildren(...body.examples.map((ex) => h("li", {}, exampleButton(ex))));
  } catch {
    status.textContent = "The examples could not be loaded.";
    loadBuildFromLiveness();
  }
}

async function loadBuildFromLiveness() {
  try {
    const res = await fetch("/health/live");
    const body = await res.json();
    setBuild(body.index_version);
  } catch {
    $("build-version").textContent = "unknown";
  }
}

function setBuild(version) {
  page.build = version;
  $("build-version").textContent = version;
}

async function loadEntityTypes() {
  const select = $("entity");
  try {
    const res = await fetch("/entity_types", { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(String(res.status));
    const body = await res.json();
    for (const t of body.entity_types) {
      page.entityLabels.set(t.code, t.label);
      const indent = t.parent ? " " : "";
      select.append(h("option", { value: t.code, text: `${indent}${t.label} (${t.code})` }));
    }
  } catch {
    select.after(
      h("span", { class: "fine", text: "Entity types could not be loaded; you can still ask without one." }),
    );
  }
}

function exampleButton(ex) {
  return h(
    "button",
    { type: "button", class: "example", onclick: () => showCachedExample(ex) },
    h("span", { class: "example-question", text: ex.question }),
    h("span", {
      class: "example-meta",
      text: `${ex.entity_type ? `entity ${ex.entity_type} · ` : ""}as of ${ex.as_of}`,
    }),
    h("span", { class: "example-cta", "aria-hidden": "true", text: "Show answer" }),
  );
}

function showAnswerSection() {
  // The reader asked for something: bring the Answer section into view, once, at the start.
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  $("answer-section").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
}

// ------------------------------------------------------------------ asking

async function onSubmit(event) {
  event.preventDefault();
  const question = $("question").value.trim();
  const status = $("form-status");
  if (question.length < 3) {
    status.textContent = "Write a question of at least three characters.";
    $("question").focus();
    return;
  }
  status.textContent = "";
  const body = {
    question,
    entity: $("entity").value || null,
    as_of: $("as-of").value || null, // the input's own ISO value, sent as it is
  };
  await ask(body);
}

async function ask(body) {
  if (page.controller) page.controller.abort();
  const controller = new AbortController();
  page.controller = controller;
  const button = $("ask-button");
  button.setAttribute("aria-disabled", "true");
  startProgress();
  waitingForResult();
  showAnswerSection();
  let finished = false;
  try {
    const res = await fetch("/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) {
      await rejected(res);
      finished = true;
      return;
    }
    for await (const { event, data } of serverEvents(res.body)) {
      if (controller.signal.aborted) return;
      if (event === "node_started") nodeStarted(data);
      else if (event === "node_finished") nodeFinished(data);
      else if (event === "restarted") restarted(data);
      else if (event === "response") {
        finished = true;
        progressDone(data.response);
        renderResponse(data.response, body.question);
      } else if (event === "failure") {
        finished = true;
        renderFailure(data.detail);
      }
    }
    if (!finished) {
      renderFailure(
        "The connection closed before the answer arrived. Nothing is shown because nothing was verified.",
      );
    }
  } catch (err) {
    if (err.name === "AbortError") return;
    renderFailure("Extant could not be reached. Check the connection and ask again.");
  } finally {
    if (page.controller === controller) {
      page.controller = null;
      button.removeAttribute("aria-disabled");
    }
  }
}

async function* serverEvents(stream) {
  // Server sent events over a POST response: blocks separated by a blank line, each with an
  // event line and one data line of JSON.
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
    }
  }
}

async function rejected(res) {
  let detail = `The request was refused (HTTP ${res.status}).`;
  try {
    const body = await res.json();
    if (res.status === 429) {
      detail = `${body.detail} Try again in ${body.retry_after_seconds} s.`;
    } else if (res.status === 422) {
      detail = "The question could not be sent: check its length and the date.";
    } else if (typeof body.detail === "string") {
      detail = body.detail;
    }
  } catch {
    /* keep the generic sentence */
  }
  $("form-status").textContent = detail;
  resetProgress("Nothing was attempted.");
  resultPlaceholder("Nothing was asked: the request was refused before it reached the graph.");
}

// ------------------------------------------------------------------ the progress panel

function resetProgress(text) {
  $("progress-now").textContent = text;
  $("progress-steps").replaceChildren();
  $("checks-list").replaceChildren();
  $("progress-checks").hidden = true;
  page.lastSteps = new Map();
}

function startProgress() {
  resetProgress("Sent. Waiting for the first step.");
}

function nodeStarted(ev) {
  const label = NODE_LABELS[ev.node] || ev.node;
  const row = h(
    "li",
    { class: "step", dataset: { state: "running", node: ev.node } },
    h("span", { class: "step-mark", "aria-hidden": "true", text: "›" }),
    h("span", {}, h("span", { class: "step-name", text: label }), h("span", { class: "step-node", text: ev.node })),
    h("span", { class: "step-time" }),
    h("div", { class: "step-facts" }),
  );
  $("progress-steps").append(row);
  page.lastSteps.set(ev.node, row);
  $("progress-now").textContent = `${label}…`;
}

function nodeFinished(ev) {
  const row = page.lastSteps.get(ev.node);
  if (!row) return;
  row.dataset.state = "done";
  row.querySelector(".step-mark").textContent = "✓";
  row.querySelector(".step-time").textContent = `${ev.latency_ms} ms`;
  row.querySelector(".step-facts").replaceChildren(...stepFacts(ev));
  if (ev.checks && ev.checks.length) showChecks(ev.checks);
}

function stepFacts(ev) {
  const facts = [];
  const line = (...parts) => facts.push(h("span", {}, ...parts));
  if (ev.path) line("route ", code(ev.path));
  if (ev.entity_type) line("entity ", code(ev.entity_type));
  if (ev.as_of) line(`as of ${ev.as_of}`, ev.as_of_source ? ` (${ev.as_of_source})` : "");
  if (ev.candidates !== undefined) {
    line(ev.node === "retrieve" ? `${ev.candidates} paragraphs retrieved` : `${ev.candidates} paragraphs held`);
  }
  if (ev.neighbours !== undefined) line(`${ev.neighbours} neighbouring paragraphs fetched`);
  for (const d of ev.dropped || []) {
    facts.push(
      h("span", { class: "dropped-inline" }, "set aside ", code(d.para_id), `: ${DROP_REASONS[d.reason] || d.reason}`),
    );
  }
  if ((ev.node === "gate" || ev.node === "authority") && !(ev.dropped || []).length) line("nothing set aside");
  if (ev.drafted !== undefined) line(`${ev.drafted} claims drafted`);
  if (ev.verified !== undefined) line(`${ev.verified} claims passed the quote, date and number checks`);
  for (const marker of ev.degraded || []) line("degraded: ", code(marker));
  if (ev.refusal_reason) line("will refuse: ", code(ev.refusal_reason));
  if (ev.model_calls) line(ev.model_calls === 1 ? "1 model call" : `${ev.model_calls} model calls`);
  return facts;
}

function showChecks(checks) {
  $("progress-checks").hidden = false;
  $("checks-list").replaceChildren(
    ...checks.map((c) =>
      h(
        "li",
        { class: "check", dataset: { passed: String(c.passed) } },
        `${c.name} `,
        h("span", { class: "check-verdict", text: c.passed ? "passed" : "failed" }),
        c.detail ? ` ${c.detail}` : "",
      ),
    ),
  );
}

function restarted(ev) {
  for (const row of $("progress-steps").children) {
    if (row.dataset.state === "running") row.dataset.state = "abandoned";
  }
  $("progress-steps").append(
    h("li", { class: "step restart" }, h("span", { class: "step-mark", text: "↺" }), h("span", {
      text: `Starting again, attempt ${ev.attempt}: ${ev.reason}.`,
    })),
  );
  $("checks-list").replaceChildren();
  $("progress-checks").hidden = true;
  page.lastSteps = new Map();
}

function progressDone(response) {
  const r = response.result;
  const steps = $("progress-steps").children.length;
  let text;
  if (r.outcome === "answer") text = "Done. Every claim below passed its checks.";
  else if (r.outcome === "refusal") text = "Done. Extant declined, and says why.";
  else if (r.outcome === "retrieval_only") text = "Done. Provisions shown without a composed answer.";
  else text = "Done. No live answer was possible.";
  if (steps === 0) text += " The graph did not run.";
  $("progress-now").textContent = text;
}

// ------------------------------------------------------------------ the result

function resultSheet(classes, outcome) {
  const body = $("result-body");
  body.className = `result-body ${classes}`.trim();
  body.dataset.outcome = outcome;
  body.replaceChildren();
  return body;
}

function resultPlaceholder(text) {
  const body = resultSheet("", "none");
  body.append(h("p", { class: "placeholder", text }));
}

function waitingForResult() {
  $("result").setAttribute("aria-busy", "true");
  resultPlaceholder("The answer appears here once, after it has been verified. The working is shown as it happens.");
  transparencyPlaceholder("Shown with the answer.");
}

function head(title, meta) {
  return h(
    "div",
    { class: "result-head" },
    h("h3", { class: "result-kind", text: title }),
    h("p", { class: "result-meta" }, ...meta),
  );
}

function metaItem(label, value) {
  return h("span", {}, `${label} `, h("b", { text: value }));
}

function responseMeta(response) {
  return [
    metaItem("as of", response.as_of),
    metaItem("entity", shown(response.entity_type, "not specified")),
    h("span", {}, "build ", code(response.index_version)),
  ];
}

function renderResponse(response, question) {
  $("result").setAttribute("aria-busy", "false");
  const r = response.result;
  const degraded = response.degraded.length > 0 || (r.caveats || []).includes("degraded_retrieval");
  const declined = r.outcome === "refusal";
  const sheet = resultSheet(`${degraded ? "is-degraded" : ""} ${declined ? "is-declined" : ""}`, r.outcome);
  if (r.reason) sheet.dataset.reason = r.reason;

  const title = declined ? REASONS[r.reason] || "Extant declined to answer" : OUTCOME_TITLES[r.outcome];
  sheet.append(head(title, responseMeta(response)));
  if (question) sheet.append(h("p", { class: "asked" }, "Asked: ", h("q", { text: question })));
  sheet.append(flags(response.degraded, r.caveats || [], response.notice));

  const v = response.index_version;
  if (r.outcome === "answer") {
    sheet.append(claimsList(r.claims, v));
  } else if (r.outcome === "refusal") {
    sheet.append(h("p", { class: "prose", text: r.message }));
    sheet.append(h("p", { class: "reason-code", text: `reason: ${r.reason}` }));
    if (r.nearest.length) {
      sheet.append(
        h(
          "div",
          { class: "nearest" },
          h("h4", { class: "minor-label", text: "Found, but not relied on" }),
          ...r.nearest.map((c) => citation(c, v)),
        ),
      );
    }
    if (r.examples && r.examples.length) sheet.append(examplesOffer(r.examples));
  } else if (r.outcome === "retrieval_only") {
    sheet.append(h("p", { class: "prose", text: r.message }));
    sheet.append(h("div", { class: "citations" }, ...r.provisions.map((c) => citation(c, v, { open: true }))));
  } else {
    sheet.append(h("p", { class: "prose", text: r.message }));
    if (r.examples && r.examples.length) sheet.append(examplesOffer(r.examples));
  }
  sheet.append(
    h(
      "p",
      { class: "accounting" },
      h("span", {}, "latency_ms ", code(String(response.latency_ms))),
      h("span", {}, "cost_eur ", code(String(response.cost_eur))),
    ),
  );
  renderTransparency(response);
}

function flags(degraded, caveats, notice) {
  const box = h("div", { class: "flags" });
  for (const marker of degraded) {
    box.append(h("p", { class: "flag", dataset: { kind: "degraded" } }, DEGRADED[marker] || "Degraded.", " ", code(marker)));
  }
  for (const caveat of caveats) {
    box.append(h("p", { class: "flag", dataset: { kind: "caveat" } }, CAVEATS[caveat] || "", " ", code(caveat)));
  }
  if (notice) box.append(h("p", { class: "flag", dataset: { kind: "notice" }, text: notice }));
  return box;
}

function claimsList(claims, indexVersion) {
  return h(
    "ol",
    { class: "claims" },
    ...claims.map((claim) =>
      h(
        "li",
        { class: "claim" },
        h("p", { class: "claim-text", text: claim.text }),
        h("div", { class: "citations" }, ...claim.citations.map((c) => citation(c, indexVersion))),
      ),
    ),
  );
}

function examplesOffer(examples) {
  return h(
    "div",
    { class: "nearest" },
    h("h4", { class: "minor-label", text: "Cached examples, answered earlier" }),
    h("ul", { class: "example-list" }, ...examples.map((ex) => h("li", {}, exampleButton(ex)))),
  );
}

function renderFailure(detail) {
  $("result").setAttribute("aria-busy", "false");
  const sheet = resultSheet("", "failure");
  sheet.append(head("Nothing was answered", []), h("p", { class: "prose", text: detail }));
  $("progress-now").textContent = "Stopped.";
  transparencyPlaceholder("Nothing to show: no answer was produced.");
}

// ------------------------------------------------------------------ cached examples

function showCachedExample(ex) {
  if (page.controller) page.controller.abort();
  resetProgress("No graph ran. This answer was computed earlier and is served from the cache, so no model was called.");
  $("result").setAttribute("aria-busy", "false");
  const answer = ex.answer;
  const sheet = resultSheet("is-cached", "cached_example");
  showAnswerSection();
  sheet.append(
    head("Cached example", [
      metaItem("as of", ex.as_of),
      metaItem("entity", shown(ex.entity_type, "not specified")),
      h("span", {}, "answered on build ", code(ex.index_version)),
    ]),
    h("p", { class: "asked" }, "Asked: ", h("q", { text: ex.question })),
  );
  if (page.build && ex.index_version !== page.build) {
    sheet.append(
      h(
        "p",
        { class: "flag", dataset: { kind: "stale" } },
        "This example was answered on build ",
        code(ex.index_version),
        "; the service now runs ",
        code(page.build),
        ", so a cited paragraph may have changed since.",
      ),
    );
  }
  sheet.append(flags([], answer.caveats, null), claimsList(answer.claims, ex.index_version));
  transparencyPlaceholder(
    "A cached example keeps its answer only. The paragraphs retrieved and set aside when it was answered were not kept with it.",
  );
}

// ------------------------------------------------------------------ the transparency panel

function transparencyPlaceholder(text) {
  $("transparency-body").replaceChildren(h("p", { class: "placeholder", text }));
}

function renderTransparency(response) {
  const body = $("transparency-body");
  const parts = [];
  parts.push(h("h3", { class: "minor-label", text: "Set aside by the gate" }));
  if (response.dropped.length) {
    parts.push(
      h(
        "ul",
        { class: "dropped-list", id: "dropped-list" },
        ...response.dropped.map((d) =>
          h(
            "li",
            { class: "dropped-row" },
            code(d.para_id),
            h("span", { class: "dropped-reason" }, `${DROP_REASONS[d.reason] || d.reason} `, code(d.reason)),
            h("span", { text: d.detail }),
          ),
        ),
      ),
    );
  } else {
    parts.push(h("p", { class: "fine", id: "dropped-list", text: "Nothing was set aside." }));
  }
  parts.push(h("h3", { class: "minor-label", text: "Retrieved" }));
  if (response.retrieved.length) {
    const score = (label, value) => h("span", {}, `${label} `, code(shown(value, "–")));
    parts.push(
      h(
        "ol",
        { class: "retrieved-list", id: "retrieved-list", "aria-label": "Paragraphs retrieved, in the order retrieval returned them" },
        ...response.retrieved.map((r) =>
          h(
            "li",
            { class: "retrieved-row", dataset: { expanded: String(r.expanded) } },
            h("code", { class: "retrieved-id", text: r.para_id }),
            h(
              "span",
              { class: "retrieved-scores" },
              h("span", { class: "retrieved-how", text: r.expanded ? "neighbour" : "ranked" }),
              score("fusion", r.fusion_score),
              score("lexical", r.lexical_rank),
              score("dense", r.dense_rank),
            ),
          ),
        ),
      ),
    );
    parts.push(
      h("p", { class: "fine", text: "In retrieval's order. A neighbour was added beside a ranked paragraph and has no score." }),
    );
  } else {
    parts.push(h("p", { class: "fine", text: "Nothing was retrieved for this response." }));
  }
  body.replaceChildren(...parts);
}

// ------------------------------------------------------------------ citations

let citationCount = 0;

function citation(c, indexVersion, { open = false } = {}) {
  const node = $("citation-template").content.firstElementChild.cloneNode(true);
  const button = node.querySelector(".cite");
  const panel = node.querySelector(".cite-panel");
  const id = `cite-${++citationCount}`;
  panel.id = id;
  button.setAttribute("aria-controls", id);
  node.querySelector(".cite-number").textContent = `¶ ${c.paragraph_number}`;
  node.querySelector(".cite-title").textContent = c.doc_title;
  node.dataset.paraId = c.para_id;
  let filled = false;
  const toggle = () => {
    const expanded = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!expanded));
    panel.hidden = expanded;
    if (!expanded && !filled) {
      filled = true;
      fillCitation(node, c, indexVersion);
    }
  };
  button.addEventListener("click", toggle);
  if (open) toggle();
  return node;
}

function fillCitation(node, c, indexVersion) {
  const facts = node.querySelector(".cite-facts");
  const row = (label, ...value) => [h("dt", { text: label }), h("dd", {}, ...value)];
  const scope = c.entity_scope.map((e, i) => [
    i ? ", " : "",
    code(e),
    page.entityLabels.has(e) ? ` ${page.entityLabels.get(e)}` : "",
  ]);
  facts.replaceChildren(
    ...row("Instrument", c.doc_title),
    ...row("Paragraph", c.paragraph_number),
    ...row("Identifier", code(c.para_id)),
    ...row("In force from", c.effective_from),
    ...row("In force until", shown(c.effective_to, "no end date in this build")),
    ...row("Entity scope", ...scope),
    ...row("Binding", c.binding ? "yes" : "no"),
    ...row("Authority rank", String(c.authority_rank)),
  );
  const href = absent(c.source_page) ? c.source_url : `${c.source_url}#page=${c.source_page}`;
  node.querySelector(".cite-source").replaceChildren(
    h(
      "a",
      { href, target: "_blank", rel: "noopener noreferrer" },
      absent(c.source_page) ? "Open the direction on rbi.org.in" : `Open the source at page ${c.source_page}`,
    ),
  );
  showParagraph(node, c, indexVersion);
}

function fetchParagraph(paraId) {
  if (!page.paragraphs.has(paraId)) {
    const pending = fetch(`/paragraph/${encodeURIComponent(paraId)}`, { headers: { Accept: "application/json" } })
      .then(async (res) => ({ status: res.status, body: await res.json().catch(() => null) }))
      .catch(() => ({ status: 0, body: null }));
    page.paragraphs.set(paraId, pending);
    pending.then((got) => {
      if (got.status !== 200) page.paragraphs.delete(paraId); // try again next time
    });
  }
  return page.paragraphs.get(paraId);
}

async function showParagraph(node, c, indexVersion) {
  const status = node.querySelector(".cite-status");
  const quote = node.querySelector(".provision");
  status.textContent = "Fetching the paragraph…";
  const got = await fetchParagraph(c.para_id);
  if (got.status === 404) {
    status.textContent = `This paragraph is not in the build being served${got.body ? ` (${got.body.index_version})` : ""}, and no alias points to it.`;
    return;
  }
  if (got.status !== 200 || !got.body) {
    status.textContent = "The paragraph text could not be fetched. The details above come with the answer itself.";
    return;
  }
  const p = got.body;
  const notes = [];
  if (p.resolved_through_alias) {
    status.classList.add("is-note");
    notes.push(p.note);
  }
  const located = locateQuote(p, c, indexVersion);
  if (located.ok) {
    quote.replaceChildren(located.before, h("mark", { text: located.span }), located.after);
    notes.push("The highlighted text is the span the response cites.");
  } else {
    quote.textContent = p.paragraph.body;
    notes.push(located.why);
  }
  node.dataset.highlight = located.ok ? "located" : "not_located";
  status.textContent = notes.join(" ");
}

function locateQuote(p, c, indexVersion) {
  // quote_start and quote_end are offsets into the paragraph body as the answering build
  // stored it, in characters (code points), so the body is sliced by code point, not by
  // JavaScript's UTF-16 units. A span that cannot be placed exactly is not highlighted.
  if (p.resolved_through_alias) {
    return {
      ok: false,
      why: `The quoted span was located in ${p.requested}, so it is not placed in the paragraph that replaced it.`,
    };
  }
  if (p.index_version !== indexVersion) {
    return {
      ok: false,
      why: `This answer came from build ${indexVersion} and the paragraph from build ${p.index_version}, so the quoted span could not be located.`,
    };
  }
  const chars = Array.from(p.paragraph.body);
  const { quote_start: start, quote_end: end } = c;
  const clean =
    Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end > start && end <= chars.length;
  if (!clean) {
    return { ok: false, why: "The quoted span could not be located in this paragraph, so nothing is highlighted." };
  }
  return {
    ok: true,
    before: chars.slice(0, start).join(""),
    span: chars.slice(start, end).join(""),
    after: chars.slice(end).join(""),
  };
}
