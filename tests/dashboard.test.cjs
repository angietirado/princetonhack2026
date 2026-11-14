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
  let inspection = false;
  const app = boot(t, (url) => url === "/api/v1/agent/status" ? response(agentStatus({ costar_enabled: housing, matterport_enabled: inspection })) : askRoute(url));
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
  assert.doesNotMatch(app.get("ask-question").value, /verified floor-plan model references/);
  assert.equal(app.calls.length, 3);
  inspection = true;
  app.click("ask-check"); await tick(); app.click("ask-example-housing");
  assert.match(app.get("ask-question").value, /only plans with verified floor-plan model references/);
  assert.match(app.get("ask-question").value, /Do not inspect yet; I will choose a floor plan/);
  assert.equal(app.calls.length, 4);
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
    assert.match(app.get("ask-state").textContent, { answered: /^Answer ready\./, limited: /^Request limit reached\./, unavailable: /^Answer unavailable\./ }[status]);
    assert.equal(app.get("ask-state").getAttribute("role"), "status");
    assert.equal(app.get("ask-state").getAttribute("aria-live"), "polite");
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

function selectionCriteria(overrides = {}) {
  return { state: "TX", city: null, bedrooms: 1, max_monthly_rent_usd: 1800,
    min_square_feet: 0, limit: 2, require_floor_plan_model: true,
    include_specialized_housing: false, ...overrides };
}
function shortlistAnswer() {
  const row = { property_id: 20, floor_plan_id: 30, property_name: "Synthetic Place",
    floor_plan_name: "Study plan", asking_rent: { minimum: 1200, maximum: 1700, currency: "USD", period: "month", basis: "per_unit" },
    price_range_within_budget: true, matterport: { scope: "floor_plan", model_ids: ["m-one", "m-two"],
      model_reference_count: 2, same_property_catalog_checked: true, shared_across_properties: false,
      shared_across_floor_plans: false } };
  return agentAnswer({ answer: "Two recorded floor plans; inspect one to see available measurements.",
    evidence: [{ tool: "costar_search", arguments: { limit: 2 }, result: {
      status: "ok", source_kind: "costar_local_snapshot", source_hash: "a".repeat(64),
      criteria: selectionCriteria(), results: [row, { ...structuredClone(row), property_id: 21,
        floor_plan_id: 31, floor_plan_name: "Second plan" }], limitations: ["Recorded availability only."] } }] });
}
function inspectionResponse(overrides = {}) {
  return { status: "ok", source_kind: "matterport_model_api", property_id: 20, floor_plan_id: 30,
    model_id: "m-one", catalog_source_hash: "a".repeat(64), models_requested: 1, models_inspected: 1,
    verified_reference_count: 2, all_referenced_models_inspected: false,
    geometry_clearance_verified: false, current_unit_availability_verified: false,
    rooms_returned: 1, room_count: 2, rooms_truncated: true, rooms: [{ room_id: "r", label: "Study",
      dimension_estimates: null, legacy_dimensions: { area_floor: 100, area_unit: "square_feet",
        width: null, depth: null, length_unit: "feet" } }],
    limitations: ["No furniture fit or live unit availability was verified."], ...overrides };
}
async function showShortlist(app) {
  await tick(); await openAsk(app); typeQuestion(app, "Find an apartment in TX under $1800.");
  app.submit("ask-form"); await tick();
}
function inspectionRoute(url) {
  if (url === "/api/v1/agent/status") return response(agentStatus({ costar_enabled: true, matterport_enabled: true }));
  if (url === "/api/v1/agent/answer") return response(shortlistAnswer());
  if (url === "/api/v1/costar/inspect") return response(inspectionResponse());
  return askRoute(url);
}

