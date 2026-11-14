const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { JSDOM, VirtualConsole } = require("jsdom");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "app.js"), "utf8");
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(body) });
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise(setImmediate); };

function payload() {
  return {
    source: "nessie", customer_id: "cust_test", as_of: "2026-11-14T12:00:00Z",
    accounts: [
      { id: "checking", customer_id: "cust_test", nickname: "Checking", type: "Checking", balance_cents: 120000 },
      { id: "credit", customer_id: "cust_test", nickname: "Card", type: "Credit Card", balance_cents: 25000 },
    ],
    cash_balance_cents: 120000, credit_balance_cents: 25000,
    summary: { source: "nessie", currency: "USD", start: "2026-10-01", end: "2026-11-14", net_spending_cents: 9000, included_count: 3, excluded_count: 1, categories: [{ category: "groceries", amount_cents: 9000 }] },
    gross_spending_cents: 10000, refunds_cents: 1000,
    expense_categories: [{ category: "groceries", amount_cents: 10000 }],
    monthly_spending: [{ month: "2026-10", net_spending_cents: 10000 }, { month: "2026-11", net_spending_cents: -1000 }],
    transactions: [
      { id: "t1", account_id: "checking", merchant: 'Oak, "Research"', description: "Weekly stock", category: "groceries", kind: "purchase", status: "completed", amount_cents: 8000, occurred_at: "2026-11-14T12:00:00Z" },
      { id: "t2", account_id: "credit", merchant: "Cafe", description: "Coffee", category: "groceries", kind: "purchase", status: "completed", amount_cents: 2000, occurred_at: "2026-11-13T12:00:00Z" },
      { id: "t3", account_id: "checking", merchant: "Oak", description: "Return", category: "groceries", kind: "refund", status: "completed", amount_cents: 1000, occurred_at: "2026-11-12T12:00:00Z" },
      { id: "t4", account_id: "credit", merchant: "Cafe", description: "Pending coffee", category: "groceries", kind: "purchase", status: "pending", amount_cents: 3000, occurred_at: "2026-11-11T12:00:00Z" },
    ],
    recurring: [{ merchant: "Cloud", amount_cents: 1200, occurrences: 3, interval_days: 30, confidence: "candidate", transaction_ids: ["a", "b", "c"] }],
  };
}
function boot(t, responder = () => response(payload())) {
  const errors = [];
  const calls = [];
  const blobs = [];
  const downloads = [];
  const console = new VirtualConsole();
  console.on("jsdomError", (error) => errors.push(error));
  const dom = new JSDOM(html, { url: "http://localhost:5173", runScripts: "outside-only", virtualConsole: console });
  const { window } = dom;
  window.scrollTo = () => {};
  window.setTimeout = () => 0;
  window.clearTimeout = () => {};
  window.fetch = async (url, options) => {
    calls.push({ url, options });
    return responder(url, options, calls.length);
  };
  window.Blob = class { constructor(parts, options) { this.parts = parts; this.type = options.type; } };
  window.URL.createObjectURL = (blob) => { blobs.push(blob); return "blob:http://localhost/report"; };
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { downloads.push(this.download); };
  window.eval(source);
  t.after(() => { window.close(); assert.deepEqual(errors, []); });
  const get = (id) => { const element = window.document.getElementById(id); assert.ok(element, id); return element; };
  const click = (id) => get(id).click();
  const change = (id, value) => { get(id).value = value; get(id).dispatchEvent(new window.Event("change", { bubbles: true })); };
  const submit = (id) => get(id).dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  return { window, document: window.document, get, click, change, submit, calls, blobs, downloads };
}

function parseCsv(text) {
  const rows = [];
  let row = [], value = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { value += '"'; i++; }
      else if (char === '"') quoted = false;
      else value += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") { row.push(value); value = ""; }
    else if (char === "\r" || char === "\n") {
      if (char === "\r" && text[i + 1] === "\n") i++;
      row.push(value); rows.push(row); row = []; value = "";
    } else value += char;
  }
  if (value || row.length) { row.push(value); rows.push(row); }
  assert.equal(quoted, false);
  return rows;
}

