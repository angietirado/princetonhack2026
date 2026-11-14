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

function comparisonPeriods() {
  return { baseline: { start: "2026-11-01", end: "2026-11-07" }, current: { start: "2026-11-08", end: "2026-11-14" } };
}
function comparisonResponse(periods = comparisonPeriods()) {
  const total = { baseline_cents: 2500, current_cents: 4500, delta_cents: 2000, percent_change_bps: 8000, percent_status: "defined" };
  const stats = (period, gross, refunds) => ({ ...period, duration_days: Math.round((new Date(period.end) - new Date(period.start)) / 86400000) + 1, gross_spending_cents: gross, refunds_cents: refunds, net_spending_cents: gross - refunds, observed_count: 2, included_count: 2, excluded_count: 0 });
  const section = (name, identity, key) => ({ rows: [{ ...total, key, label: name, label_truncated: false, identity_kind: identity }], total_groups: 1, omitted_groups: 0, omitted_baseline_cents: 0, omitted_current_cents: 0, omitted_delta_cents: 0 });
  const baseline = stats(periods.baseline, 3000, 500), current = stats(periods.current, 5500, 1000);
  const categoryKey = "c".repeat(64), merchantKey = "d".repeat(64);
  const evidence = [["baseline", "purchase", 3000], ["baseline", "refund", -500], ["current", "purchase", 5500], ["current", "refund", -1000]].map(([period, kind, amount]) => ({ id: period + "-" + kind, period, occurred_at: periods[period].start + "T12:00:00Z", category_key: categoryKey, merchant_key: merchantKey, signed_spending_cents: amount }));
  return { status: "ok", comparison_id: "b".repeat(64), calculation_version: "spending-comparison-v1", source: "fixture", source_hash: "a".repeat(64), currency: "USD", coverage: "observed_records_only", baseline, current, equal_duration: baseline.duration_days === current.duration_days, total, categories: section("Dining", "category", categoryKey), merchants: section("Cafe", "merchant_id", merchantKey), findings: [{ finding_id: "e".repeat(64), scope: "total", group_key: null, assertion: "increased", joint_largest: false, text: "Observed net spending increased by $20.00 USD." }], caveats: ["Observed records only; completeness is unknown."], transaction_evidence: evidence, evidence_summary: { eligible_count: 4, returned_count: 4, omitted_count: 0, baseline_returned: 2, current_returned: 2, truncated: false } };
}
function fillComparison(app, periods = comparisonPeriods()) {
  for (const key of ["baseline", "current"]) for (const edge of ["start", "end"]) app.change("comparison-" + key + "-" + edge, periods[key][edge]);
}

test("comparison submits exact explicit periods with memory auth and no model call", async (t) => {
  const app = boot(t, (url, options) => {
    if (options.headers.Authorization !== "Bearer comparison-token") return response({}, 401);
    return url === "/api/v1/spending/compare" ? response(comparisonResponse(JSON.parse(options.body))) : response(payload());
  });
  await tick(); app.get("app-token").value = "comparison-token"; app.submit("access-form"); await tick();
  const before = app.calls.length;
  fillComparison(app);
  assert.equal(app.calls.length, before);
  app.submit("comparison-form"); await tick();
  const request = app.calls.at(-1);
  assert.equal(request.url, "/api/v1/spending/compare");
  assert.deepEqual(JSON.parse(request.options.body), comparisonPeriods());
  assert.equal(request.options.cache, "no-store");
  assert.equal(request.options.redirect, "error");
  assert.match(app.get("comparison-totals").textContent, /\$25\.00.*\$45\.00.*\$20\.00.*80\.00%/);
  assert.match(app.get("comparison-findings").textContent, /Observed net spending increased/);
  assert.match(app.get("comparison-state").textContent, /Comparison ready/);
  assert.equal(app.get("comparison-details").open, false);
  assert.equal(app.calls.some((call) => call.url.includes("agent")), false);
  assert.equal(app.window.localStorage.length + app.window.sessionStorage.length, 0);
});

test("displayed-period shortcut fills adjacent equal periods without a request", async (t) => {
  const app = boot(t); await tick();
  const before = app.calls.length;
  app.click("comparison-use-period");
  assert.equal(app.get("comparison-current-start").value, "2026-10-01");
  assert.equal(app.get("comparison-current-end").value, "2026-11-14");
  assert.equal(app.get("comparison-baseline-start").value, "2026-08-17");
  assert.equal(app.get("comparison-baseline-end").value, "2026-09-30");
  assert.equal(app.calls.length, before);
  assert.match(app.get("comparison-state").textContent, /Dates filled/);
});

test("comparison rejects invalid or overlapping dates before a read", async (t) => {
  const app = boot(t); await tick();
  for (const periods of [{ baseline: { start: "", end: "" }, current: comparisonPeriods().current },
    { ...comparisonPeriods(), baseline: { start: "2026-11-07", end: "2026-11-01" } },
    { ...comparisonPeriods(), current: { start: "2026-11-07", end: "2026-11-14" } }]) {
    fillComparison(app, periods); app.submit("comparison-form"); await tick();
    assert.match(app.get("comparison-error").textContent, /Choose valid dates/);
    assert.equal(app.calls.length, 1);
  }
});

test("comparison rejects mismatched periods and inconsistent financial totals", async (t) => {
  let mutate;
  const app = boot(t, (url) => {
    if (url !== "/api/v1/spending/compare") return response(payload());
    const value = comparisonResponse(); mutate(value); return response(value);
  });
  await tick(); fillComparison(app);
  for (mutate of [
    (r) => { r.current.start = "2026-11-09"; }, (r) => { r.total.delta_cents = 1; },
    (r) => { r.merchants.omitted_current_cents = 1; }, (r) => { r.source_hash = "bad"; },
    (r) => { r.total.current_cents = 1.5; }, (r) => { r.current.duration_days = 1; },
  ]) {
    app.submit("comparison-form"); await tick();
    assert.match(app.get("comparison-error").textContent, /valid comparison matching your periods/);
    assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
  }
});

test("comparison rejects percentages inconsistent with positive baseline and delta", async (t) => {
  let mutate;
  const app = boot(t, (url) => {
    if (url !== "/api/v1/spending/compare") return response(payload());
    const value = comparisonResponse(); mutate(value); return response(value);
  });
  await tick(); fillComparison(app);
  // Independently: (4500 - 2500) / 2500 * 100 = 80%, or 8000 basis points.
  for (mutate of [
    (r) => { r.total.percent_change_bps = 1; },
    (r) => { r.categories.rows[0].percent_change_bps = -8000; },
    (r) => { r.merchants.rows[0].percent_change_bps = 8001; },
  ]) {
    app.submit("comparison-form"); await tick();
    assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
    assert.match(app.get("comparison-error").textContent, /valid comparison matching your periods/);
  }
});