test("direct floor-plan inspection sends original canonical selection without another AI call", async (t) => {
  const app = boot(t, (url, options) => options.headers.Authorization === "Bearer selection-token" ? inspectionRoute(url) : response({}, 401));
  await tick();
  app.get("app-token").value = "selection-token"; app.submit("access-form"); await tick();
  await showShortlist(app);
  const aiText = app.get("ask-answer").textContent;
  const aiUsage = app.get("ask-usage").textContent;
  typeQuestion(app, "Edited draft asking about California under a different budget.");
  const buttons = app.document.querySelectorAll(".inspect-floor-plan");
  assert.equal(buttons.length, 2);
  buttons[0].click(); await tick();
  const request = app.calls.at(-1);
  assert.equal(request.url, "/api/v1/costar/inspect");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.headers.Authorization, "Bearer selection-token");
  assert.equal(request.options.headers["Content-Type"], "application/json");
  assert.equal(request.options.redirect, "error");
  assert.deepEqual(JSON.parse(request.options.body), { criteria: selectionCriteria(), property_id: 20, floor_plan_id: 30 });
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/agent/answer").length, 1);
  assert.equal(app.get("ask-answer").textContent, aiText);
  assert.equal(app.get("ask-usage").textContent, aiUsage);
  assert.match(app.get("inspection-title").textContent, /Direct Matterport evidence/);
  assert.match(app.get("inspection-selection").textContent, /Property 20 \/ Floor plan 30/);
  assert.match(app.get("inspection-evidence").textContent, /Dimension estimates unavailable/);
  assert.match(app.get("inspection-evidence").textContent, /100 square feet; width: unknown; depth: unknown/);
  assert.match(app.get("inspection-evidence").textContent, /Only 1 of 2 rooms/);
  assert.match(app.get("inspection-evidence").textContent, /1 of 2 referenced models/);
  assert.match(app.get("inspection-evidence").textContent, /Furniture fit and clear routes were not verified/);
  const exact = JSON.parse(app.get("inspection-evidence").querySelector("pre").textContent);
  assert.deepEqual(exact.request, JSON.parse(request.options.body));
  assert.deepEqual(exact.response, inspectionResponse());
  assert.equal(Object.hasOwn(exact, "tool"), false);
  assert.equal(app.window.localStorage.length, 0);
  assert.doesNotMatch(app.document.documentElement.outerHTML, /selection-token/);
});

test("inspection action requires enabled service and complete unambiguous server selection", async (t) => {
  let body = shortlistAnswer();
  let enabled = true;
  const app = boot(t, (url) => {
    if (url === "/api/v1/agent/status") return response(agentStatus({ costar_enabled: true, matterport_enabled: enabled }));
    if (url === "/api/v1/agent/answer") return response(body);
    return inspectionRoute(url);
  });
  await tick(); await openAsk(app); typeQuestion(app);
  const corruptions = [
    (result) => { delete result.criteria; },
    (result) => { result.criteria = {}; },
    (result) => { result.criteria.model_id = "other-model"; },
    (result) => { delete result.source_hash; },
    (result) => { result.source_hash = "not-a-hash"; },
    (result) => { result.source_kind = "model_invented"; },
    (result) => { result.results[0].property_id = true; },
    (result) => { result.results[0].floor_plan_id = 0; },
    (result) => { result.results[0].matterport.scope = "community_only"; },
    (result) => { result.results[0].matterport.shared_across_floor_plans = true; },
    (result) => { result.results[0].matterport.shared_across_properties = true; },
    (result) => { delete result.results[0].matterport.same_property_catalog_checked; },
    (result) => { result.results[0].matterport.model_ids = []; },
  ];
  for (const corrupt of corruptions) {
    body = shortlistAnswer(); body.evidence[0].result.results.length = 1;
    corrupt(body.evidence[0].result);
    app.submit("ask-form"); await tick();
    assert.equal(app.document.querySelectorAll(".inspect-floor-plan").length, 0);
  }
  body = shortlistAnswer(); enabled = false;
  app.click("ask-check"); await tick(); app.submit("ask-form"); await tick();
  assert.equal(app.document.querySelectorAll(".inspect-floor-plan").length, 0);
  assert.equal(app.calls.some((call) => call.url === "/api/v1/costar/inspect"), false);
});

test("applied catalog filters display returned values even when they differ from the question", async (t) => {
  const body = shortlistAnswer();
  body.answer = "Here are apartments under your $1000 budget.";
  body.evidence[0].arguments = { max_monthly_rent_usd: 1000 };
  body.evidence[0].result.criteria = selectionCriteria({ city: "Austin", min_square_feet: 650 });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : inspectionRoute(url));
  await tick(); await openAsk(app); typeQuestion(app, "Find apartments under $1000.");
  app.submit("ask-form"); await tick();
  assert.match(app.get("ask-asked-question").textContent, /under \$1000/);
  const summary = app.get("ask-evidence").querySelector(".ask-applied-criteria");
  assert.match(summary.textContent, /TX, Austin; 1 bedroom/);
  assert.match(summary.textContent, /advertised starting rent at most \$1,800 per month, per unit/);
  assert.match(summary.textContent, /at least 650 square feet; up to 2 results/);
  assert.match(summary.textContent, /verified floor-plan model references required; specialized housing excluded/);
  assert.match(summary.textContent, /Compare these applied filters with your question/);
  assert.doesNotMatch(summary.textContent, /\$1,?000/);
});