test("one API payload drives balances, charts, insights, and reports without prototype figures", async (t) => {
  const app = boot(t);
  await tick();
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].url, "/api/v1/dashboard");
  assert.equal(app.get("source-badge").textContent, "Nessie sandbox");
  assert.equal(app.get("balance-value").textContent, "$1,200.00");
  assert.equal(app.get("credit-value").textContent, "$250.00");
  assert.equal(app.get("spend-value").textContent, "$90.00");
  assert.equal(app.get("report-total").textContent, "$90.00");
  assert.match(app.get("refund-note").textContent, /\$100\.00.*\$10\.00.*\$90\.00/);
  assert.match(app.get("monthly-chart").textContent, /-\$10/);
  assert.equal(app.get("transaction-rows").children.length, 4);
  assert.equal(app.get("connection-customer").textContent, "cust_test");
  assert.doesNotMatch(app.document.body.textContent, /\$84,290|September 2024|4\.2 months/);
});

test("protected API requires the app token, keeps it out of storage, and lock clears data", async (t) => {
  const app = boot(t, (url, options) => options.headers.Authorization === "Bearer test-only-token" ? response(payload()) : response({}, 401));
  await tick();
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  app.get("app-token").value = "test-only-token";
  app.submit("access-form");
  await tick();
  assert.equal(app.get("app-token").value, "");
  assert.equal(app.get("dashboard-data").classList.contains("hidden"), false);
  assert.equal(app.window.localStorage.length, 0);
  assert.equal(app.window.sessionStorage.length, 0);
  assert.doesNotMatch(app.document.documentElement.outerHTML, /test-only-token/);
  app.click("lock-button");
  assert.equal(app.get("spend-value").textContent, "");
  assert.equal(app.get("connection-customer").textContent, "");
  assert.equal(app.get("transaction-rows").children.length, 0);
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  app.click("refresh-button");
  await tick();
  assert.equal(app.calls.at(-1).options.headers.Authorization, undefined);
});

test("invalid token stays locked and the unlock control remains usable", async (t) => {
  const app = boot(t, () => response({}, 401));
  await tick();
  app.get("app-token").value = "invalid";
  app.submit("access-form");
  await tick();
  assert.match(app.get("access-error").textContent, /not accepted/);
  assert.equal(app.get("unlock-button").disabled, false);
  assert.equal(app.get("dashboard-data").classList.contains("hidden"), true);
});

test("custom dates go to the API and an empty period clears historical figures", async (t) => {
  const app = boot(t, (url) => {
    const result = payload();
    if (url.includes("2030")) {
      result.summary = { ...result.summary, start: "2030-01-01", end: "2030-01-31", net_spending_cents: 0, included_count: 0, excluded_count: 0, categories: [] };
      Object.assign(result, { gross_spending_cents: 0, refunds_cents: 0, transactions: [], expense_categories: [], monthly_spending: [], recurring: [] });
    }
    return response(result);
  });
  await tick();
  app.change("date-button", "custom");
  app.get("date-start").value = "2030-02-01";
  app.get("date-end").value = "2030-01-31";
  app.submit("custom-date-form");
  assert.equal(app.calls.length, 1);
  assert.match(app.get("date-error").textContent, /start date/);
  app.get("date-start").value = "2030-01-01";
  app.submit("custom-date-form");
  await tick();
  assert.match(app.calls.at(-1).url, /start=2030-01-01&end=2030-01-31/);
  assert.equal(app.get("spend-value").textContent, "$0.00");
  assert.equal(app.get("balance-value").textContent, "$1,200.00");
  assert.match(app.get("recent-activity").textContent, /No transactions/);
  assert.doesNotMatch(app.document.body.textContent, /NaN|Infinity/);
});

test("status and text filters match table rows and filtered CSV exports", async (t) => {
  const app = boot(t);
  await tick();
  app.document.querySelector('[data-filter="pending"]').click();
  assert.equal(app.get("transaction-rows").children.length, 1);
  assert.match(app.get("transaction-rows").textContent, /Pending coffee/);
  app.document.querySelector('[data-filter="all"]').click();
  app.get("transaction-search").value = "Cafe";
  app.get("transaction-search").dispatchEvent(new app.window.Event("input"));
  assert.equal(app.get("transaction-rows").children.length, 2);
  app.click("export-transactions");
  const rows = parseCsv(app.blobs[0].parts.join(""));
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.slice(1).map((row) => row[0]), ["t2", "t4"]);
});