test("comparison preserves exact positive and negative half-up percentage ties", async (t) => {
  let delta = -1;
  const app = boot(t, (url) => {
    if (url !== "/api/v1/spending/compare") return response(payload());
    const value = comparisonResponse();
    const total = { baseline_cents: 20000, current_cents: 20000 + delta, delta_cents: delta, percent_change_bps: delta, percent_status: "defined" };
    value.total = total;
    value.baseline.net_spending_cents = value.baseline.gross_spending_cents = 20000; value.baseline.refunds_cents = 0;
    value.current.net_spending_cents = value.current.gross_spending_cents = 20000 + delta; value.current.refunds_cents = 0;
    for (const key of ["categories", "merchants"]) Object.assign(value[key].rows[0], total);
    value.transaction_evidence.forEach((row) => { row.signed_spending_cents = row.id.endsWith("refund") ? 0 : row.period === "baseline" ? 20000 : 20000 + delta; });
    value.findings[0].assertion = delta < 0 ? "decreased" : "increased";
    value.findings[0].text = "Observed net spending changed by one cent.";
    return response(value);
  });
  await tick(); fillComparison(app);
  for (delta of [-1, 1]) {
    app.submit("comparison-form"); await tick();
    assert.equal(app.get("comparison-result").classList.contains("hidden"), false);
    assert.match(app.get("comparison-totals").textContent, delta < 0 ? /-0\.01%/ : /Total change: 0\.01%/);
  }
});

test("comparison rejects group sums hidden by floating point cancellation", async (t) => {
  const value = comparisonResponse();
  const total = { baseline_cents: 1, current_cents: 1, delta_cents: 0, percent_change_bps: 0, percent_status: "defined" };
  value.total = total; value.baseline.net_spending_cents = value.current.net_spending_cents = 1;
  Object.assign(value.merchants.rows[0], total);
  value.categories.rows = [Number.MAX_SAFE_INTEGER, 2, -Number.MAX_SAFE_INTEGER].map((amount, index) => ({ ...value.categories.rows[0], key: String(index), baseline_cents: amount, current_cents: amount, delta_cents: 0, percent_change_bps: amount > 0 ? 0 : null, percent_status: amount > 0 ? "defined" : "negative_baseline" }));
  value.categories.total_groups = 3;
  const app = boot(t, (url) => response(url === "/api/v1/spending/compare" ? value : payload()));
  await tick(); fillComparison(app); app.submit("comparison-form"); await tick();
  // These three exact integer groups total 2 cents, never the claimed 1 cent.
  assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
  assert.match(app.get("comparison-error").textContent, /valid comparison matching your periods/);
});

test("comparison preserves undefined percentages, omitted groups and text safety", async (t) => {
  const value = comparisonResponse();
  const zero = { ...value.total, baseline_cents: 0, current_cents: 1000, delta_cents: 1000, percent_change_bps: null, percent_status: "zero_baseline" };
  value.total = zero; value.baseline.net_spending_cents = 0; value.current.net_spending_cents = 1000;
  for (const key of ["categories", "merchants"]) value[key] = { rows: [{ ...zero, label: '<img src=x onerror="alert(1)">', label_truncated: true }], omitted_groups: 2, omitted_baseline_cents: 0, omitted_current_cents: 0, omitted_delta_cents: 0 };
  value.findings = [{ text: "Observed total from an empty baseline; missing history is unknown." }];
  const app = boot(t, (url) => response(url === "/api/v1/spending/compare" ? value : payload()));
  await tick(); fillComparison(app); app.submit("comparison-form"); await tick();
  assert.match(app.get("comparison-totals").textContent, /percentage change is undefined.*zero/);
  assert.match(app.get("comparison-merchants").textContent, /2 more groups/);
  assert.equal(app.get("comparison-result").querySelector("img"), null);
});

for (const action of ["refresh", "lock", "cancel"]) {
  test("late comparison JSON cannot restore discarded records after " + action, async (t) => {
    let finish;
    const app = boot(t, (url) => url === "/api/v1/spending/compare" ? { ok: true, status: 200, json: () => new Promise((resolve) => { finish = resolve; }) } : response(payload()));
    await tick(); fillComparison(app); app.submit("comparison-form"); await tick();
    const request = app.calls.at(-1), count = app.calls.length;
    app.submit("comparison-form"); assert.equal(app.calls.length, count);
    app.click(action === "cancel" ? "comparison-cancel" : action === "lock" ? "lock-button" : "refresh-button");
    await tick(); assert.equal(request.options.signal.aborted, true);
    finish(comparisonResponse()); await tick();
    assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
    assert.equal(app.get("comparison-evidence").textContent, "");
    if (action === "lock") assert.equal(app.get("comparison-current-start").value, "");
  });
}

test("comparison errors and deadline do not retry or retain prior results", async (t) => {
  let code = 503;
  const timers = [];
  let finish;
  const app = boot(t, (url) => url === "/api/v1/spending/compare" ? code ? response({ detail: "private error" }, code) : new Promise((resolve) => { finish = resolve; }) : response(payload()));
  await tick(); fillComparison(app);
  for (code of [422, 429, 503, 504, 500]) {
    const count = app.calls.length; app.submit("comparison-form"); await tick();
    assert.equal(app.calls.length, count + 1);
    assert.doesNotMatch(app.get("comparison-error").textContent, /private/);
  }
  app.window.setTimeout = (fn, milliseconds) => { timers.push({ fn, milliseconds }); return timers.length; };
  code = 0; app.submit("comparison-form");
  const request = app.calls.at(-1);
  timers.find((timer) => timer.milliseconds === 50000).fn();
  assert.equal(request.options.signal.aborted, true);
  assert.equal(app.get("comparison-submit").disabled, false);
  finish(response(comparisonResponse())); await tick();
  assert.match(app.get("comparison-error").textContent, /timed out/);
  assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
});

test("comparison authorization failure locks and clears dates and prior records", async (t) => {
  const app = boot(t, (url, options) => {
    if (options.headers.Authorization !== "Bearer comparison-acceptance-token") return response({}, 401);
    return url === "/api/v1/spending/compare" ? response({}, 401) : response(payload());
  });
  await tick(); app.get("app-token").value = "comparison-acceptance-token"; app.submit("access-form"); await tick();
  fillComparison(app); app.submit("comparison-form"); await tick();
  assert.equal(app.get("access-panel").classList.contains("hidden"), false);
  assert.equal(app.document.activeElement, app.get("app-token"));
  assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
  assert.equal(app.get("comparison-evidence").textContent, "");
  for (const key of ["baseline", "current"]) for (const edge of ["start", "end"]) assert.equal(app.get("comparison-" + key + "-" + edge).value, "");
  assert.match(app.get("access-error").textContent, /valid app token to compare/);
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/spending/compare").length, 1);
});

for (const phase of ["headers", "body"]) {
  test("late comparison " + phase + " cannot replace a result after a credential change", async (t) => {
    let finish;
    const app = boot(t, (url, options) => {
      if (!options.headers.Authorization) return response({}, 401);
      if (url !== "/api/v1/spending/compare") return response(payload());
      if (options.headers.Authorization === "Bearer old-acceptance-token") {
        if (phase === "headers") return new Promise((resolve) => { finish = resolve; });
        return { ok: true, status: 200, json: () => new Promise((resolve) => { finish = resolve; }) };
      }
      return response(comparisonResponse());
    });
    await tick(); app.get("app-token").value = "old-acceptance-token"; app.submit("access-form"); await tick();
    fillComparison(app); app.submit("comparison-form"); await tick();
    const oldRequest = app.calls.at(-1);
    app.click("lock-button"); app.get("app-token").value = "new-acceptance-token"; app.submit("access-form"); await tick();
    fillComparison(app); app.submit("comparison-form"); await tick();
    assert.equal(oldRequest.options.signal.aborted, true);
    const accepted = app.get("comparison-evidence").textContent;
    assert.notEqual(accepted, "");
    const oldBody = comparisonResponse(); oldBody.findings[0].text = "STALE PRIOR CUSTOMER CLAIM";
    finish(phase === "headers" ? response({}, 401) : oldBody); await tick();
    assert.equal(app.get("access-panel").classList.contains("hidden"), true);
    assert.equal(app.get("comparison-evidence").textContent, accepted);
    assert.doesNotMatch(app.document.body.textContent, /STALE PRIOR CUSTOMER CLAIM/);
    assert.equal(app.calls.filter((call) => call.url === "/api/v1/spending/compare").length, 2);
  });
}