test("availability recheck hides existing inspection actions when Matterport becomes disabled", async (t) => {
  let enabled = true;
  const app = boot(t, (url) => url === "/api/v1/agent/status" ? response(agentStatus({ costar_enabled: true, matterport_enabled: enabled })) : inspectionRoute(url));
  await showShortlist(app);
  const button = app.document.querySelector(".inspect-floor-plan");
  assert.equal(button.classList.contains("hidden"), false);
  enabled = false; app.click("ask-check"); await tick();
  assert.equal(button.classList.contains("hidden"), true);
  assert.equal(button.disabled, true);
  button.dispatchEvent(new app.window.MouseEvent("click", { bubbles: true })); await tick();
  assert.equal(app.calls.some((call) => call.url === "/api/v1/costar/inspect"), false);
});

test("an inspection blocks concurrent selections and questions, then cancel ignores a late reply", async (t) => {
  let finish;
  const app = boot(t, (url) => url === "/api/v1/costar/inspect" ? new Promise((resolve) => { finish = resolve; }) : inspectionRoute(url));
  await showShortlist(app);
  const buttons = app.document.querySelectorAll(".inspect-floor-plan");
  buttons[0].click();
  const pending = app.calls.at(-1);
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("ask-check").disabled, true);
  assert.equal(buttons[1].disabled, true);
  assert.equal(app.get("inspection-result").getAttribute("aria-busy"), "true");
  buttons[1].dispatchEvent(new app.window.Event("click"));
  app.submit("ask-form");
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/costar/inspect").length, 1);
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/agent/answer").length, 1);
  app.click("inspection-cancel");
  assert.equal(pending.options.signal.aborted, true);
  assert.equal(app.get("ask-submit").disabled, false);
  assert.equal(buttons[1].disabled, false);
  finish(response(inspectionResponse())); await tick();
  assert.equal(app.get("inspection-evidence").children.length, 0);
  assert.match(app.get("inspection-state").textContent, /Stopped waiting/);
});

test("inspection rejects changed catalog revisions and mismatched identities without rendering measurements", async (t) => {
  let result = inspectionResponse();
  const app = boot(t, (url) => url === "/api/v1/costar/inspect" ? response(result) : inspectionRoute(url));
  await showShortlist(app);
  for (const override of [{ catalog_source_hash: "b".repeat(64) }, { property_id: 999 },
    { floor_plan_id: 999 }, { model_id: "unrelated-model" }, { source_kind: "other-source" },
    { geometry_clearance_verified: true }]) {
    result = inspectionResponse(override);
    app.document.querySelector(".inspect-floor-plan").click(); await tick();
    assert.equal(app.get("inspection-evidence").children.length, 0);
    assert.equal(app.get("inspection-source").textContent, "");
    assert.ok(app.get("inspection-error").textContent);
    if (override.catalog_source_hash) assert.match(app.get("inspection-error").textContent, /catalog changed.*fresh apartment search/);
    assert.doesNotMatch(app.get("inspection-result").textContent, /100 square feet/);
  }
});

test("inspection errors are sanitized and do not trigger model calls or automatic retries", async (t) => {
  let failure = 422;
  let bodyReads = 0;
  const app = boot(t, (url) => {
    if (url !== "/api/v1/costar/inspect") return inspectionRoute(url);
    if (failure === "network") throw new Error("PRIVATE_API_TOKEN");
    return { ok: false, status: failure, json: async () => { bodyReads++; return { detail: "PRIVATE_API_TOKEN" }; } };
  });
  await showShortlist(app);
  for (failure of [403, 422, 429, 503, 504, 500, "network"]) {
    const count = app.calls.length;
    app.document.querySelector(".inspect-floor-plan").click(); await tick();
    assert.equal(app.calls.length, count + 1);
    assert.ok(app.get("inspection-error").textContent);
    assert.doesNotMatch(app.document.body.textContent, /PRIVATE_API_TOKEN/);
    assert.equal(app.get("inspection-evidence").children.length, 0);
  }
  assert.equal(bodyReads, 0);
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/agent/answer").length, 1);
});

test("inspection auth failure locks and clears both AI and direct evidence", async (t) => {
  const app = boot(t, (url) => url === "/api/v1/costar/inspect" ? response({}, 401) : inspectionRoute(url));
  await showShortlist(app);
  app.document.querySelector(".inspect-floor-plan").click(); await tick();
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  for (const id of ["ask-answer", "inspection-selection", "inspection-source"]) assert.equal(app.get(id).textContent, "");
  assert.equal(app.get("ask-evidence").children.length, 0);
  assert.equal(app.get("inspection-evidence").children.length, 0);
  assert.equal(app.get("inspection-result").classList.contains("hidden"), true);
});

