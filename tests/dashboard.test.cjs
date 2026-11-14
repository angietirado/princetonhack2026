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
  const app = boot(t);
  await tick();
  app.document.querySelector('[data-view="insights"]').click();
  assert.equal(app.get("insights-view").classList.contains("hidden"), false);
  assert.equal(app.document.querySelector('[data-view="insights"]').getAttribute("aria-current"), "page");
  app.document.querySelector('[data-view="ask"]').click();
  assert.match(app.get("ask-view").textContent, /coming next/);
  assert.equal(app.document.querySelector("#ask-submit"), null);
});