test("comparison cancellation restores visible focus with labeled dates and live busy state", async (t) => {
  let finish;
  const app = boot(t, (url) => url === "/api/v1/spending/compare" ? new Promise((resolve) => { finish = resolve; }) : response(payload()));
  await tick();
  app.document.querySelector('[data-view="insights"]').click();
  assert.equal(app.calls.length, 1);
  for (const key of ["baseline", "current"]) for (const edge of ["start", "end"]) {
    const input = app.get("comparison-" + key + "-" + edge);
    assert.equal(input.type, "date");
    assert.ok(input.labels.length > 0);
    assert.ok(input.closest("fieldset").querySelector("legend").textContent.trim());
  }
  assert.equal(app.get("comparison-state").getAttribute("role"), "status");
  assert.equal(app.get("comparison-state").getAttribute("aria-live"), "polite");
  assert.equal(app.get("comparison-error").getAttribute("role"), "alert");
  fillComparison(app); assert.equal(app.calls.length, 1);
  app.submit("comparison-form");
  assert.equal(app.get("comparison-form").getAttribute("aria-busy"), "true");
  assert.equal(app.get("comparison-submit").disabled, true);
  app.get("comparison-cancel").focus(); app.click("comparison-cancel");
  assert.equal(app.document.activeElement, app.get("comparison-submit"));
  assert.equal(app.get("comparison-form").getAttribute("aria-busy"), "false");
  assert.match(app.get("comparison-state").textContent, /server may finish processing/);
  finish(response(comparisonResponse())); await tick();
  assert.equal(app.get("comparison-result").classList.contains("hidden"), true);
  assert.equal(app.calls.length, 2);
});

test("comparison completion does not move focus after the user navigates away", async (t) => {
  let finish;
  const app = boot(t, (url) => url === "/api/v1/spending/compare" ? new Promise((resolve) => { finish = resolve; }) : response(payload()));
  await tick(); app.document.querySelector('[data-view="insights"]').click();
  fillComparison(app); app.submit("comparison-form");
  app.document.querySelector('[data-view="transactions"]').click();
  const destination = app.get("transactions-view").querySelector("h1");
  finish(response(comparisonResponse())); await tick();
  assert.equal(app.document.activeElement, destination);
  assert.equal(app.get("insights-view").classList.contains("hidden"), true);
  assert.match(app.get("comparison-state").textContent, /Comparison ready/);
});

test("overview chart uses its visible width after resizing from another view", async (t) => {
  const app = boot(t, askRoute);
  await tick();
  let visibleWidth = 800;
  const chart = app.get("monthly-chart");
  Object.defineProperty(chart, "clientWidth", { configurable: true, get: () => app.get("overview-view").classList.contains("hidden") ? 0 : visibleWidth });
  app.window.dispatchEvent(new app.window.Event("resize"));
  assert.equal(chart.querySelector("svg").style.minWidth, "800px");
  await openAsk(app);
  visibleWidth = 248;
  app.window.dispatchEvent(new app.window.Event("resize"));
  app.document.querySelector('[data-view="overview"]').click();
  assert.equal(chart.querySelector("svg").style.minWidth, "248px");
  assert.match(chart.querySelector("svg").getAttribute("aria-label"), /Monthly net spending in USD/);
});

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
    category_mapping: { version: "source-categories-v1", entries: [{ raw_category: "groceries", category_id: "groceries", category_label: "Groceries", subcategory_id: null, subcategory_label: null, origin: "source_mapping" }] },
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

// These ten records and expected query memberships were specified before the feature.
function categoryPayload() {
  const result = payload();
  result.source = result.summary.source = "fixture";
  result.customer_id = "category-customer";
  result.accounts = ["account-a", "account-b"].map((id) => ({ id, customer_id: result.customer_id, nickname: "Checking", type: "Checking", balance_cents: 0 }));
  result.cash_balance_cents = result.credit_balance_cents = 0;
  const records = [
    ["coffee-a", "account-a", "coffee", "Corner Shop", 450, "purchase", "completed"],
    ["coffee-b", "account-b", "coffee", "Corner Shop", 650, "purchase", "pending"],
    ["cafe-dining", "account-a", "dining", "Campus Coffee", 700, "purchase", "completed"],
    ["rent-a", "account-a", "rent", "Property", 150000, "bill_payment", "completed"],
    ["grocery-a", "account-a", "groceries", "Market", 12050, "purchase", "completed"],
    ["grocery-refund", "account-a", "groceries", "Market", 2050, "refund", "completed"],
    ["unknown-zero", "account-a", "__proto__", "Unknown", 0, "fee", "cancelled"],
    ["transport-a", "account-b", "transport", "Transit", 3000, "purchase", "completed"],
    ["coffee-trim", "account-a", " coffee ", "Corner Shop", 500, "purchase", "completed"],
    ["coffee-case", "account-b", "COFFEE", "Corner Shop", 600, "purchase", "completed"],
  ];
  result.transactions = records.map(([id, account_id, category, merchant, amount_cents, kind, status]) => ({ id, account_id, category, merchant, merchant_id: "merchant-" + id, amount_cents, kind, status, description: id, occurred_at: "2026-11-01T12:00:00Z" }));
  const entry = (raw_category, category_id, category_label, subcategory_id = null, subcategory_label = null, origin = "source_mapping") => ({ raw_category, category_id, category_label, subcategory_id, subcategory_label, origin });
  result.category_mapping = { version: "source-categories-v1", entries: [
    entry(" coffee ", "dining", "Dining", "coffee", "Coffee"),
    entry("COFFEE", "dining", "Dining", "coffee", "Coffee"),
    entry("__proto__", "unclassified", "Unclassified", null, null, "unclassified"),
    entry("coffee", "dining", "Dining", "coffee", "Coffee"),
    entry("dining", "dining", "Dining"), entry("groceries", "groceries", "Groceries"),
    entry("rent", "housing", "Housing", "rent", "Rent"), entry("transport", "transport", "Transport"),
  ] };
  result.gross_spending_cents = 167300; result.refunds_cents = 2050;
  result.summary = { source: "fixture", currency: "USD", start: "2026-11-01", end: "2026-11-01", net_spending_cents: 165250, included_count: 8, excluded_count: 2, categories: [
    { category: "rent", amount_cents: 150000 }, { category: "groceries", amount_cents: 10000 },
    { category: "transport", amount_cents: 3000 }, { category: "dining", amount_cents: 700 },
    { category: "COFFEE", amount_cents: 600 }, { category: " coffee ", amount_cents: 500 }, { category: "coffee", amount_cents: 450 },
  ] };
  result.expense_categories = result.summary.categories.map((row) => ({ ...row, amount_cents: row.category === "groceries" ? 12050 : row.amount_cents }));
  result.monthly_spending = [{ month: "2026-11", net_spending_cents: 165250 }];
  result.recurring = [];
  return result;
}