for (const reset of ["refresh", "lock", "token"]) {
  test("late inspection JSON cannot restore selection after " + reset, async (t) => {
    let finishJson;
    const app = boot(t, (url) => url === "/api/v1/costar/inspect" ? { ok: true, status: 200, json: () => new Promise((resolve) => { finishJson = resolve; }) } : inspectionRoute(url));
    await showShortlist(app);
    app.document.querySelector(".inspect-floor-plan").click(); await tick();
    const pending = app.calls.at(-1);
    if (reset === "refresh") app.click("refresh-button");
    else if (reset === "lock") app.click("lock-button");
    else { app.get("app-token").value = "replacement-token"; app.submit("access-form"); }
    await tick();
    finishJson(inspectionResponse()); await tick();
    assert.equal(pending.options.signal.aborted, true);
    assert.equal(app.get("inspection-result").classList.contains("hidden"), true);
    assert.equal(app.get("inspection-evidence").children.length, 0);
    assert.equal(app.get("inspection-selection").textContent, "");
    assert.equal(app.get("ask-question").value, "");
  });
}

test("old shortlist cards cannot inspect after a new question replaces their context", async (t) => {
  const app = boot(t, inspectionRoute);
  await showShortlist(app);
  const oldButton = app.document.querySelector(".inspect-floor-plan");
  oldButton.click(); await tick();
  assert.equal(app.get("inspection-evidence").children.length, 1);
  typeQuestion(app, "Find a different apartment."); app.submit("ask-form"); await tick();
  assert.equal(app.get("inspection-result").classList.contains("hidden"), true);
  assert.equal(app.get("inspection-evidence").children.length, 0);
  const before = app.calls.length;
  oldButton.disabled = false;
  oldButton.dispatchEvent(new app.window.Event("click"));
  assert.equal(app.calls.length, before);
});

test("inspection deadline releases controls, rejects its late response, and performs no retry", async (t) => {
  let finish;
  const timers = [];
  const app = boot(t, (url) => url === "/api/v1/costar/inspect" ? new Promise((resolve) => { finish = resolve; }) : inspectionRoute(url));
  await showShortlist(app);
  app.window.setTimeout = (fn, milliseconds) => { timers.push({ fn, milliseconds }); return timers.length; };
  app.document.querySelector(".inspect-floor-plan").click();
  const pending = app.calls.at(-1);
  const count = app.calls.length;
  timers.find((timer) => timer.milliseconds === 35000).fn();
  assert.equal(pending.options.signal.aborted, true);
  assert.match(app.get("inspection-error").textContent, /timed out/);
  assert.equal(app.get("ask-submit").disabled, false);
  assert.equal(app.get("inspection-cancel").classList.contains("hidden"), true);
  assert.equal(app.calls.length, count);
  finish(response(inspectionResponse())); await tick();
  assert.equal(app.get("inspection-evidence").children.length, 0);
  assert.match(app.get("inspection-error").textContent, /timed out/);
});

function directCriteria(overrides = {}) {
  return selectionCriteria({ limit: 3, ...overrides });
}
function catalogResponse(criteria = directCriteria()) {
  const result = structuredClone(shortlistAnswer().evidence[0].result);
  result.criteria = criteria;
  for (const row of result.results) {
    row.asking_rent.minimum = Math.min(row.asking_rent.minimum, criteria.max_monthly_rent_usd);
    row.asking_rent.maximum = Math.min(row.asking_rent.maximum, criteria.max_monthly_rent_usd);
  }
  return result;
}
function catalogRoute(url, options) {
  if (url === "/api/v1/agent/status") return response(agentStatus({ enabled: false, costar_enabled: true, matterport_enabled: true }));
  if (url === "/api/v1/costar/search") return response(catalogResponse(JSON.parse(options.body)));
  return inspectionRoute(url);
}
async function showCatalog(app) {
  await tick(); await openAsk(app); app.submit("catalog-form"); await tick();
}