test("pagination does not truncate export and merchant text never becomes HTML", async (t) => {
  const result = payload();
  result.transactions = Array.from({ length: 30 }, (_, index) => ({ ...result.transactions[0], id: "tx" + index, merchant: index === 0 ? "<img src=x onerror=alert(1)>" : "Vendor " + index }));
  const app = boot(t, () => response(result));
  await tick();
  assert.equal(app.get("transaction-rows").children.length, 25);
  assert.equal(app.get("transaction-rows").querySelector("img"), null);
  assert.match(app.get("transaction-rows").textContent, /<img src=x/);
  app.click("next-page");
  assert.equal(app.get("transaction-rows").children.length, 5);
  assert.equal(app.get("next-page").disabled, true);
  app.click("export-transactions");
  assert.equal(parseCsv(app.blobs[0].parts.join("")).length, 31);
});

test("CSV escapes quotes and formula text while preserving signed numeric totals", async (t) => {
  const result = payload();
  result.transactions[1].merchant = "=HYPERLINK(\"example\")";
  result.summary.categories = [{ category: "Returns", amount_cents: -500 }];
  const app = boot(t, () => response(result));
  await tick();
  app.click("export-transactions");
  const rows = parseCsv(app.blobs[0].parts.join(""));
  assert.equal(rows[1][3], 'Oak, "Research"');
  assert.equal(rows[2][3], "'=HYPERLINK(\"example\")");
  app.click("download-report");
  const report = parseCsv(app.blobs[1].parts.join(""));
  assert.deepEqual(report.find((row) => row[0] === "Returns"), ["Returns", "-500", "USD"]);
  assert.deepEqual(report.find((row) => row[0] === "Net spending (cents)"), ["Net spending (cents)", "9000"]);
});

test("a late response cannot replace a newer period or unlock a locked page", async (t) => {
  let resolveFirst;
  const app = boot(t, (url, options, count) => count === 1 ? new Promise((resolve) => { resolveFirst = resolve; }) : response({ ...payload(), summary: { ...payload().summary, net_spending_cents: 4321 } }));
  app.change("date-button", "month");
  await tick();
  assert.equal(app.get("spend-value").textContent, "$43.21");
  resolveFirst(response(payload()));
  await tick();
  assert.equal(app.get("spend-value").textContent, "$43.21");
  app.click("lock-button");
  assert.equal(app.get("spend-value").textContent, "");
});

test("failed refresh hides old data and supports retry without fallback sample numbers", async (t) => {
  const app = boot(t, (url, options, count) => count === 2 ? response({}, 502) : response(payload()));
  await tick();
  app.click("refresh-button");
  await tick();
  assert.equal(app.get("dashboard-data").classList.contains("hidden"), true);
  assert.equal(app.get("spend-value").textContent, "");
  assert.match(app.get("status-banner").textContent, /unavailable/);
  app.click("refresh-button");
  await tick();
  assert.equal(app.get("spend-value").textContent, "$90.00");
});

test("navigation updates accessible state and AI view contains no fabricated answer", async (t) => {
  const app = boot(t, (url) => url === "/api/v1/agent/status" ? response(agentStatus({ enabled: false })) : response(payload()));
  await tick();
  app.document.querySelector('[data-view="insights"]').click();
  assert.equal(app.get("insights-view").classList.contains("hidden"), false);
  assert.equal(app.document.querySelector('[data-view="insights"]').getAttribute("aria-current"), "page");
  app.document.querySelector('[data-view="ask"]').click();
  await tick();
  assert.match(app.get("ask-provider").textContent, /disabled/);
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("ask-answer").textContent, "");
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
});

function agentStatus(overrides = {}) {
  return { enabled: true, provider: "gemini", model: "synthetic-model", read_only: true,
    costar_enabled: false, matterport_enabled: false, max_model_turns: 4, max_tool_calls: 6,
    timeout_seconds: 45, ...overrides };
}
function agentAnswer(overrides = {}) {
  return { status: "answered", answer: "Observed net spending was $90.00; history completeness is unknown.",
    provider: "gemini", model: "synthetic-model", source: "fixture", source_hash: "1".repeat(64),
    model_calls: 2, tool_calls: 1, caveats: ["Only observed records are included."],
    evidence: [{ tool: "summarize_spending", arguments: { start: "2026-11-01", end: "2026-11-13" },
      result: { status: "ok", net_spending_cents: 9000, coverage: "observed_records_only" } }], ...overrides };
}
function typeQuestion(app, question = "Summarize November 1 through 13, 2026 in UTC.") {
  app.get("ask-question").value = question;
  app.get("ask-question").dispatchEvent(new app.window.Event("input", { bubbles: true }));
}
async function openAsk(app) {
  app.document.querySelector('[data-view="ask"]').click();
  await tick();
}
function askRoute(url) {
  if (url.startsWith("/api/v1/dashboard")) return response(payload());
  if (url === "/api/v1/agent/status") return response(agentStatus());
  if (url === "/api/v1/agent/answer") return response(agentAnswer());
  assert.fail("Unexpected request " + url);
}