function input(app, id, value) {
  app.get(id).value = value;
  app.get(id).dispatchEvent(new app.window.Event("input", { bubbles: true }));
}
function categoryFilters(app, values) {
  const selects = { category: "transaction-category", subcategory: "transaction-subcategory", account: "transaction-account", kind: "transaction-kind" };
  for (const [key, value] of Object.entries(values)) {
    if (selects[key]) app.change(selects[key], value);
    else if (key === "status") app.document.querySelector('[data-filter="' + value + '"]').click();
    else input(app, { min: "transaction-min-amount", max: "transaction-max-amount", search: "transaction-search" }[key], value);
  }
}
function visibleCategoryIds(app) {
  return Array.from(app.get("transaction-rows").querySelectorAll("tr"), (row) => row.querySelector("td:first-child small")?.textContent).filter(Boolean);
}
function exportedCategoryIds(app) {
  app.click("export-transactions");
  return parseCsv(app.blobs.at(-1).parts.join("")).slice(1).map((row) => row[0]);
}

test("category filters intersect exact source labels, accounts, kinds, amounts and existing filters", async (t) => {
  const app = boot(t, () => response(categoryPayload())); await tick();
  const calls = app.calls.length;
  const expected = [
    [{ category: "dining" }, ["coffee-a", "coffee-b", "cafe-dining", "coffee-trim", "coffee-case"]],
    [{ category: "dining", subcategory: "coffee" }, ["coffee-a", "coffee-b", "coffee-trim", "coffee-case"]],
    [{ category: "dining", subcategory: "coffee", account: "account-a", status: "completed", min: "4.50", max: "5.00" }, ["coffee-a", "coffee-trim"]],
    [{ category: "groceries", kind: "refund" }, ["grocery-refund"]],
    [{ min: "0", max: "0" }, ["unknown-zero"]],
    [{ status: "other", min: "0", max: "0" }, ["unknown-zero"]],
    [{ account: "account-b", status: "completed", min: "6.00", max: "30.00" }, ["transport-a", "coffee-case"]],
    [{ search: "coffee" }, ["coffee-a", "coffee-b", "cafe-dining", "coffee-trim", "coffee-case"]],
    [{ category: "unclassified" }, ["unknown-zero"]],
  ];
  for (const [filters, ids] of expected) {
    app.click("transaction-reset-filters"); categoryFilters(app, filters);
    assert.deepEqual(visibleCategoryIds(app), ids, JSON.stringify(filters));
    assert.deepEqual(exportedCategoryIds(app), ids, "CSV " + JSON.stringify(filters));
    assert.equal(app.get("spend-value").textContent, "$1,652.50");
    assert.equal(app.get("report-total").textContent, "$1,652.50");
  }
  assert.equal(app.calls.length, calls, "table filters do not fetch or change the dashboard period");
  for (const id of ["transaction-category", "transaction-subcategory", "transaction-account", "transaction-kind", "transaction-min-amount", "transaction-max-amount"]) {
    assert.ok(app.document.querySelector('label[for="' + id + '"]') || app.get(id).closest("label"), id + " has a visible label");
  }
  const accountLabels = Array.from(app.get("transaction-account").options, (option) => option.textContent).join(" ");
  assert.match(accountLabels, /Checking.*account-a/); assert.match(accountLabels, /Checking.*account-b/);
});

test("broad-only classifications remain distinct from explicit detail and source labels survive CSV", async (t) => {
  const app = boot(t, () => response(categoryPayload())); await tick();
  categoryFilters(app, { category: "dining", subcategory: "__none__" });
  assert.deepEqual(visibleCategoryIds(app), ["cafe-dining"]);
  app.click("export-transactions");
  const row = parseCsv(app.blobs.at(-1).parts.join(""))[1];
  assert.equal(row[3], "Campus Coffee"); assert.equal(row[5], "dining");
  assert.deepEqual(row.slice(10), ["Dining", "", "source_mapping", "source-categories-v1"]);
  app.change("transaction-subcategory", "coffee"); app.change("transaction-category", "housing");
  assert.equal(app.get("transaction-subcategory").value, "");
  assert.deepEqual(visibleCategoryIds(app), ["rent-a"]);
  app.change("transaction-category", "dining"); app.change("transaction-subcategory", "__none__");
  app.change("transaction-category", "housing");
  assert.equal(app.get("transaction-subcategory").value, "__none__");
  assert.deepEqual(visibleCategoryIds(app), []);
  app.click("transaction-reset-filters"); categoryFilters(app, { search: "coffee-trim" });
  app.click("export-transactions");
  const trimmed = parseCsv(app.blobs.at(-1).parts.join(""))[1];
  assert.equal(trimmed[5], " coffee ");
  assert.deepEqual(trimmed.slice(10), ["Dining", "Coffee", "source_mapping", "source-categories-v1"]);
});

test("USD bounds are exact cents and invalid input never exports misleading matches", async (t) => {
  const result = categoryPayload();
  result.transactions[0].amount_cents = 29;
  result.gross_spending_cents -= 421; result.summary.net_spending_cents -= 421;
  result.monthly_spending[0].net_spending_cents -= 421;
  result.summary.categories.find((row) => row.category === "coffee").amount_cents = 29;
  result.expense_categories.find((row) => row.category === "coffee").amount_cents = 29;
  const app = boot(t, () => response(result)); await tick();
  categoryFilters(app, { min: "0.29", max: "0.29" });
  assert.deepEqual(visibleCategoryIds(app), ["coffee-a"]);
  assert.deepEqual(exportedCategoryIds(app), ["coffee-a"]);
  for (const [minimum, maximum] of [["-1", ""], ["1e3", ""], ["NaN", ""], ["Infinity", ""], ["12.345", ""], ["12.", ""], ["90071992547409.92", ""], ["5.01", "5.00"]]) {
    categoryFilters(app, { min: minimum, max: maximum });
    assert.ok(app.get("transaction-filter-error").textContent.trim(), minimum);
    assert.deepEqual(visibleCategoryIds(app), []);
    assert.equal(app.get("export-transactions").disabled, true);
    const before = app.blobs.length; app.click("export-transactions"); assert.equal(app.blobs.length, before);
  }
  categoryFilters(app, { min: "0", max: "90071992547409.91" });
  assert.equal(app.get("transaction-filter-error").textContent, "");
  assert.equal(visibleCategoryIds(app).length, 10);
  for (const maximum of ["12", "12.3", "12.34"]) {
    categoryFilters(app, { min: "", max: maximum });
    assert.equal(app.get("transaction-filter-error").textContent, "");
  }
});