test("direct catalog search works with AI disabled and sends the exact explicit filters with memory auth", async (t) => {
  const app = boot(t, (url, options) => options.headers.Authorization === "Bearer catalog-only-token" ? catalogRoute(url, options) : response({}, 401));
  await tick(); app.get("app-token").value = "catalog-only-token"; app.submit("access-form"); await tick();
  const before = app.calls.length; await openAsk(app);
  assert.equal(app.calls.length, before + 1);
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("catalog-submit").disabled, false);
  assert.equal(app.get("catalog-model").checked, true);
  app.change("catalog-state", "CA"); app.change("catalog-city", "  Sacramento  ");
  app.change("catalog-bedrooms", "0"); app.change("catalog-rent", "1000"); app.change("catalog-area", "650");
  app.get("catalog-model").checked = false;
  assert.equal(app.calls.length, before + 1, "editing filters never searches");
  app.submit("catalog-form"); await tick();
  const request = app.calls.at(-1);
  const expected = directCriteria({ state: "CA", city: "Sacramento", bedrooms: 0, max_monthly_rent_usd: 1000, min_square_feet: 650, require_floor_plan_model: false });
  assert.equal(request.url, "/api/v1/costar/search");
  assert.equal(request.options.method, "POST");
  assert.equal(request.options.redirect, "error");
  assert.equal(request.options.cache, "no-store");
  assert.equal(request.options.headers.Authorization, "Bearer catalog-only-token");
  assert.deepEqual(JSON.parse(request.options.body), expected);
  assert.equal(app.get("catalog-result").classList.contains("hidden"), false);
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
  assert.equal(app.get("ask-answer").textContent, "");
  assert.match(app.get("catalog-evidence").querySelector(".ask-applied-criteria").textContent, /CA, Sacramento; studio; advertised starting rent at most \$1,000/);
  const exact = JSON.parse(app.get("catalog-evidence").querySelector("pre").textContent);
  assert.deepEqual(exact.request, expected);
  assert.equal(Object.hasOwn(exact, "tool"), false);
  assert.equal(app.calls.some((call) => call.url === "/api/v1/agent/answer"), false);
  assert.equal(app.window.localStorage.length, 0);
  assert.equal(app.window.sessionStorage.length, 0);
  assert.doesNotMatch(app.document.documentElement.outerHTML, /catalog-only-token/);
});

test("direct catalog default model filter follows inspection availability while explicit edits survive status checks", async (t) => {
  let matterport = false;
  let catalog = true;
  const app = boot(t, (url, options) => url === "/api/v1/agent/status" ? response(agentStatus({ enabled: false, costar_enabled: catalog, matterport_enabled: matterport })) : catalogRoute(url, options));
  await tick(); await openAsk(app);
  assert.equal(app.get("catalog-model").checked, false);
  assert.equal(app.get("catalog-submit").disabled, false);
  app.get("catalog-model").checked = true;
  app.click("ask-check"); await tick();
  assert.equal(app.get("catalog-model").checked, true);
  catalog = false; app.click("ask-check"); await tick();
  assert.equal(app.get("catalog-submit").disabled, true);
  app.submit("catalog-form"); await tick();
  assert.equal(app.calls.some((call) => call.url === "/api/v1/costar/search"), false);
});

test("direct catalog rejects invalid field values before any search", async (t) => {
  const app = boot(t, catalogRoute);
  await tick(); await openAsk(app);
  for (const [id, value] of [["catalog-state", "NY"], ["catalog-city", "x".repeat(81)], ["catalog-city", "A\u0001B"],
    ["catalog-bedrooms", "7"], ["catalog-rent", ""], ["catalog-rent", "0"], ["catalog-rent", "100001"],
    ["catalog-rent", "1000.5"], ["catalog-rent", "1e3"], ["catalog-area", "-1"], ["catalog-area", "10001"], ["catalog-area", "0.5"]]) {
    app.get("catalog-form").reset(); app.change(id, value); app.submit("catalog-form"); await tick();
    assert.match(app.get("catalog-error").textContent, /Use TX or CA/, id + "=" + value);
    assert.equal(app.calls.some((call) => call.url === "/api/v1/costar/search"), false);
  }
  app.get("catalog-form").reset(); app.change("catalog-rent", "100000"); app.change("catalog-area", "10000"); app.change("catalog-bedrooms", "6");
  app.submit("catalog-form"); await tick();
  assert.deepEqual(JSON.parse(app.calls.at(-1).options.body), directCriteria({ max_monthly_rent_usd: 100000, min_square_feet: 10000, bedrooms: 6, require_floor_plan_model: false }));
});

test("direct catalog rejects every changed canonical filter and unexpected fields", async (t) => {
  let changed;
  const app = boot(t, (url, options) => {
    if (url !== "/api/v1/costar/search") return catalogRoute(url, options);
    const result = catalogResponse(JSON.parse(options.body)); changed(result); return response(result);
  });
  await tick(); await openAsk(app); app.change("catalog-rent", "1000");
  const mutations = [
    (value) => { value.criteria.state = "CA"; }, (value) => { value.criteria.city = "Austin"; },
    (value) => { value.criteria.bedrooms = 2; }, (value) => { value.criteria.max_monthly_rent_usd = 1800; },
    (value) => { value.criteria.min_square_feet = 1; }, (value) => { value.criteria.limit = 4; },
    (value) => { value.criteria.require_floor_plan_model = false; }, (value) => { value.criteria.include_specialized_housing = true; },
    (value) => { value.criteria.source_path = "untrusted"; }, (value) => { delete value.criteria.city; },
  ];
  for (changed of mutations) {
    app.submit("catalog-form"); await tick();
    assert.match(app.get("catalog-error").textContent, /returned filters did not match/);
    assert.equal(app.get("catalog-result").classList.contains("hidden"), true);
    assert.equal(app.get("catalog-evidence").textContent, "");
    assert.equal(app.document.querySelector(".inspect-floor-plan"), null);
  }
});