test("Ask loads status only on navigation, and example buttons never submit", async (t) => {
  let housing = false;
  const app = boot(t, (url) => url === "/api/v1/agent/status" ? response(agentStatus({ costar_enabled: housing })) : askRoute(url));
  await tick();
  assert.equal(app.calls.length, 1);
  await openAsk(app);
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[1].url, "/api/v1/agent/status");
  assert.equal(app.get("ask-example-housing").classList.contains("hidden"), true);
  app.click("ask-example-spending");
  assert.match(app.get("ask-question").value, /2026-10-01 through 2026-11-14/);
  assert.equal(app.calls.length, 2);
  await openAsk(app);
  assert.equal(app.calls.length, 2);
  housing = true;
  app.click("ask-check");
  await tick();
  assert.equal(app.get("ask-example-housing").classList.contains("hidden"), false);
  app.click("ask-example-housing");
  assert.match(app.get("ask-question").value, /one-bedroom apartments in TX/);
  assert.equal(app.calls.length, 3);
  assert.equal(app.calls.some((call) => call.options.method === "POST"), false);
});

test("Ask authenticates with the memory token and sends only the explicit question", async (t) => {
  const app = boot(t, (url, options) => options.headers.Authorization === "Bearer ask-only-token" ? askRoute(url) : response({}, 401));
  await tick();
  app.get("app-token").value = "ask-only-token";
  app.submit("access-form");
  await tick();
  app.change("date-button", "month");
  await tick();
  await openAsk(app);
  typeQuestion(app, "  Show dining for 2026-10-01 through 2026-10-14.  ");
  app.submit("ask-form");
  await tick();
  const sent = app.calls.at(-1);
  assert.equal(sent.url, "/api/v1/agent/answer");
  assert.equal(sent.options.method, "POST");
  assert.equal(sent.options.headers.Authorization, "Bearer ask-only-token");
  assert.equal(sent.options.headers["Content-Type"], "application/json");
  assert.equal(sent.options.cache, "no-store");
  assert.deepEqual(JSON.parse(sent.options.body), { question: "Show dining for 2026-10-01 through 2026-10-14." });
  assert.equal(app.get("ask-asked-question").textContent, "Show dining for 2026-10-01 through 2026-10-14.");
  assert.equal(app.window.localStorage.length, 0);
  assert.equal(app.window.sessionStorage.length, 0);
  assert.doesNotMatch(app.document.documentElement.outerHTML, /ask-only-token/);
  assert.match(app.get("ask-guidance").textContent, /not applied automatically/);
});

test("Ask guards question length and duplicate submits while a request is pending", async (t) => {
  let finish;
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? new Promise((resolve) => { finish = resolve; }) : askRoute(url));
  await tick(); await openAsk(app);
  for (const invalid of ["   ", "x".repeat(2001)]) {
    typeQuestion(app, invalid); app.submit("ask-form");
    assert.equal(app.calls.length, 2);
    assert.match(app.get("ask-error").textContent, /1 to 2000/);
  }
  typeQuestion(app); app.submit("ask-form");
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("ask-question").disabled, true);
  assert.equal(app.get("ask-form").getAttribute("aria-busy"), "true");
  assert.match(app.get("ask-state").textContent, /Reading evidence/);
  app.submit("ask-form");
  assert.equal(app.calls.length, 3);
  finish(response(agentAnswer())); await tick();
  assert.equal(app.get("ask-form").getAttribute("aria-busy"), "false");
  assert.equal(app.get("ask-question").disabled, false);
});