test("new filters reset pagination and export every matching row beyond the current page", async (t) => {
  const result = categoryPayload();
  const extra = Array.from({ length: 27 }, (_, index) => ({ ...result.transactions[0], id: "bulk-" + index, description: "bulk-" + index }));
  result.transactions.push(...extra);
  result.gross_spending_cents += 12150; result.summary.net_spending_cents += 12150; result.summary.included_count += 27;
  result.monthly_spending[0].net_spending_cents += 12150;
  result.summary.categories.find((row) => row.category === "coffee").amount_cents += 12150;
  result.expense_categories.find((row) => row.category === "coffee").amount_cents += 12150;
  const app = boot(t, () => response(result)); await tick();
  categoryFilters(app, { category: "dining", subcategory: "coffee" });
  assert.equal(visibleCategoryIds(app).length, 25);
  app.click("next-page"); assert.equal(visibleCategoryIds(app).length, 6);
  const ids = ["coffee-a", "coffee-b", "coffee-trim", "coffee-case", ...extra.map((row) => row.id)];
  assert.deepEqual(exportedCategoryIds(app), ids);
  assert.equal(new Set(ids).size, 31);
  app.change("transaction-account", "account-a");
  assert.match(app.get("page-count").textContent, /Page 1 of 2/);
  assert.deepEqual(exportedCategoryIds(app), ["coffee-a", "coffee-trim", ...extra.map((row) => row.id)]);
});

test("date refresh retains unavailable selections and reset changes only table filters", async (t) => {
  let result = categoryPayload();
  const app = boot(t, () => response(result)); await tick();
  categoryFilters(app, { category: "dining", subcategory: "coffee", account: "account-b", kind: "purchase" });
  const rent = result.transactions.find((row) => row.id === "rent-a");
  result = { ...result, accounts: [result.accounts[0]], transactions: [rent], category_mapping: { version: "source-categories-v1", entries: [result.category_mapping.entries.find((row) => row.raw_category === "rent")] } };
  result.summary = { ...result.summary, net_spending_cents: 150000, included_count: 1, excluded_count: 0, categories: [{ category: "rent", amount_cents: 150000 }] };
  result.gross_spending_cents = 150000; result.refunds_cents = 0;
  result.expense_categories = [{ category: "rent", amount_cents: 150000 }]; result.monthly_spending = [{ month: "2026-11", net_spending_cents: 150000 }];
  app.change("date-button", "month"); await tick();
  for (const [id, value] of [["transaction-category", "dining"], ["transaction-subcategory", "coffee"], ["transaction-account", "account-b"], ["transaction-kind", "purchase"]]) {
    assert.equal(app.get(id).value, value);
    assert.match(app.get(id).selectedOptions[0].textContent, /not in this period/);
  }
  assert.deepEqual(visibleCategoryIds(app), []); assert.equal(app.get("export-transactions").disabled, true);
  const callCount = app.calls.length, dateValue = app.get("date-button").value;
  app.click("transaction-reset-filters");
  assert.equal(app.get("date-button").value, dateValue); assert.equal(app.calls.length, callCount);
  assert.deepEqual(visibleCategoryIds(app), ["rent-a"]);
  assert.equal(app.document.querySelector('[data-filter="all"]').getAttribute("aria-pressed"), "true");
});

test("lock clears category metadata, private filter options and entered amount/search text", async (t) => {
  const app = boot(t, () => response(categoryPayload())); await tick();
  categoryFilters(app, { category: "dining", subcategory: "coffee", account: "account-a", kind: "purchase", min: "4.50", max: "5.00", search: "coffee" });
  app.click("lock-button");
  for (const id of ["transaction-category", "transaction-subcategory", "transaction-account", "transaction-kind", "transaction-min-amount", "transaction-max-amount", "transaction-search"]) assert.equal(app.get(id).value, "");
  assert.doesNotMatch(app.get("transaction-account").textContent, /account-a|account-b/);
  assert.equal(app.get("transaction-rows").textContent, "");
});

test("invalid or incomplete category metadata fails closed rather than inventing labels", async (t) => {
  let result = categoryPayload();
  const app = boot(t, () => response(result)); await tick();
  const malformed = [
    (r) => { delete r.category_mapping; },
    (r) => { r.category_mapping.version = "future-version"; },
    (r) => { r.category_mapping.entries.push({ ...r.category_mapping.entries[0] }); },
    (r) => { r.category_mapping.entries = r.category_mapping.entries.filter((row) => row.raw_category !== "COFFEE"); },
    (r) => { r.category_mapping.entries[0].subcategory_label = null; },
    (r) => { r.category_mapping.entries[0].origin = "guessed"; },
    (r) => { r.category_mapping.entries[0].category_label = "Conflicting label"; },
    (r) => { r.category_mapping.entries[0].category_id = "housing"; r.category_mapping.entries[0].category_label = "Housing"; },
    (r) => { r.category_mapping.entries.find((row) => row.origin === "unclassified").origin = "source_mapping"; },
  ];
  for (const mutate of malformed) {
    result = categoryPayload(); mutate(result); app.click("refresh-button"); await tick();
    assert.equal(app.get("dashboard-data").classList.contains("hidden"), true);
    assert.equal(app.get("status-banner").classList.contains("error"), true);
    assert.equal(app.get("transaction-rows").textContent, "");
    assert.equal(app.get("transaction-category").value, "");
  }
});

test("hostile raw category strings remain text and formula-neutralized CSV cells", async (t) => {
  const result = categoryPayload();
  const hostile = ['<img src=x onerror="alert(1)">', "=CATEGORY()"];
  result.transactions = hostile.map((category, index) => ({ ...result.transactions[0], id: "hostile-" + index, description: "hostile-" + index, category }));
  result.category_mapping.entries = hostile.map((raw_category) => ({ raw_category, category_id: "unclassified", category_label: "Unclassified", subcategory_id: null, subcategory_label: null, origin: "unclassified" }));
  result.gross_spending_cents = 900; result.refunds_cents = 0;
  result.summary = { ...result.summary, net_spending_cents: 900, included_count: 2, excluded_count: 0, categories: hostile.map((category) => ({ category, amount_cents: 450 })) };
  result.expense_categories = result.summary.categories.map((row) => ({ ...row }));
  result.monthly_spending = [{ month: "2026-11", net_spending_cents: 900 }];
  const app = boot(t, () => response(result)); await tick();
  categoryFilters(app, { category: "unclassified" });
  assert.equal(app.get("transaction-rows").querySelector("img"), null);
  assert.match(app.get("transaction-rows").textContent, /<img src=x/);
  app.click("export-transactions");
  const rows = parseCsv(app.blobs.at(-1).parts.join(""));
  assert.equal(rows[1][5], hostile[0]); assert.equal(rows[2][5], "'=CATEGORY()");
  assert.deepEqual(rows[1].slice(10), ["Unclassified", "", "unclassified", "source-categories-v1"]);
});