test("direct catalog keeps full and unknown rent ranges explicit and allows selected inspection without AI", async (t) => {
  const app = boot(t, (url, options) => {
    if (url !== "/api/v1/costar/search") return catalogRoute(url, options);
    const result = catalogResponse(JSON.parse(options.body));
    result.results[0].asking_rent = { minimum: 900, maximum: 1300, currency: "USD", period: "month", basis: "per_unit" };
    result.results[0].price_range_within_budget = false;
    result.results[1].asking_rent = { minimum: 950, maximum: null, currency: "USD", period: "month", basis: "per_unit" };
    result.results[1].price_range_within_budget = false;
    return response(result);
  });
  await tick(); await openAsk(app); app.change("catalog-rent", "1000"); app.submit("catalog-form"); await tick();
  const evidence = app.get("catalog-evidence");
  assert.match(evidence.textContent, /\$900\.00 – \$1,300\.00 per month, per unit/);
  assert.match(evidence.textContent, /\$950\.00 – upper bound unknown/);
  assert.equal(Array.from(evidence.querySelectorAll("p")).filter((item) => item.textContent === "The full range is not confirmed within the applied catalog budget.").length, 2);
  app.change("catalog-rent", "2000");
  evidence.querySelector(".inspect-floor-plan").click(); await tick();
  assert.equal(app.calls.at(-1).url, "/api/v1/costar/inspect");
  assert.deepEqual(JSON.parse(app.calls.at(-1).options.body), { criteria: directCriteria({ max_monthly_rent_usd: 1000 }), property_id: 20, floor_plan_id: 30 });
  assert.match(app.get("inspection-evidence").textContent, /100 square feet/);
  assert.equal(app.calls.some((call) => call.url === "/api/v1/agent/answer"), false);
});

test("direct catalog request blocks other reads and cancellation rejects an eventual response", async (t) => {
  let finish;
  const app = boot(t, (url, options) => {
    if (url === "/api/v1/costar/search") return new Promise((resolve) => { finish = resolve; });
    return inspectionRoute(url, options);
  });
  await showShortlist(app);
  const oldButton = app.document.querySelector(".inspect-floor-plan");
  app.submit("catalog-form");
  const request = app.calls.at(-1); const count = app.calls.length;
  assert.equal(app.get("catalog-submit").disabled, true);
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("ask-check").disabled, true);
  assert.equal(app.get("catalog-rent").disabled, true);
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
  oldButton.dispatchEvent(new app.window.MouseEvent("click", { bubbles: true }));
  app.submit("ask-form"); app.submit("catalog-form"); app.click("ask-check"); await tick();
  assert.equal(app.calls.length, count);
  app.click("catalog-cancel");
  assert.equal(request.options.signal.aborted, true);
  assert.equal(app.get("catalog-submit").disabled, false);
  assert.match(app.get("catalog-state-message").textContent, /Stopped waiting/);
  finish(response(catalogResponse())); await tick();
  assert.equal(app.get("catalog-result").classList.contains("hidden"), true);
  assert.equal(app.get("catalog-evidence").textContent, "");
  assert.equal(app.calls.length, count);
});

for (const action of ["refresh", "lock", "token", "cancel"]) {
  test("late catalog JSON cannot restore records after " + action, async (t) => {
    let finish;
    const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? { ok: true, status: 200, json: () => new Promise((resolve) => { finish = resolve; }) } : catalogRoute(url, options));
    await tick(); await openAsk(app); app.submit("catalog-form"); await tick();
    const request = app.calls.at(-1);
    if (action === "refresh") app.click("refresh-button");
    else if (action === "cancel") app.click("catalog-cancel");
    else {
      app.click("lock-button");
      if (action === "token") { app.get("app-token").value = "replacement"; app.submit("access-form"); }
    }
    await tick(); assert.equal(request.options.signal.aborted, true);
    finish(catalogResponse()); await tick();
    assert.equal(app.get("catalog-result").classList.contains("hidden"), true);
    assert.equal(app.get("catalog-evidence").textContent, "");
    assert.equal(app.get("inspection-evidence").textContent, "");
  });
}