for (const status of ["answered", "limited", "unavailable"]) {
  test("Ask renders " + status + " with separate evidence and caveats", async (t) => {
    const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(agentAnswer({ status })) : askRoute(url));
    await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
    assert.equal(app.get("ask-result").classList.contains("hidden"), false);
    assert.equal(app.get("ask-answer").textContent, agentAnswer().answer);
    assert.match(app.get("ask-evidence").textContent, /Observed net spending: \$90\.00/);
    assert.match(app.get("ask-caveats").textContent, /observed records/);
    assert.equal(app.get("ask-evidence").querySelectorAll("details").length, 1);
    assert.match(app.get("ask-evidence").querySelector("pre").textContent, /2026-11-01/);
    assert.equal(app.get("ask-outcome").textContent, { answered: "Answered", limited: "Request limit reached", unavailable: "Answer unavailable" }[status]);
  });
}

test("Ask rejects a claimed answer with no successful tool evidence", async (t) => {
  let next = agentAnswer({ evidence: [] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(next) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app);
  for (const evidence of [[], [{ tool: "inspect_floor_plan", arguments: {}, result: { status: "unavailable" } }]]) {
    next = agentAnswer({ evidence, answer: "UNSUPPORTED CLAIM" });
    app.submit("ask-form"); await tick();
    assert.equal(app.get("ask-result").classList.contains("hidden"), true);
    assert.equal(app.get("ask-answer").textContent, "");
    assert.match(app.get("ask-error").textContent, /valid answer could not/);
    assert.doesNotMatch(app.document.body.textContent, /UNSUPPORTED CLAIM/);
  }
});

test("Ask HTTP and transport failures are sanitized without retries", async (t) => {
  let failure = 429;
  let bodyReads = 0;
  const app = boot(t, (url) => {
    if (url !== "/api/v1/agent/answer") return askRoute(url);
    if (failure === "network") throw new Error("PRIVATE_PROVIDER_DIAGNOSTIC");
    return { ok: false, status: failure, json: async () => { bodyReads++; return { detail: "PRIVATE_PROVIDER_DIAGNOSTIC" }; } };
  });
  await tick(); await openAsk(app); typeQuestion(app);
  for (failure of [429, 503, 504, 500, "network"]) {
    const before = app.calls.length;
    app.submit("ask-form"); await tick();
    assert.equal(app.calls.length, before + 1);
    assert.equal(app.get("ask-result").classList.contains("hidden"), true);
    assert.ok(app.get("ask-error").textContent);
    assert.doesNotMatch(app.document.body.textContent, /PRIVATE_PROVIDER_DIAGNOSTIC/);
    assert.equal(app.get("ask-submit").disabled, false);
  }
  assert.equal(bodyReads, 0);
});

test("Ask unauthorized response locks and clears question, answer, and evidence", async (t) => {
  let expired = false;
  const app = boot(t, (url) => url === "/api/v1/agent/answer" && expired ? response({}, 401) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  assert.ok(app.get("ask-answer").textContent);
  expired = true;
  app.submit("ask-form"); await tick();
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  assert.equal(app.get("ask-question").value, "");
  assert.equal(app.get("ask-answer").textContent, "");
  assert.equal(app.get("ask-evidence").children.length, 0);
  assert.equal(app.get("ask-caveats").children.length, 0);
  assert.match(app.get("access-error").textContent, /not accepted/);
});

test("dashboard refresh aborts Ask and prevents a stale response from restoring evidence", async (t) => {
  let finish;
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? new Promise((resolve) => { finish = resolve; }) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app, "OLD PRIVATE QUESTION"); app.submit("ask-form");
  const pending = app.calls.at(-1);
  app.click("refresh-button"); await tick();
  assert.equal(pending.options.signal.aborted, true);
  assert.equal(app.get("ask-question").value, "");
  assert.equal(app.get("ask-evidence").children.length, 0);
  finish(response(agentAnswer({ answer: "STALE ANSWER" }))); await tick();
  assert.equal(app.get("ask-answer").textContent, "");
  assert.doesNotMatch(app.document.body.textContent, /STALE ANSWER|OLD PRIVATE QUESTION/);
  assert.equal(app.get("ask-submit").disabled, true);
});

test("locking during answer JSON parsing ignores the late body", async (t) => {
  let finishJson;
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? { ok: true, status: 200, json: () => new Promise((resolve) => { finishJson = resolve; }) } : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  app.click("lock-button");
  finishJson(agentAnswer({ answer: "LATE BODY" })); await tick();
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
  assert.equal(app.get("ask-answer").textContent, "");
});

test("old status cannot return after locking and changing the token", async (t) => {
  let finishStatus;
  let statusCount = 0;
  const app = boot(t, (url) => {
    if (url === "/api/v1/agent/status" && ++statusCount === 1) return new Promise((resolve) => { finishStatus = resolve; });
    return askRoute(url);
  });
  await tick(); await openAsk(app);
  const pending = app.calls.at(-1);
  app.click("lock-button");
  app.get("app-token").value = "new-memory-token";
  app.submit("access-form"); await tick(); await openAsk(app);
  finishStatus(response(agentStatus({ model: "STALE MODEL", costar_enabled: true }))); await tick();
  assert.equal(pending.options.signal.aborted, true);
  assert.doesNotMatch(app.get("ask-provider").textContent, /STALE MODEL/);
  assert.equal(app.get("ask-example-housing").classList.contains("hidden"), true);
  assert.equal(app.calls.at(-1).options.headers.Authorization, "Bearer new-memory-token");
});

for (const phase of ["status", "answer"]) {
  test("Ask " + phase + " timeout releases controls and ignores the eventual response", async (t) => {
    const timers = [];
    let finish;
    const app = boot(t, (url) => {
      if (url === "/api/v1/agent/" + phase) return new Promise((resolve) => { finish = resolve; });
      return askRoute(url);
    });
    await tick();
    app.window.setTimeout = (fn, milliseconds) => { timers.push({ fn, milliseconds }); return timers.length; };
    await openAsk(app);
    if (phase === "answer") { typeQuestion(app); app.submit("ask-form"); }
    const count = app.calls.length;
    const pending = app.calls.at(-1);
    timers.find((timer) => timer.milliseconds === (phase === "status" ? 15000 : 130000)).fn();
    assert.equal(pending.options.signal.aborted, true);
    assert.match(app.get("ask-error").textContent, /timed out/);
    assert.equal(app.get("ask-check").disabled, false);
    assert.equal(app.get("ask-form").getAttribute("aria-busy"), "false");
    assert.equal(app.calls.length, count);
    finish(response(phase === "status" ? agentStatus() : agentAnswer())); await tick();
    assert.match(app.get("ask-error").textContent, /timed out/);
    assert.equal(app.get("ask-answer").textContent, "");
  });
}

test("housing and scan evidence preserve full rent ranges, units, unknowns, and text safety", async (t) => {
  const malicious = '<img src=x onerror="alert(1)"> [external](https://unrelated.invalid)';
  const body = agentAnswer({ answer: malicious, model_calls: 3, tool_calls: 2, evidence: [
    { tool: "costar_search", arguments: {}, result: { status: "ok", results: [
      { property_name: malicious, floor_plan_name: "A1", asking_rent: { minimum: 1200, maximum: 1900, currency: "USD", period: "month", basis: "per_unit" }, price_range_within_budget: false },
      { property_name: "Upper unknown", floor_plan_name: "B1", asking_rent: { minimum: 1400, maximum: null, currency: "USD", period: "month", basis: "per_unit" }, price_range_within_budget: false },
    ], limitations: ["Recorded availability, not a live quote."] } },
    { tool: "inspect_floor_plan", arguments: { property_id: 20, floor_plan_id: 30, criteria: {} }, result: {
      status: "ok", model_id: "synthetic-model", rooms_truncated: true, rooms_returned: 1, room_count: 2,
      geometry_clearance_verified: false, rooms: [{ room_id: "study", label: "Study",
        dimension_estimates: { area_square_meters: 12, width_meters: 3, depth_meters: 4 },
        legacy_dimensions: { area_floor: 100, area_unit: "square_feet", width: null, depth: null, length_unit: "feet" },
      }], limitations: ["No furniture fit was verified."] } },
  ] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  const evidence = app.get("ask-evidence");
  assert.equal(app.get("ask-answer").textContent, malicious);
  assert.match(evidence.textContent, /\$1,200\.00 – \$1,900\.00 per month, per unit/);
  assert.match(evidence.textContent, /\$1,400\.00 – upper bound unknown/);
  assert.match(evidence.textContent, /full range is not confirmed/);
  assert.match(evidence.textContent, /12 square meters; width: 3 meters; depth: 4 meters/);
  assert.match(evidence.textContent, /100 square feet; width: unknown; depth: unknown/);
  assert.match(evidence.textContent, /Only 1 of 2 rooms/);
  assert.equal(app.get("ask-result").querySelector("img, a, script, iframe"), null);
  assert.equal(evidence.querySelectorAll("details").length, 2);
});