test("money displays exact cents across safe integer boundaries and sub-dollar signs", async (t) => {
  const cases = [
    [29, "$0.29", "$0.29"], [-29, "-$0.29", "$0.29"],
    [9007199254740991, "$90,071,992,547,409.91", "$90,071,992,547,409.91"],
    [-9007199254740991, "-$90,071,992,547,409.91", "$90,071,992,547,409.91"],
    [9007199254740989, "$90,071,992,547,409.89", "$90,071,992,547,409.89"],
    [1, "$0.01", "$0.01"], [-1, "-$0.01", "$0.01"], [0, "$0.00", "$0.00"],
  ];
  let amount = cases[0][0];
  const app = boot(t, () => {
    const result = payload();
    result.transactions = [{ ...result.transactions[0], amount_cents: Math.abs(amount), kind: amount < 0 ? "refund" : "purchase" }];
    result.summary = { ...result.summary, net_spending_cents: amount, included_count: 1, excluded_count: 0, categories: [{ category: "groceries", amount_cents: amount }] };
    result.gross_spending_cents = Math.max(0, amount); result.refunds_cents = Math.max(0, -amount);
    result.expense_categories = amount > 0 ? [{ category: "groceries", amount_cents: amount }] : [];
    result.monthly_spending = [{ month: "2026-11", net_spending_cents: amount }];
    result.recurring = [];
    return response(result);
  });
  await tick();
  for (const [cents, signed, unsigned] of cases) {
    amount = cents; app.click("refresh-button"); await tick();
    assert.equal(app.get("spend-value").textContent, signed, "net cents=" + cents);
    assert.equal(app.get("report-total").textContent, signed, "report cents=" + cents);
    assert.equal(app.get("transaction-rows").querySelector("td.amount").textContent, unsigned, "face amount cents=" + cents);
  }
});

test("comparison displays exact basis points for accepted safe-integer boundary payloads", async (t) => {
  const cases = [
    [9007199254740990, "90071992547309.90%"],
    [-9007199254730990, "-90071992547409.90%"],
    [10000, "0.00%"], [10001, "0.01%"], [9999, "-0.01%"],
  ];
  let current = cases[0][0];
  const app = boot(t, (url) => {
    if (url !== "/api/v1/spending/compare") return response(payload());
    const value = comparisonResponse();
    const total = { baseline_cents: 10000, current_cents: current, delta_cents: current - 10000, percent_change_bps: current - 10000, percent_status: "defined" };
    value.total = total;
    for (const [period, amount] of [["baseline", 10000], ["current", current]]) {
      Object.assign(value[period], { gross_spending_cents: Math.max(0, amount), refunds_cents: Math.max(0, -amount), net_spending_cents: amount, observed_count: 1, included_count: 1, excluded_count: 0 });
    }
    for (const key of ["categories", "merchants"]) Object.assign(value[key].rows[0], total);
    value.transaction_evidence = [["baseline", 10000], ["current", current]].map(([period, amount]) => ({ id: period + "-synthetic", period, occurred_at: value[period].start + "T12:00:00Z", category_key: value.categories.rows[0].key, merchant_key: value.merchants.rows[0].key, signed_spending_cents: amount }));
    value.evidence_summary = { eligible_count: 2, returned_count: 2, omitted_count: 0, baseline_returned: 1, current_returned: 1, truncated: false };
    value.findings = [];
    return response(value);
  });
  await tick(); fillComparison(app);
  for (const [amount, expected] of cases) {
    current = amount;
    app.submit("comparison-form"); await tick();
    assert.equal(app.get("comparison-result").classList.contains("hidden"), false, "complete coherent comparison is accepted");
    assert.equal(app.get("comparison-error").textContent, "");
    assert.equal(app.get("comparison-totals").querySelector("p.chart-note").textContent, "Total change: " + expected + ".");
  }
});

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
      Object.assign(result, { gross_spending_cents: 0, refunds_cents: 0, transactions: [], expense_categories: [], monthly_spending: [], recurring: [], category_mapping: { version: "source-categories-v1", entries: [] } });
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
    costar_enabled: false, max_model_turns: 4, max_tool_calls: 6,
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
  assert.doesNotMatch(app.get("ask-question").value, /verified floor-plan model references/);
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
    assert.equal(app.get("ask-answer-title").textContent, { answered: "Answer", limited: "What was completed", unavailable: "Unable to answer" }[status]);
    assert.match(app.get("ask-state").textContent, { answered: /^Answer ready\./, limited: /^Request limit reached\./, unavailable: /^Answer unavailable\./ }[status]);
    assert.equal(app.get("ask-state").getAttribute("role"), "status");
    assert.equal(app.get("ask-state").getAttribute("aria-live"), "polite");
  });
}