test("a new AI question clears direct results and invalidates retained selection controls", async (t) => {
  const app = boot(t, (url, options) => url === "/api/v1/agent/status" ? response(agentStatus({ costar_enabled: true, matterport_enabled: true })) : catalogRoute(url, options));
  await showCatalog(app);
  const button = app.get("catalog-evidence").querySelector(".inspect-floor-plan");
  button.click(); await tick();
  assert.notEqual(app.get("inspection-evidence").textContent, "");
  typeQuestion(app); app.submit("ask-form"); await tick();
  assert.equal(app.get("catalog-result").classList.contains("hidden"), true);
  assert.equal(app.get("catalog-evidence").textContent, "");
  assert.equal(app.get("inspection-evidence").textContent, "");
  const count = app.calls.length;
  button.dispatchEvent(new app.window.MouseEvent("click", { bubbles: true })); await tick();
  assert.equal(app.calls.length, count);
});

test("catalog errors and malformed responses are sanitized without retry or AI fallback", async (t) => {
  let reply;
  let bodyReads = 0;
  const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? reply() : catalogRoute(url, options));
  await tick(); await openAsk(app);
  const replies = [
    ...[403, 422, 429, 503, 504, 500].map((status) => () => ({ ok: false, status, json: async () => { bodyReads += 1; throw new Error("private provider token"); } })),
    () => ({ ok: true, status: 200, json: async () => { throw new Error("private provider token"); } }),
    () => { throw new Error("private provider token"); },
    () => response({ status: "unavailable", reason: "private provider token" }),
    () => response({ ...catalogResponse(), source_hash: "invalid" }),
    () => response({ ...catalogResponse(), results: [null] }),
  ];
  for (reply of replies) {
    const count = app.calls.length;
    app.submit("catalog-form"); await tick();
    assert.equal(app.calls.length, count + 1);
    assert.notEqual(app.get("catalog-error").textContent, "");
    assert.equal(app.get("catalog-evidence").textContent, "");
    assert.equal(app.get("catalog-submit").disabled, false);
    assert.doesNotMatch(app.document.documentElement.outerHTML, /private provider token/);
  }
  assert.equal(bodyReads, 0);
  assert.equal(app.calls.some((call) => call.url === "/api/v1/agent/answer"), false);
});

test("catalog authentication failure locks and clears search inputs and all prior evidence", async (t) => {
  const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? response({}, 401) : inspectionRoute(url, options));
  await showShortlist(app); app.change("catalog-city", "Private draft city"); app.change("catalog-rent", "1000");
  app.submit("catalog-form"); await tick();
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  assert.match(app.get("access-error").textContent, /app token was not accepted/);
  assert.equal(app.get("ask-answer").textContent, "");
  assert.equal(app.get("catalog-evidence").textContent, "");
  assert.equal(app.get("catalog-city").value, "");
  assert.equal(app.get("catalog-rent").value, "1800");
  assert.equal(app.get("catalog-submit").disabled, true);
});

test("catalog deadline releases controls and rejects the late result without retry", async (t) => {
  let finish;
  const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? new Promise((resolve) => { finish = resolve; }) : catalogRoute(url, options));
  await tick(); await openAsk(app);
  let deadline;
  app.window.setTimeout = (callback, milliseconds) => { if (milliseconds === 60000) deadline = callback; return 1; };
  app.submit("catalog-form");
  const request = app.calls.at(-1); const count = app.calls.length;
  assert.equal(typeof deadline, "function"); deadline();
  assert.equal(request.options.signal.aborted, true);
  assert.equal(app.get("catalog-submit").disabled, false);
  assert.match(app.get("catalog-error").textContent, /timed out/);
  finish(response(catalogResponse())); await tick();
  assert.equal(app.get("catalog-evidence").textContent, "");
  assert.equal(app.calls.length, count);
});

test("AI answer appears directly after its question and before the apartment form", async (t) => {
  const app = boot(t, askRoute);
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  assert.equal(app.document.querySelector(".ask-compose").nextElementSibling, app.get("ask-result"));
  assert.equal(app.get("ask-result").nextElementSibling, app.document.querySelector(".catalog-compose"));
  assert.equal(app.get("ask-result").classList.contains("hidden"), false);
});

test("direct search and inspection announce accepted completion with counts and limits", async (t) => {
  let empty = false;
  const app = boot(t, (url, options) => {
    if (url === "/api/v1/costar/search" && empty) return response({ ...catalogResponse(JSON.parse(options.body)), results: [] });
    return catalogRoute(url, options);
  });
  await showCatalog(app);
  assert.match(app.get("catalog-state-message").textContent, /^Catalog search complete\. 2 recorded matches returned/);
  assert.equal(app.get("catalog-state-message").getAttribute("aria-live"), "polite");
  assert.equal(app.get("catalog-form").getAttribute("aria-busy"), "false");
  app.get("catalog-evidence").querySelector(".inspect-floor-plan").click(); await tick();
  assert.match(app.get("inspection-state").textContent, /^Inspection complete\./);
  assert.match(app.get("inspection-state").textContent, /missing measurements.*Furniture fit was not verified/);
  assert.equal(app.get("inspection-state").getAttribute("role"), "status");
  assert.equal(app.get("inspection-result").getAttribute("aria-busy"), "false");
  empty = true; app.submit("catalog-form"); await tick();
  assert.match(app.get("catalog-state-message").textContent, /No recorded matches.*not a whole-market result/);
  assert.equal(app.get("inspection-state").textContent, "");
});

for (const phase of ["answer", "search", "inspect"]) {
  test(phase + " completion does not move focus away from another page", async (t) => {
    let finish;
    const endpoint = { answer: "/api/v1/agent/answer", search: "/api/v1/costar/search", inspect: "/api/v1/costar/inspect" }[phase];
    const app = boot(t, (url, options) => {
      if (url === endpoint) return new Promise((resolve) => { finish = resolve; });
      if (url === "/api/v1/agent/status") return response(agentStatus({ costar_enabled: true, matterport_enabled: true }));
      return catalogRoute(url, options);
    });
    if (phase === "inspect") { await showCatalog(app); app.get("catalog-evidence").querySelector(".inspect-floor-plan").click(); }
    else {
      await tick(); await openAsk(app);
      if (phase === "answer") { typeQuestion(app); app.submit("ask-form"); }
      else app.submit("catalog-form");
    }
    const overview = app.document.querySelector('[data-view="overview"]'); overview.click(); overview.focus();
    finish(response({ answer: agentAnswer(), search: catalogResponse(), inspect: inspectionResponse() }[phase])); await tick();
    assert.equal(app.document.activeElement, overview);
    assert.equal(app.get("ask-view").classList.contains("hidden"), true);
    const status = app.get({ answer: "ask-state", search: "catalog-state-message", inspect: "inspection-state" }[phase]);
    assert.match(status.textContent, /ready|complete/);
  });
}

for (const phase of ["search", "inspect"]) {
  test("explicit " + phase + " cancellation restores the correct current control and ignores late completion", async (t) => {
    let finish;
    const endpoint = phase === "search" ? "/api/v1/costar/search" : "/api/v1/costar/inspect";
    const app = boot(t, (url, options) => url === endpoint ? new Promise((resolve) => { finish = resolve; }) : catalogRoute(url, options));
    let target;
    if (phase === "inspect") {
      await showCatalog(app); target = app.get("catalog-evidence").querySelectorAll(".inspect-floor-plan")[1]; target.click();
    } else { await tick(); await openAsk(app); target = app.get("catalog-submit"); app.submit("catalog-form"); }
    const cancel = app.get(phase === "search" ? "catalog-cancel" : "inspection-cancel");
    cancel.focus(); assert.equal(app.document.activeElement, cancel); cancel.click();
    assert.equal(app.document.activeElement, target);
    assert.equal(target.disabled, false);
    assert.equal(cancel.classList.contains("hidden"), true);
    const status = app.get(phase === "search" ? "catalog-state-message" : "inspection-state");
    assert.match(status.textContent, /Stopped waiting/);
    finish(response(phase === "search" ? catalogResponse() : inspectionResponse({ property_id: 21, floor_plan_id: 31 }))); await tick();
    assert.equal(app.document.activeElement, target);
    assert.match(status.textContent, /Stopped waiting/);
  });
}

test("cancel focus restoration skips hidden pages, detached selection buttons, and invalidated contexts", async (t) => {
  const app = boot(t, (url, options) => url === "/api/v1/costar/inspect" ? new Promise(() => {}) : catalogRoute(url, options));
  await showCatalog(app);
  let target = app.get("catalog-evidence").querySelector(".inspect-floor-plan");
  target.click();
  const overview = app.document.querySelector('[data-view="overview"]'); overview.click(); overview.focus();
  app.click("inspection-cancel");
  assert.equal(app.document.activeElement, overview);
  await openAsk(app);
  target.click(); app.get("inspection-cancel").focus(); target.remove(); app.click("inspection-cancel");
  assert.notEqual(app.document.activeElement, target);
  target = app.get("catalog-evidence").querySelector(".inspect-floor-plan");
  target.click(); app.click("refresh-button"); await tick();
  app.get("refresh-button").focus(); app.click("inspection-cancel");
  assert.equal(app.document.activeElement, app.get("refresh-button"));
  assert.equal(app.get("inspection-state").textContent, "");
});