test("Ask rejects a claimed answer with no successful tool evidence", async (t) => {
  let next = agentAnswer({ evidence: [] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(next) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app);
  for (const evidence of [[], [{ tool: "costar_search", arguments: {}, result: { status: "unavailable" } }]]) {
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

test("housing evidence preserves full rent ranges, units, unknowns, and text safety", async (t) => {
  const malicious = '<img src=x onerror="alert(1)"> [external](https://unrelated.invalid)';
  const body = agentAnswer({ answer: malicious, model_calls: 3, tool_calls: 2, evidence: [
    { tool: "costar_search", arguments: {}, result: { status: "ok", results: [
      { property_name: malicious, floor_plan_name: "A1", asking_rent: { minimum: 1200, maximum: 1900, currency: "USD", period: "month", basis: "per_unit" }, price_range_within_budget: false },
      { property_name: "Upper unknown", floor_plan_name: "B1", asking_rent: { minimum: 1400, maximum: null, currency: "USD", period: "month", basis: "per_unit" }, price_range_within_budget: false },
    ], limitations: ["Recorded availability, not a live quote."] } },
  ] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  const evidence = app.get("ask-evidence");
  assert.equal(app.get("ask-answer").textContent, malicious);
  assert.match(evidence.textContent, /\$1,200\.00 – \$1,900\.00 per month, per unit/);
  assert.match(evidence.textContent, /\$1,400\.00 – upper bound unknown/);
  assert.match(evidence.textContent, /full range is not confirmed/);
  assert.equal(app.get("ask-result").querySelector("img, a, script, iframe"), null);
  assert.equal(evidence.querySelectorAll("details").length, 1);
});

function selectionCriteria(overrides = {}) {
  return { state: "TX", city: null, bedrooms: 1, max_monthly_rent_usd: 1800,
    min_square_feet: 0, limit: 2,
    include_specialized_housing: false, ...overrides };
}
function shortlistAnswer() {
  const row = { property_id: 20, floor_plan_id: 30, property_name: "Synthetic Place",
    floor_plan_name: "Study plan", asking_rent: { minimum: 1200, maximum: 1700, currency: "USD", period: "month", basis: "per_unit" },
    price_range_within_budget: true };
  return agentAnswer({ answer: "Two recorded floor plans; availability needs confirmation.",
    evidence: [{ tool: "costar_search", arguments: { limit: 2 }, result: {
      status: "ok", source_kind: "costar_local_snapshot", source_hash: "a".repeat(64),
      criteria: selectionCriteria(), results: [row, { ...structuredClone(row), property_id: 21,
        floor_plan_id: 31, floor_plan_name: "Second plan" }], limitations: ["Recorded availability only."] } }] });
}
async function showShortlist(app) {
  await tick(); await openAsk(app); typeQuestion(app, "Find an apartment in TX under $1800.");
  app.submit("ask-form"); await tick();
}
function housingRoute(url) {
  if (url === "/api/v1/agent/status") return response(agentStatus({ costar_enabled: true }));
  if (url === "/api/v1/agent/answer") return response(shortlistAnswer());
  return askRoute(url);
}

test("applied catalog filters display returned values even when they differ from the question", async (t) => {
  const body = shortlistAnswer();
  body.answer = "Here are apartments under your $1000 budget.";
  body.evidence[0].arguments = { max_monthly_rent_usd: 1000 };
  body.evidence[0].result.criteria = selectionCriteria({ city: "Austin", min_square_feet: 650 });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : housingRoute(url));
  await tick(); await openAsk(app); typeQuestion(app, "Find apartments under $1000.");
  app.submit("ask-form"); await tick();
  assert.match(app.get("ask-asked-question").textContent, /under \$1000/);
  const summary = app.get("ask-evidence").querySelector(".ask-applied-criteria");
  assert.match(summary.textContent, /TX, Austin; 1 bedroom/);
  assert.match(summary.textContent, /advertised starting rent at most \$1,800 per month, per unit/);
  assert.match(summary.textContent, /at least 650 square feet; up to 2 results/);
  assert.match(summary.textContent, /specialized housing excluded/);
  assert.match(summary.textContent, /Compare these applied filters with your question/);
  assert.doesNotMatch(summary.textContent, /\$1,?000/);
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
  if (url === "/api/v1/agent/status") return response(agentStatus({ enabled: false, costar_enabled: true }));
  if (url === "/api/v1/costar/search") return response(catalogResponse(JSON.parse(options.body)));
  return housingRoute(url);
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
  app.change("catalog-state", "CA"); app.change("catalog-city", "  Sacramento  ");
  app.change("catalog-bedrooms", "0"); app.change("catalog-rent", "1000"); app.change("catalog-area", "650");
  assert.equal(app.calls.length, before + 1, "editing filters never searches");
  app.submit("catalog-form"); await tick();
  const request = app.calls.at(-1);
  const expected = directCriteria({ state: "CA", city: "Sacramento", bedrooms: 0, max_monthly_rent_usd: 1000, min_square_feet: 650 });
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
  assert.deepEqual(JSON.parse(app.calls.at(-1).options.body), directCriteria({ max_monthly_rent_usd: 100000, min_square_feet: 10000, bedrooms: 6 }));
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
    (value) => { value.criteria.include_specialized_housing = true; },
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

test("direct catalog keeps full and unknown rent ranges explicit without AI", async (t) => {
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
  assert.equal(app.calls.some((call) => call.url === "/api/v1/agent/answer"), false);
});

test("direct catalog request blocks other reads and cancellation rejects an eventual response", async (t) => {
  let finish;
  const app = boot(t, (url, options) => {
    if (url === "/api/v1/costar/search") return new Promise((resolve) => { finish = resolve; });
    return housingRoute(url, options);
  });
  await showShortlist(app);
  app.submit("catalog-form");
  const request = app.calls.at(-1); const count = app.calls.length;
  assert.equal(app.get("catalog-submit").disabled, true);
  assert.equal(app.get("ask-submit").disabled, true);
  assert.equal(app.get("ask-check").disabled, true);
  assert.equal(app.get("catalog-rent").disabled, true);
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
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
  });
}

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
  const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? response({}, 401) : housingRoute(url, options));
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

test("clarification is a valid question for inputs without invented evidence", async (t) => {
  const body = agentAnswer({ status: "clarification", answer: "Which two periods should I compare?", needed_inputs: ["Baseline start and end dates", "Comparison start and end dates"], evidence: [], activity: [{ role: "advisor", tool: "request_clarification", status: "ok" }], output_tokens_reserved: 1024 });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app, "Compare my spending"); app.submit("ask-form"); await tick();
  assert.equal(app.get("ask-outcome").textContent, "More information needed");
  assert.equal(app.get("ask-needed-inputs").children.length, 2);
  assert.match(app.get("ask-evidence").textContent, /No tool evidence/);
  assert.match(app.get("ask-usage").textContent, /1024 output tokens reserved \(not billed usage\)/);
  assert.match(app.get("ask-state").textContent, /More information is needed/);
  assert.equal(app.calls.filter((call) => call.url === "/api/v1/agent/answer").length, 1);
  app.click("refresh-button"); await tick();
  assert.equal(app.get("ask-needed-inputs").textContent, "");
  assert.equal(app.get("ask-activity").textContent, "");
});

test("agent activity is text-only and cannot replace evidence for an answer", async (t) => {
  let body = agentAnswer({ activity: [{ role: "advisor", tool: "task", status: '<img src=x onerror="alert(1)">' }] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  assert.match(app.get("ask-activity").textContent, /<img/);
  assert.equal(app.get("ask-activity").querySelector("img"), null);
  body = { ...body, evidence: [] };
  app.submit("ask-form"); await tick();
  assert.match(app.get("ask-error").textContent, /valid answer could not be received/);
  assert.equal(app.get("ask-result").classList.contains("hidden"), true);
});

test("clarification rejects missing or malformed requested inputs", async (t) => {
  let inputs;
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(agentAnswer({ status: "clarification", evidence: [], needed_inputs: inputs })) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app);
  for (inputs of [undefined, [], "dates", [1], ["x".repeat(201)]]) {
    app.submit("ask-form"); await tick();
    assert.equal(app.get("ask-result").classList.contains("hidden"), true);
  }
});

test("catalog completion announces counts while removed spatial controls stay absent", async (t) => {
  const app = boot(t, catalogRoute);
  await showCatalog(app);
  assert.match(app.get("catalog-state-message").textContent, /^Catalog search complete\. 2 recorded matches returned/);
  assert.equal(app.get("catalog-state-message").getAttribute("aria-live"), "polite");
  assert.equal(app.document.querySelector("#catalog-model, #inspection-result, .inspect-floor-plan"), null);
  assert.equal(app.calls.some((call) => call.url.includes("inspect")), false);
});

test("cancelled catalog search restores focus and ignores late results", async (t) => {
  let finish;
  const app = boot(t, (url, options) => url === "/api/v1/costar/search" ? new Promise((resolve) => { finish = resolve; }) : catalogRoute(url, options));
  await tick(); await openAsk(app); app.submit("catalog-form");
  app.get("catalog-cancel").focus(); app.click("catalog-cancel");
  assert.equal(app.document.activeElement, app.get("catalog-submit"));
  finish(response(catalogResponse())); await tick();
  assert.match(app.get("catalog-state-message").textContent, /Stopped waiting/);
  assert.equal(app.get("catalog-result").classList.contains("hidden"), true);
});

test("navigation scopes period controls and focuses headings without loading a new period", async (t) => {
  const app = boot(t, askRoute);
  await tick();
  const toolbar = app.document.querySelector(".range-toolbar");
  for (const view of ["transactions", "insights", "reports", "ask", "settings", "overview"]) {
    app.document.querySelector('[data-view="' + view + '"]').click();
    assert.equal(toolbar.classList.contains("hidden"), view === "ask" || view === "settings");
    assert.equal(app.document.activeElement, app.get(view + "-view").querySelector("h1"));
    await tick();
  }
  assert.equal(app.calls.filter((call) => call.url.startsWith("/api/v1/dashboard")).length, 1);
  app.document.querySelector('[data-view-link="reports"]').click();
  assert.equal(app.document.activeElement, app.get("reports-view").querySelector("h1"));
});

test("transaction status controls are ordinary pressed filter buttons", async (t) => {
  const app = boot(t);
  await tick();
  app.document.querySelector('[data-view="transactions"]').click();
  const pending = app.document.querySelector('[data-filter="pending"]');
  assert.equal(pending.parentElement.getAttribute("role"), "group");
  assert.equal(pending.getAttribute("role"), null);
  pending.focus(); pending.click();
  assert.equal(app.document.activeElement, pending);
  assert.equal(pending.getAttribute("aria-pressed"), "true");
  assert.equal(app.document.querySelector('[data-filter="all"]').getAttribute("aria-pressed"), "false");
  assert.equal(app.document.querySelector('[aria-selected], [role="tablist"]'), null);
  assert.equal(app.get("transaction-rows").children.length, 1);
});

test("clarification labels are readable and editing reuses the complete original question without submitting", async (t) => {
  const body = agentAnswer({ status: "clarification", evidence: [], answer: "Please add the missing details.", needed_inputs: ["date_range", "monthly_rent_budget", "minimum_square_feet"] });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app);
  const original = "Summarize my spending and find one-bedroom apartments in Austin, TX.";
  typeQuestion(app, original); app.submit("ask-form"); await tick();
  assert.deepEqual(Array.from(app.get("ask-needed-inputs").children, (item) => item.textContent), ["Start and end dates (UTC)", "Monthly rent budget (USD)", "Minimum area (square feet)"]);
  assert.equal(app.get("ask-clarification").classList.contains("hidden"), false);
  assert.match(app.get("ask-question").getAttribute("aria-describedby"), /ask-needed-inputs/);
  typeQuestion(app, "A different draft");
  const before = app.calls.length;
  app.click("ask-edit-question");
  assert.equal(app.get("ask-question").value, original);
  assert.equal(app.document.activeElement, app.get("ask-question"));
  assert.equal(app.calls.length, before);
  assert.match(app.get("ask-state").textContent, /Each question starts fresh/);
  typeQuestion(app, original + " Use 2026-11-01 through 2026-11-14 and a $1,800 monthly rent budget.");
  app.submit("ask-form"); await tick();
  assert.deepEqual(JSON.parse(app.calls.at(-1).options.body), { question: app.get("ask-question").value });
});

for (const phase of ["response", "json"]) {
  test("stopping the answer wait during " + phase + " preserves the question and rejects late results without retry", async (t) => {
    let finish;
    let requests = 0;
    const app = boot(t, (url) => {
      if (url !== "/api/v1/agent/answer") return askRoute(url);
      requests += 1;
      if (requests > 1) return response(agentAnswer({ answer: "Fresh explicit answer" }));
      if (phase === "response") return new Promise((resolve) => { finish = resolve; });
      return { ok: true, status: 200, json: () => new Promise((resolve) => { finish = resolve; }) };
    });
    await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
    const question = app.get("ask-question").value;
    const request = app.calls.at(-1);
    assert.equal(app.get("ask-cancel").classList.contains("hidden"), false);
    assert.equal(app.get("ask-cancel").disabled, false);
    app.get("ask-cancel").focus(); app.click("ask-cancel");
    assert.equal(request.options.signal.aborted, true);
    assert.equal(app.get("ask-form").getAttribute("aria-busy"), "false");
    assert.equal(app.get("ask-cancel").classList.contains("hidden"), true);
    assert.equal(app.get("ask-cancel").disabled, true);
    assert.equal(app.get("ask-question").value, question);
    assert.equal(app.document.activeElement, app.get("ask-question"));
    assert.equal(requests, 1);
    assert.match(app.get("ask-state").textContent, /server may still finish.*no retry was sent/);
    assert.equal(app.get("ask-result").classList.contains("hidden"), true);
    typeQuestion(app, "A new explicit question"); app.submit("ask-form"); await tick();
    assert.equal(app.get("ask-answer").textContent, "Fresh explicit answer");
    if (phase === "response") finish(response({}, 401));
    else finish(agentAnswer({ answer: "STALE CANCELLED ANSWER" }));
    await tick();
    assert.equal(requests, 2);
    assert.equal(app.get("ask-answer").textContent, "Fresh explicit answer");
    assert.equal(app.get("access-panel").classList.contains("hidden"), true);
    assert.doesNotMatch(app.document.body.textContent, /STALE CANCELLED ANSWER/);
  });
}

test("asynchronous availability and answer completion do not steal navigation focus", async (t) => {
  let finishStatus, finishAnswer;
  const app = boot(t, (url) => {
    if (url === "/api/v1/agent/status") return new Promise((resolve) => { finishStatus = resolve; });
    if (url === "/api/v1/agent/answer") return new Promise((resolve) => { finishAnswer = resolve; });
    return askRoute(url);
  });
  await tick(); await openAsk(app);
  app.document.querySelector('[data-view="settings"]').click();
  const settingsHeading = app.get("settings-view").querySelector("h1");
  finishStatus(response(agentStatus())); await tick();
  assert.equal(app.document.activeElement, settingsHeading);
  await openAsk(app); typeQuestion(app); app.submit("ask-form");
  app.document.querySelector('[data-view="transactions"]').click();
  const transactionsHeading = app.get("transactions-view").querySelector("h1");
  finishAnswer(response(agentAnswer())); await tick();
  assert.equal(app.document.activeElement, transactionsHeading);
  assert.equal(app.get("ask-view").classList.contains("hidden"), true);
});

test("disabled catalog stays compact and enabled catalog works independently of AI", async (t) => {
  let status = agentStatus({ enabled: false });
  const app = boot(t, (url) => url === "/api/v1/agent/status" ? response(status) : askRoute(url));
  await tick(); await openAsk(app);
  assert.equal(app.get("catalog-controls").classList.contains("hidden"), true);
  assert.equal(app.get("catalog-submit").disabled, true);
  assert.match(app.get("catalog-availability").textContent, /disabled/);
  status = agentStatus({ enabled: false, costar_enabled: true });
  app.click("ask-check"); await tick();
  assert.equal(app.get("catalog-controls").classList.contains("hidden"), false);
  assert.equal(app.get("catalog-submit").disabled, false);
  assert.equal(app.get("ask-submit").disabled, true);
});

test("answer diagnostics are collapsed after evidence while source context remains visible", async (t) => {
  const body = agentAnswer({ output_tokens_reserved: 1536 });
  const app = boot(t, (url) => url === "/api/v1/agent/answer" ? response(body) : askRoute(url));
  await tick(); await openAsk(app); typeQuestion(app); app.submit("ask-form"); await tick();
  const details = app.get("ask-technical-details");
  assert.equal(details.open, false);
  assert.equal(app.get("ask-usage").closest("details"), details);
  assert.equal(app.get("ask-revision").closest("details"), details);
  assert.ok(app.get("ask-evidence").compareDocumentPosition(details) & app.window.Node.DOCUMENT_POSITION_FOLLOWING);
  assert.match(app.get("ask-source").textContent, /Synthetic fixture/);
  assert.doesNotMatch(app.get("ask-source").textContent, /synthetic-model|11111111|tokens/);
  assert.match(app.get("ask-usage").textContent, /1536 output tokens reserved \(not billed usage\)/);
  assert.match(app.get("ask-revision").textContent, /11111111/);
  assert.doesNotMatch(app.get("ask-provider").textContent, /synthetic-model/);
});
