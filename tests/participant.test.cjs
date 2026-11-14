const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const { JSDOM, VirtualConsole } = require("jsdom");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "apps/participant/index.html"), "utf8");
const source = fs.readFileSync(path.join(root, "apps/participant/app.js"), "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => clone(body) });
const tick = async () => { for (let i = 0; i < 8; i++) await new Promise(setImmediate); };
const constraints = { budget_cents: 2500, time_budget_minutes: 45, travel_mode: "drive", max_stores: 2 };
function scenario() {
  return { id: "houston-test", version: "fixture-v1", name: "Houston shopping challenge", description: "A fixed basket, two stores, and your limits.", home: { label: "Your Houston home", latitude: 29.8, longitude: -95.4, housing_context: "Historical home location. No income is inferred.", source: "costar" }, defaults: clone(constraints), basket: [{ id: "milk", name: "Milk", quantity: 1, unit: "gallon", category: "Dairy" }, { id: "bread", name: "Bread", quantity: 2, unit: "loaf", category: "Bakery" }], stores: [{ id: "corner", name: "Corner Market", latitude: 29.801, longitude: -95.401, shopping_minutes: 7, prices: { milk: 500, bread: 300 } }, { id: "value", name: "Value Market", latitude: 29.81, longitude: -95.41, shopping_minutes: 12, prices: { milk: 400, bread: 250 } }, { id: "third", name: "Third Market", latitude: 29.79, longitude: -95.39, shopping_minutes: 10, prices: { milk: 600, bread: 400 } }], provenance: { prices: "Simulated prices.", travel: "Modeled travel, not directions." } };
}
function evaluation(assignments = { milk: "corner", bread: "corner" }, limits = constraints) {
  const s = scenario();
  const stores = [...new Set(Object.values(assignments))];
  const basket = s.basket.reduce((sum, item) => sum + item.quantity * s.stores.find((store) => store.id === assignments[item.id]).prices[item.id], 0);
  const transport = stores.includes("value") ? 200 : 100;
  const duration = stores.includes("value") ? 30 : 20;
  const violations = [];
  if (basket + transport > limits.budget_cents) violations.push("Over budget.");
  if (duration > limits.time_budget_minutes) violations.push("Over time.");
  if (stores.length > limits.max_stores) violations.push("Too many stores.");
  return { basket_cost_cents: basket, transport_cost_cents: transport, total_cost_cents: basket + transport, travel_minutes: duration - 10, shopping_minutes: 10, total_minutes: duration, feasible: !violations.length, violations, route: ["home", ...stores, "home"], store_ids: stores, assignments: clone(assignments), constraints: clone(limits) };
}
function boot(t, options = {}) {
  const calls = [], choices = [], errors = [], chatHistory = clone(options.chatHistory || []);
  const scenarios = clone(options.scenarios || [scenario()]);
  const wallets = clone(options.wallets || walletProfiles());
  const investigationReplies = new Map();
  const chatReplies = new Map(); let chatConstraints = clone(constraints);
  let sequence = 0;
  const serverSession = { id: "session-1", token: "scoped-session-token", scenario: clone(options.snapshot || scenarios[0]), constraints: clone(constraints), is_demo: false, created_at: "2026-11-14T12:00:00Z", choices };
  function sessionEvaluation(assignments, limits) {
    const result = evaluation(assignments, limits), context = serverSession.scenario.financial_context;
    if (!context) return result;
    const summary = clone(context.projection), left = summary.spendable_now_cents - result.total_cost_cents;
    result.wallet = { summary, after_choice: { ...summary, local_spending_cents: result.total_cost_cents, spendable_now_cents: Math.max(0, left), ending_available_cents: left, shortfall_cents: Math.max(0, -left) } };
    if (left < 0) { result.violations.push("The trip exceeds the wallet cash cap."); result.feasible = false; }
    return result;
  }
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => errors.push(e));
  const dom = new JSDOM(html, { url: options.url || "http://localhost:5183/", runScripts: "outside-only", virtualConsole });
  const window = dom.window;
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.confirm = () => true;
  if (options.stored) window.localStorage.setItem("blockwise.participant.v1", JSON.stringify(options.stored));
  if (options.initial) choices.push(clone(options.initial));
  Object.entries(options.sessionStorage || {}).forEach(([key, value]) => window.sessionStorage.setItem(key, value));
  const defaultHandler = async (url, request) => {
    const body = request.body ? JSON.parse(request.body) : null;
    if (url === "/api/v1/wallets") return response({ wallets, notice: "Fictional local fixtures." });
    if (url === "/api/v1/scenarios") return response({ scenarios, simulation_notice: "Simulation." });
    if (url === "/api/v1/sessions") { serverSession.scenario = clone(scenarios.find((item) => item.id === body.scenario_id) || serverSession.scenario); if (body.wallet_profile_id) serverSession.scenario.financial_context = walletContext(wallets.find((item) => item.id === body.wallet_profile_id)); serverSession.constraints = body.constraints; serverSession.is_demo = body.is_demo; return response(serverSession); }
    if (request.headers.Authorization !== "Bearer scoped-session-token") return response({ detail: "Not authorized" }, 401);
    if (url === "/api/v1/sessions/session-1") return response(serverSession);
    if (url.endsWith("/investigate")) {
      if (!investigationReplies.has(body.request_id)) {
        const report = options.investigation ? options.investigation(body, choices.at(-1)?.id, serverSession.scenario.financial_context) : investigationReport(body, choices.at(-1)?.id);
        investigationReplies.set(body.request_id, clone(report));
      }
      return response(investigationReplies.get(body.request_id));
    }
    if (url.endsWith("/chat")) {
      if (request.method === "GET") return response({ messages: chatHistory, constraints: chatConstraints });
      if (chatReplies.has(body.request_id)) return response(chatReplies.get(body.request_id));
      let status = "ok";
      if (body.message === "one store only") chatConstraints.max_stores = 1;
      else if (body.message === "walking") chatConstraints.travel_mode = "walk";
      else if (body.message === "under 30 minutes") chatConstraints.time_budget_minutes = 30;
      else if (body.message === "budget $60") chatConstraints.budget_cents = 6000;
      else status = "clarification";
      const plan = sessionEvaluation({ milk: "value", bread: "value" }, chatConstraints);
      const reply = status === "clarification" ? "Please give a specific budget, time, travel, or stop limit." : "Here are your options with those limits. Apply a proposal when ready.";
      const result = { reply, mode: "offline", constraints: clone(chatConstraints), plans: status === "ok" && plan.feasible ? [{ ...plan, id: "chat-plan", label: "Lowest cost" }] : [], status, notice: "Suggestions do not save choices." };
      chatHistory.push({ role: "user", content: body.message }, { role: "assistant", content: reply });
      chatReplies.set(body.request_id, clone(result));
      return response(result);
    }
    if (url.endsWith("/evaluate")) return response(sessionEvaluation(body.assignments, body.constraints));
    if (url.endsWith("/choices")) {
      const existing = choices.find((c) => c.request_id === body.request_id);
      if (existing) return response(existing);
      const choice = { id: "choice-" + ++sequence, request_id: body.request_id, stage: body.stage, evaluation: sessionEvaluation(body.assignments, body.constraints), created_at: "2026-11-14T12:00:00Z" };
      choices.push(choice); return response(choice);
    }
    if (url.endsWith("/recommendations")) {
      const plan = sessionEvaluation({ milk: "value", bread: "value" }, body.constraints);
      return response({ status: plan.feasible ? "ok" : "no_feasible_plan", plans: plan.feasible ? [{ ...plan, id: "plan-1", label: "Lowest cost" }] : [], evaluated_plan_count: 6, feasible_plan_count: plan.feasible ? 4 : 0, notice: "Simulated prices and travel." });
    }
    throw new Error("Unexpected URL: " + url);
  };
  window.fetch = async (url, request) => { calls.push({ url, request }); return options.handler ? options.handler(url, request, defaultHandler) : defaultHandler(url, request); };
  window.eval(source);
  t.after(() => { dom.window.close(); assert.deepEqual(errors, [], "No unhandled browser errors"); });
  const get = (id) => window.document.getElementById(id);
  const click = (id) => get(id).click();
  const submit = (id) => get(id).dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  return { window, calls, choices, serverSession, get, click, submit,
    async start() { await tick(); get("consent").checked = true; submit("start-form"); await tick(); },
    async selectStore(id = "corner") { window.document.querySelector('[data-store="' + id + '"]').click(); await tick(); }
  };
}

test("setup never starts collection without consent and exposes simulation provenance", async (t) => {
  const app = boot(t); await tick();
  assert.equal(app.get("setup").hidden, false);
  assert.equal(app.get("challenge").hidden, true);
  assert.deepEqual(app.calls.map((call) => call.url).sort(), ["/api/v1/scenarios", "/api/v1/wallets"]);
  app.submit("start-form"); await tick();
  assert.match(app.get("start-status").textContent, /agree to save/);
  assert.equal(app.calls.some((c) => c.url === "/api/v1/sessions"), false);
  assert.match(app.window.document.body.textContent, /Simulated stores, prices/);
  assert.equal(app.window.document.querySelectorAll(".scenario-card").length, 1);
});

test("complete participant flow saves initial choice before alternatives and revised choice uses canonical totals", async (t) => {
  const app = boot(t); await app.start();
  const create = app.calls.find((c) => c.url === "/api/v1/sessions");
  assert.equal(JSON.parse(create.request.body).is_demo, false);
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.get("save-choice").disabled, true);
  await app.selectStore();
  assert.equal(app.get("total-cost").textContent, "$12.00");
  assert.equal(app.get("total-time").textContent, "20 min");
  assert.equal(app.get("save-choice").disabled, false);
  assert.equal(app.calls.some((c) => c.url.endsWith("/recommendations")), false);
  app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 1);
  assert.equal(app.choices[0].stage, "initial");
  assert.equal(app.get("recommendations-section").hidden, false);
  assert.equal(app.window.document.querySelectorAll(".recommendation-card").length, 1);
  app.window.document.querySelector(".recommendation-card button").click(); await tick();
  assert.equal(app.choices.length, 1, "Trying an option does not persist a choice");
  assert.equal(app.get("total-cost").textContent, "$11.00");
  app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 2);
  assert.equal(app.choices[1].stage, "revised");
  assert.match(app.get("saved-summary").textContent, /\$1\.00 less/);
  assert.match(app.get("saved-summary").textContent, /10 min more time/);
  assert.equal(app.get("saved-result").hidden, false);
  assert.equal(app.get("save-choice").disabled, true);
  for (const call of app.calls.filter((c) => c.url.includes("/sessions/session-1"))) {
    assert.equal(call.request.headers.Authorization, "Bearer scoped-session-token");
    assert.equal(call.request.cache, "no-store");
  }
  assert.equal(app.calls.some((c) => /^https?:/.test(c.url)), false);
});

test("budget changes retain infeasible observations but never fabricate a feasible recommendation", async (t) => {
  const app = boot(t); await app.start(); await app.selectStore();
  app.get("active-budget").value = "1.00"; app.submit("limits-form"); await tick();
  assert.match(app.get("plan-state").textContent, /Outside/);
  assert.match(app.get("evaluation-error").textContent, /Over budget/);
  assert.equal(app.get("save-choice").disabled, false);
  app.click("save-choice"); await tick();
  assert.equal(app.choices[0].evaluation.feasible, false);
  assert.match(app.get("recommendations-status").textContent, /No complete shopping plan/);
  assert.equal(app.window.document.querySelectorAll(".recommendation-card").length, 0);
  assert.equal(app.choices[0].evaluation.constraints.budget_cents, 100);
});

test("lost save responses reuse request id and do not duplicate a committed choice", async (t) => {
  let lost = true;
  const app = boot(t, { handler: async (url, request, next) => {
    const result = await next(url, request);
    if (url.endsWith("/choices") && lost) { lost = false; throw new Error("Connection interrupted after submission"); }
    return result;
  } });
  await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 1);
  assert.match(app.get("save-status").textContent, /Connection interrupted/);
  app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 1);
  const requests = app.calls.filter((c) => c.url.endsWith("/choices")).map((c) => JSON.parse(c.request.body));
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(app.get("recommendations-section").hidden, false);
});

test("a slower evaluation cannot replace the participant's newer basket", async (t) => {
  let release, delayed = true;
  const app = boot(t, { handler: async (url, request, next) => {
    if (url.endsWith("/evaluate") && delayed) { delayed = false; return new Promise((resolve) => { release = async () => resolve(await next(url, request)); }); }
    return next(url, request);
  } });
  await app.start(); await app.selectStore("corner");
  assert.equal(app.get("save-choice").disabled, true);
  await app.selectStore("value");
  assert.equal(app.get("total-cost").textContent, "$11.00");
  await release(); await tick();
  assert.equal(app.get("total-cost").textContent, "$11.00");
  assert.equal(app.get("item-0").value, "value");
});

test("demo links label and segregate sessions while default links stay participant mode", async (t) => {
  const app = boot(t, { url: "http://localhost:5183/?demo=1" }); await app.start();
  assert.match(app.get("demo-notice").textContent, /Demo mode/);
  assert.equal(JSON.parse(app.calls.find((c) => c.url === "/api/v1/sessions").request.body).is_demo, true);
  assert.match(app.get("scenario-eyebrow").textContent, /DEMO/);
});

test("resumption preserves saved mode and warns when the URL requests another mode", async (t) => {
  const app = boot(t, { url: "http://localhost:5183/?demo=1", stored: { id: "session-1", token: "scoped-session-token", assignments: { milk: "corner", bread: "corner" }, constraints } });
  await tick();
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.calls.some((c) => c.url === "/api/v1/sessions"), false);
  assert.match(app.get("session-notice").textContent, /saved session is in participant mode/);
  assert.doesNotMatch(app.get("scenario-eyebrow").textContent, /DEMO/);
});

test("expired browser session is cleared without exposing tokens or blocking a fresh start", async (t) => {
  const app = boot(t, { stored: { id: "session-1", token: "expired-token" } }); await tick();
  assert.equal(app.get("setup").hidden, false);
  assert.match(app.get("global-error").textContent, /previous session is no longer available/);
  assert.equal(app.window.localStorage.getItem("blockwise.participant.v1"), null);
  assert.doesNotMatch(app.window.document.body.textContent, /expired-token/);
});

test("changed limits are explicitly excluded from like-for-like savings language", async (t) => {
  const app = boot(t); await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  app.get("active-time").value = "60"; app.submit("limits-form"); await tick();
  app.click("save-choice"); await tick();
  assert.match(app.get("saved-summary").textContent, /changed your limits/);
  assert.doesNotMatch(app.get("saved-summary").textContent, /less than your first choice/);
  assert.equal(app.choices[0].evaluation.constraints.time_budget_minutes, 45);
  assert.equal(app.choices[1].evaluation.constraints.time_budget_minutes, 60);
});

test("chat is gated by an initial choice and applying a proposal never saves a choice", async (t) => {
  const app = boot(t); await app.start(); await app.selectStore();
  assert.equal(app.get("chat-section").hidden, true);
  assert.equal(app.calls.some((c) => c.url.endsWith("/chat")), false);
  app.click("save-choice"); await tick();
  assert.equal(app.get("chat-section").hidden, false);
  app.get("chat-input").value = "one store only"; app.submit("chat-form"); await tick();
  assert.equal(app.get("active-stops").value, "2", "A chat response does not change controls");
  assert.equal(app.get("total-cost").textContent, "$12.00");
  assert.equal(app.choices.length, 1);
  assert.equal(app.get("chat-proposal").hidden, false);
  assert.match(app.get("chat-mode").textContent, /Rule-based/);
  app.window.document.querySelector(".chat-plan-option").click(); await tick();
  assert.equal(app.get("active-stops").value, "1");
  assert.equal(app.get("item-0").value, "value");
  assert.equal(app.get("total-cost").textContent, "$11.00");
  assert.equal(app.choices.length, 1, "Apply must not persist a choice");
  assert.match(app.get("save-status").textContent, /has not been saved/);
  app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 2);
  assert.equal(app.choices[1].evaluation.constraints.max_stores, 1);
});

test("chat history restores as safe text and does not overwrite the participant draft", async (t) => {
  const initial = { id: "initial-1", stage: "initial", evaluation: evaluation(), created_at: "2026-11-14T12:00:00Z" };
  const app = boot(t, {
    stored: { id: "session-1", token: "scoped-session-token", assignments: { milk: "corner", bread: "corner" }, constraints },
    initial,
    chatHistory: [{ role: "user", content: "Show <img src=x onerror=alert(1)> options" }, { role: "assistant", content: "Try a <script>safe</script> constraint." }]
  }); await tick();
  assert.equal(app.get("chat-section").hidden, false);
  assert.match(app.get("chat-history").textContent, /<img src=x/);
  assert.match(app.get("chat-history").textContent, /<script>/);
  assert.equal(app.get("chat-history").querySelectorAll("img,script").length, 0);
  assert.equal(app.get("item-0").value, "corner");
  assert.equal(app.choices.length, 1);
});

test("chat lost-response retries reuse UUID and do not duplicate restored conversation", async (t) => {
  let lose = true;
  const app = boot(t, { handler: async (url, request, next) => {
    const result = await next(url, request);
    if (url.endsWith("/chat") && request.method === "POST" && lose) { lose = false; throw new Error("Chat connection interrupted"); }
    return result;
  } });
  await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  app.get("chat-input").value = "walking"; app.submit("chat-form"); await tick();
  assert.match(app.get("chat-status").textContent, /retry safely/);
  assert.equal(app.get("active-mode").value, "drive");
  app.click("chat-retry-history"); await tick();
  app.submit("chat-form"); await tick();
  const requests = app.calls.filter((c) => c.url.endsWith("/chat") && c.request.method === "POST").map((c) => JSON.parse(c.request.body));
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(app.get("chat-history").querySelectorAll(".chat-message").length, 2);
  assert.equal(app.choices.length, 1);
});

test("chat clarification keeps limits and basket unchanged and hides Apply controls", async (t) => {
  const app = boot(t); await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  app.get("chat-input").value = "keep the coffee brand"; app.submit("chat-form"); await tick();
  assert.match(app.get("chat-status").textContent, /specific money, time/);
  assert.equal(app.get("chat-proposal").hidden, true);
  assert.equal(app.get("active-stops").value, "2");
  assert.equal(app.get("item-0").value, "corner");
  assert.equal(app.choices.length, 1);
});

test("chat failures leave the core basket usable and reject oversized messages locally", async (t) => {
  const app = boot(t, { handler: async (url, request, next) => {
    if (url.endsWith("/chat") && request.method === "GET") return response({ detail: "Guide unavailable" }, 503);
    return next(url, request);
  } });
  await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  assert.match(app.get("chat-status").textContent, /basket controls still work/);
  assert.equal(app.get("save-choice").disabled, false);
  const before = app.calls.length;
  app.get("chat-input").value = "x".repeat(1501); app.submit("chat-form"); await tick();
  assert.match(app.get("chat-status").textContent, /1,500 characters/);
  assert.equal(app.calls.length, before);
  await app.selectStore("value");
  assert.equal(app.get("total-cost").textContent, "$11.00");
});


function rentalScenario(id = "west", rent = 230000) {
  const item = scenario();
  item.id = "houston-rental-" + id; item.name = id + " shopping challenge";
  item.world_id = "houston-inner-loop"; item.world_version = "shared-fixture-world";
  Object.assign(item.home, { recorded_address: "1010 Rosine Street", unit_number: "49", housing_price_basis: "monthly_rent", monthly_rent_cents: rent, bedrooms_min: 2, bedrooms_max: 2, living_area_sqft_min: 1300, living_area_sqft_max: 1300, source_listing_date: "2026-11-04T00:00:00+00:00", additional_monthly_fees_cents: null, all_in_monthly_housing_cost_cents: null });
  return item;
}

test("recorded rent and home facts are visible with unknown fees and shared-world context", async (t) => {
  const rental = rentalScenario();
  const app = boot(t, { scenarios: [rental] }); await tick();
  const card = app.window.document.querySelector(".scenario-card");
  assert.match(card.textContent, /\$2,300\.00/);
  assert.match(card.textContent, /month · recorded rent/);
  assert.match(card.textContent, /2 bedrooms · 1,300 sq ft/);
  assert.equal(app.get("selected-home").textContent, "1010 Rosine Street, unit 49");
  assert.match(app.get("home-source-note").textContent, /Historical CoStar asking rent/);
  assert.match(app.get("home-source-note").textContent, /fees and utilities are unknown/);
  assert.match(app.get("world-context").textContent, /same 3 fictional stores and prices/);
  assert.match(app.get("world-context").textContent, /starting home changes modeled travel/);
  assert.match(app.get("world-context").textContent, /shopping budget is set separately/);
  assert.equal(app.get("setup-budget").value, "25.00");
  await app.start();
  assert.equal(app.get("active-home-context").hidden, false);
  assert.match(app.get("active-home-context").textContent, /Recorded asking rent: \$2,300\.00/);
  assert.match(app.get("active-home-context").textContent, /fees and utilities unknown/);
  assert.match(app.get("source-home").textContent, /unit 49 · 2 bedrooms · 1,300 sq ft/);
  assert.match(app.get("source-housing").textContent, /Source listing date: 2026-11-04\./);
  assert.doesNotMatch(app.get("source-housing").textContent, /2026-11-03/);
  assert.equal(app.get("source-world").textContent, app.get("world-context").textContent);
});

test("switching homes updates recorded context without deriving a shopping budget from rent", async (t) => {
  const west = rentalScenario(), east = rentalScenario("east", 320000);
  east.home.recorded_address = 'A home <img src=x onerror="alert(1)">';
  east.home.unit_number = null; east.home.bedrooms_min = 3; east.home.bedrooms_max = 3;
  const app = boot(t, { scenarios: [west, east] }); await tick();
  app.window.document.querySelector('[data-scenario="houston-rental-east"]').click();
  assert.equal(app.get("selected-home").textContent, east.home.recorded_address);
  assert.equal(app.get("selected-home").querySelector("img"), null);
  assert.equal(app.get("setup-budget").value, "25.00");
  assert.equal(app.window.document.querySelector('[data-scenario="houston-rental-east"]').getAttribute("aria-pressed"), "true");
  assert.equal(app.window.document.querySelector('[data-scenario="houston-rental-west"]').getAttribute("aria-pressed"), "false");
  await app.start();
  assert.match(app.get("active-home-context").textContent, /\$3,200\.00/);
  assert.match(app.get("source-home").textContent, /3 bedrooms/);
  assert.equal(app.get("source-home").querySelector("img"), null);
  assert.equal(app.get("active-budget").value, "25.00");
  assert.equal(JSON.parse(app.calls.find((c) => c.url === "/api/v1/sessions").request.body).scenario_id, east.id);
});

test("legacy snapshots keep their own housing context and never turn sale prices into rent", async (t) => {
  const legacy = scenario();
  legacy.home.housing_price_usd = 450000; legacy.home.housing_price_basis = "sale_price";
  const app = boot(t, { scenarios: [rentalScenario()], snapshot: legacy, stored: { id: "session-1", token: "scoped-session-token", assignments: {}, constraints: clone(constraints) } }); await tick();
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.get("active-home-context").hidden, true);
  assert.equal(app.get("active-home-context").textContent, "");
  assert.equal(app.get("source-housing").textContent, legacy.home.housing_context);
  assert.match(app.get("source-world").textContent, /simulated for this scenario/);
  assert.doesNotMatch(app.get("source-world").textContent, /same .*fictional stores/);
  assert.doesNotMatch(app.get("source-housing").textContent, /450,000|\$0|undefined|NaN/);
});

test("absent and invalid rental metadata are omitted instead of displayed as zero", async (t) => {
  const missing = rentalScenario(); delete missing.home.monthly_rent_cents; delete missing.home.housing_context;
  missing.home.bedrooms_min = null; missing.home.bedrooms_max = null;
  const app = boot(t, { scenarios: [missing] }); await tick();
  assert.equal(app.window.document.querySelector(".scenario-rent"), null);
  assert.match(app.get("home-source-note").textContent, /Housing costs are not available/);
  assert.doesNotMatch(app.get("scenario-cards").textContent, /\$0|NaN|undefined/);
  await app.start();
  assert.equal(app.get("active-home-context").hidden, true);
  assert.doesNotMatch(app.get("source-home").textContent, /0 bedrooms|Studio/);
});

function walletProfiles() {
  return [
    { id: "steady", name: "Steady week", available_cents: 40000, scheduled_obligations_cents: 18000, spendable_now_cents: 22000 },
    { id: "tight", name: "Tight week", available_cents: 14000, scheduled_obligations_cents: 11000, spendable_now_cents: 3000 },
    { id: "buffer", name: "Building a buffer", available_cents: 60000, scheduled_obligations_cents: 24000, spendable_now_cents: 36000 },
  ].map((item) => ({ ...item, source: "fixture", description: "An invented checking account and dated example bill.", snapshot_id: "fixture-" + item.id, shopping_allowance_cents: item.spendable_now_cents, reserve_cents: 0 }));
}
function walletContext(profile = walletProfiles()[1]) {
  return { profile_id: profile.id, profile_name: profile.name, description: profile.description, snapshot: { source: "fixture", id: profile.snapshot_id }, projection_inputs: { days: 7, reserve_cents: 0, expected_income: [], events: [] }, projection: { status: "ready", baseline_available_cents: profile.available_cents, scheduled_obligations_cents: profile.scheduled_obligations_cents, spendable_now_cents: profile.spendable_now_cents, ending_available_cents: profile.spendable_now_cents, local_spending_cents: 0, shortfall_cents: 0, reserve_cents: 0 } };
}
function selectWallet(app, id) {
  app.get("wallet-profile").value = id;
  app.get("wallet-profile").dispatchEvent(new app.window.Event("change", { bubbles: true }));
}

test("wallet is optional by default and no profile is sent without an explicit choice", async (t) => {
  const app = boot(t); await tick();
  assert.equal(app.get("wallet-profile").value, "");
  assert.equal(app.get("setup-wallet-context").hidden, true);
  assert.match(app.get("wallet-status").textContent, /no live bank connection/);
  await app.start();
  const body = JSON.parse(app.calls.find((call) => call.url === "/api/v1/sessions").request.body);
  assert.equal(Object.hasOwn(body, "wallet_profile_id"), false);
  assert.equal(app.get("wallet-summary").hidden, true);
  assert.equal(app.get("active-budget").value, "25.00");
});

test("explicit wallet selection shows cash after bills without changing the chosen budget", async (t) => {
  const app = boot(t); await tick(); selectWallet(app, "tight");
  assert.equal(app.get("setup-wallet-context").hidden, false);
  assert.equal(app.get("setup-wallet-checking").textContent, "$140.00");
  assert.equal(app.get("setup-wallet-bills").textContent, "$110.00");
  assert.equal(app.get("setup-wallet-capacity").textContent, "$30.00");
  assert.equal(app.get("setup-budget").value, "25.00");
  assert.match(app.get("setup-wallet-context").textContent, /unused credit is excluded/);
  await app.start();
  const body = JSON.parse(app.calls.find((call) => call.url === "/api/v1/sessions").request.body);
  assert.equal(body.wallet_profile_id, "tight");
  assert.equal(body.constraints.budget_cents, 2500);
  assert.equal(app.get("wallet-heading").textContent, "Fictional wallet");
  assert.equal(app.get("active-wallet-checking").textContent, "$140.00");
  assert.equal(app.get("active-wallet-bills").textContent, "$110.00");
  assert.equal(app.get("active-wallet-capacity").textContent, "$30.00");
  assert.match(app.get("wallet-after-choice").textContent, /Complete your basket/);
});

test("wallet preview counts one current decision and does not accumulate saved revisions", async (t) => {
  const app = boot(t); await tick(); selectWallet(app, "tight"); await app.start(); await app.selectStore();
  assert.match(app.get("wallet-after-choice").textContent, /After this one shop: \$18\.00/);
  app.click("save-choice"); await tick(); await app.selectStore("value");
  assert.match(app.get("wallet-after-choice").textContent, /After this one shop: \$19\.00/);
  app.click("save-choice"); await tick();
  assert.equal(app.choices.length, 2);
  assert.match(app.get("wallet-after-choice").textContent, /\$19\.00/);
  assert.equal(app.get("active-wallet-capacity").textContent, "$30.00");
  assert.match(app.get("wallet-summary").textContent, /Saved revisions are alternatives, not extra purchases/);
  app.get("active-budget").value = "100"; app.submit("limits-form"); await tick();
  assert.equal(app.get("active-wallet-capacity").textContent, "$30.00");
  assert.match(app.get("wallet-after-choice").textContent, /\$19\.00/);
});

test("wallet catalog failure permits the core challenge and retry never invents profiles", async (t) => {
  let fail = true;
  const app = boot(t, { handler: async (url, request, next) => url === "/api/v1/wallets" && fail ? response({ detail: "Catalog offline" }, 503) : next(url, request) }); await tick();
  assert.equal(app.get("setup").hidden, false);
  assert.equal(app.get("wallet-profile").options.length, 1);
  assert.equal(app.get("wallet-profile").disabled, true);
  assert.equal(app.get("wallet-retry").hidden, false);
  assert.match(app.get("wallet-status").textContent, /still start with your own budget/);
  await app.start();
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.get("wallet-summary").hidden, true);
  app.click("new-session"); fail = false; app.click("wallet-retry"); await tick();
  assert.equal(app.get("wallet-profile").options.length, 4);
  assert.equal(app.get("wallet-profile").value, "");
  selectWallet(app, "buffer");
  assert.equal(app.get("setup-wallet-capacity").textContent, "$360.00");
});

test("slow optional wallet loading does not block starting and cannot attach a wallet later", async (t) => {
  let release;
  const app = boot(t, { handler: async (url, request, next) => url === "/api/v1/wallets" ? new Promise((resolve) => { release = async () => resolve(await next(url, request)); }) : next(url, request) });
  await app.start();
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.get("wallet-summary").hidden, true);
  await release(); await tick();
  assert.equal(app.get("wallet-summary").hidden, true);
  assert.equal(app.serverSession.scenario.financial_context, undefined);
});

test("lost session response retries preserve the explicitly chosen wallet and request id", async (t) => {
  let lost = true;
  const app = boot(t, { handler: async (url, request, next) => {
    const result = await next(url, request);
    if (url === "/api/v1/sessions" && lost) { lost = false; throw new Error("Lost create response"); }
    return result;
  } }); await tick(); selectWallet(app, "steady"); await app.start();
  assert.equal(app.get("wallet-profile").value, "steady");
  assert.match(app.get("start-status").textContent, /Lost create response/);
  app.submit("start-form"); await tick();
  const requests = app.calls.filter((call) => call.url === "/api/v1/sessions").map((call) => JSON.parse(call.request.body));
  assert.equal(requests.length, 2);
  assert.equal(requests[0].request_id, requests[1].request_id);
  assert.equal(requests[0].wallet_profile_id, "steady");
  assert.equal(requests[1].wallet_profile_id, "steady");
  assert.equal(app.get("active-wallet-capacity").textContent, "$220.00");
});

test("resumed wallet uses the frozen session projection rather than a changed catalog", async (t) => {
  const snapshot = scenario(); snapshot.financial_context = walletContext();
  const catalog = walletProfiles(); catalog[1].spendable_now_cents = 99900;
  const app = boot(t, { snapshot, wallets: catalog, stored: { id: "session-1", token: "scoped-session-token", constraints, assignments: {} } }); await tick();
  assert.equal(app.get("challenge").hidden, false);
  assert.equal(app.get("active-wallet-capacity").textContent, "$30.00");
  assert.equal(app.get("active-wallet-checking").textContent, "$140.00");
  assert.doesNotMatch(app.get("wallet-summary").textContent, /999/);
  app.click("new-session");
  assert.equal(app.get("wallet-profile").value, "");
  assert.equal(app.get("setup-wallet-context").hidden, true);
});

test("unresolved wallet values stay unknown and a missing preview never reuses an earlier amount", async (t) => {
  let review = false;
  const app = boot(t, { handler: async (url, request, next) => {
    const result = await next(url, request);
    if (url.endsWith("/evaluate") && review) { const data = await result.json(); data.wallet = { summary: { status: "needs_review", baseline_available_cents: null, scheduled_obligations_cents: null, spendable_now_cents: null }, after_choice: null }; return response(data); }
    return result;
  } }); await tick(); selectWallet(app, "tight"); await app.start(); await app.selectStore();
  assert.match(app.get("wallet-after-choice").textContent, /\$18\.00/);
  review = true; await app.selectStore("value");
  assert.equal(app.get("active-wallet-checking").textContent, "Not available");
  assert.equal(app.get("active-wallet-capacity").textContent, "Needs review");
  assert.match(app.get("wallet-after-choice").textContent, /unavailable or needs review/);
  assert.doesNotMatch(app.get("wallet-after-choice").textContent, /\$18|\$0/);
});

function investigationReport(body, anchor) {
  const plans = body.allowed_travel_modes.includes(body.constraints.travel_mode) ? ["value", "corner"].map((store) => ({ ...evaluation({ milk: store, bread: store }, body.constraints), id: "plan_" + store })).filter((plan) => plan.feasible) : [];
  return { mode: "deterministic", status: plans.length ? "feasible" : "no_feasible_plan", request_id: body.request_id, investigation_id: "report-1", anchor_choice_id: anchor, stale: false, constraints: clone(body.constraints), protected_constraints: clone(body.protected_constraints), allowed_travel_modes: clone(body.allowed_travel_modes), wallet: null, explanation: plans.length ? "Another assignment fits without changing your limits." : "No permitted plan fits your rules.", selected_plan: evaluation(body.assignments, body.constraints), feasible_plans: plans, alternatives: [], evidence: [{ id: "ev_limits", text: "The complete basket was checked against the requested limits.", values: {} }], minimum_requirements: { minimum_budget_cents: 1100, minimum_time_minutes: 20 }, question: plans.length > 1 ? { id: "cost_or_time", text: "Would you rather spend less or finish sooner?" } : null, highlighted_alternative_ids: [] };
}
function combinedInvestigation(body, anchor) {
  const report = investigationReport(body, anchor), limits = { ...body.constraints, budget_cents: 1100, time_budget_minutes: 30 };
  report.status = "changes_available"; report.feasible_plans = []; report.question = null;
  report.alternatives = [{ id: "alt_combined", kind: "combined", changes: { budget_cents: 1100, time_budget_minutes: 30 }, constraints: limits, required_minutes: 30, delta: { budget_cents: 100, time_budget_minutes: 15 }, plan: evaluation({ milk: "value", bread: "value" }, limits), evidence_ids: ["ev_limits"] }];
  report.highlighted_alternative_ids = ["alt_combined"]; return report;
}
function changeInvestigationControl(app, id, checked) {
  app.get(id).checked = checked; app.get(id).dispatchEvent(new app.window.Event("change", { bubbles: true }));
}
async function initialInvestigationSession(app, limits) {
  await tick();
  if (limits) { app.get("setup-budget").value = (limits.budget_cents / 100).toFixed(2); app.get("setup-time").value = limits.time_budget_minutes; app.get("setup-stops").value = limits.max_stores; }
  await app.start(); await app.selectStore(); app.click("save-choice"); await tick();
  app.get("investigation-panel").open = true;
}

test("switching starting homes preserves all custom setup limits and a new challenge resets defaults", async (t) => {
  const west = rentalScenario(), east = rentalScenario("east"); east.defaults = { budget_cents: 9000, time_budget_minutes: 99, travel_mode: "drive", max_stores: 2 };
  const app = boot(t, { scenarios: [west, east] }); await tick();
  app.get("setup-budget").value = "55.51"; app.get("setup-time").value = "23"; app.get("setup-mode").value = "walk"; app.get("setup-stops").value = "1";
  app.window.document.querySelector('[data-scenario="houston-rental-east"]').click();
  assert.equal(app.get("setup-budget").value, "55.51"); assert.equal(app.get("setup-time").value, "23");
  assert.equal(app.get("setup-mode").value, "walk"); assert.equal(app.get("setup-stops").value, "1");
  await app.start(); app.click("new-session");
  assert.equal(app.get("setup-budget").value, "90.00"); assert.equal(app.get("setup-time").value, "99");
});

test("investigation opens after the initial choice and sends current draft plus explicit controls", async (t) => {
  const app = boot(t); await app.start();
  assert.equal(app.get("investigation-panel").hidden, true);
  app.submit("investigation-form"); await tick();
  assert.equal(app.calls.some((call) => call.url.endsWith("/investigate")), false);
  await app.selectStore(); app.click("save-choice"); await tick();
  assert.equal(app.get("investigation-panel").hidden, false);
  assert.equal(app.get("investigation-panel").open, false);
  assert.equal(app.get("investigate-mode-drive").checked, true); assert.equal(app.get("investigate-mode-walk").checked, false);
  app.get("active-budget").value = "40"; app.get("active-time").value = "60"; app.submit("limits-form"); await tick(); await app.selectStore("value");
  changeInvestigationControl(app, "investigate-protect-budget", true); changeInvestigationControl(app, "investigate-mode-walk", true);
  app.submit("investigation-form"); await tick();
  const request = JSON.parse(app.calls.find((call) => call.url.endsWith("/investigate")).request.body);
  assert.deepEqual(request.constraints, { ...constraints, budget_cents: 4000, time_budget_minutes: 60 });
  assert.deepEqual(request.assignments, { milk: "value", bread: "value" });
  assert.deepEqual(request.protected_constraints, ["budget_cents"]); assert.deepEqual(request.allowed_travel_modes, ["drive", "walk"]);
  assert.equal(Object.hasOwn(request, "message"), false);
  assert.equal(app.get("investigation-mode").textContent, "Calculated check");
  assert.equal(app.get("investigation-report").hidden, false);
  assert.equal(app.choices.length, 1); assert.equal(app.get("investigation-form").querySelector("textarea"), null);
});

test("combined proposals disclose every changed limit and apply to draft before a separate save", async (t) => {
  const app = boot(t, { investigation: combinedInvestigation });
  await initialInvestigationSession(app, { ...constraints, budget_cents: 1000, time_budget_minutes: 15, max_stores: 1 });
  app.submit("investigation-form"); await tick();
  const card = app.window.document.querySelector('[data-investigation-id="alt_combined"]');
  assert.ok(card, app.get("investigation-status").textContent);
  assert.match(card.textContent, /Shopping budget: \$10\.00 → \$11\.00/);
  assert.match(card.textContent, /Whole-trip time: 15 min → 30 min/);
  assert.match(card.textContent, /Milk: Corner Market → Value Market/);
  assert.match(card.textContent, /Resulting limits: \$11\.00 · 30 min · 1 store · Driving/);
  assert.equal(app.get("active-budget").value, "10.00"); assert.equal(app.choices.length, 1);
  card.querySelector("button").click(); await tick();
  assert.equal(app.get("active-budget").value, "11.00"); assert.equal(app.get("active-time").value, "30");
  assert.equal(app.get("item-0").value, "value"); assert.equal(app.choices.length, 1);
  assert.match(app.get("investigation-status").textContent, /Applied to your draft only/);
  assert.equal(card.querySelector("button").disabled, true);
  app.click("save-choice"); await tick(); assert.equal(app.choices.length, 2);
  assert.equal(app.choices[1].evaluation.constraints.time_budget_minutes, 30);
});

test("one consequential answer prioritizes verified options without hiding other computed choices or editing the draft", async (t) => {
  const app = boot(t, { investigation: (body, anchor) => {
    const report = investigationReport(body, anchor); report.status = "changes_available"; report.feasible_plans = [];
    report.question = { id: "money_or_time", text: "Which is easier to change: spending more or allowing more time?" };
    report.alternatives = [["budget", "corner", { budget_cents: 1200 }], ["time", "value", { time_budget_minutes: 30 }]].map(([kind, store, changes]) => { const limits = { ...body.constraints, ...changes }; const plan = evaluation({ milk: store, bread: store }, limits); return { id: "alt_" + kind, kind, changes, constraints: limits, plan, required_minutes: plan.total_minutes, evidence_ids: ["ev_limits"] }; });
    return report;
  } }); await initialInvestigationSession(app, { ...constraints, budget_cents: 1100, time_budget_minutes: 20, max_stores: 1 });
  app.submit("investigation-form"); await tick();
  assert.equal(app.get("investigation-answers").querySelectorAll("button").length, 2);
  app.window.document.querySelector('[data-investigation-answer="more-time"]').click();
  assert.equal(app.get("investigation-alternatives").firstElementChild.dataset.investigationId, "alt_time");
  assert.equal(app.get("investigation-more").hidden, false);
  assert.ok(app.get("investigation-more-list").querySelector('[data-investigation-id="alt_budget"]'));
  assert.equal(app.get("active-time").value, "20"); assert.equal(app.get("active-budget").value, "11.00");
  assert.equal(app.choices.length, 1); assert.equal(app.calls.filter((call) => call.url.endsWith("/investigate")).length, 1);
});

for (const problem of ["protected", "undisclosed", "transport"]) test("unverified investigation proposal is rejected: " + problem, async (t) => {
  const app = boot(t, { investigation: (body, anchor) => {
    const report = combinedInvestigation(body, anchor), alternative = report.alternatives[0];
    if (problem === "undisclosed") delete alternative.changes.time_budget_minutes;
    if (problem === "transport") { alternative.changes.travel_mode = "walk"; alternative.constraints.travel_mode = "walk"; alternative.plan.constraints.travel_mode = "walk"; }
    return report;
  } }); await initialInvestigationSession(app, { ...constraints, budget_cents: 1000, time_budget_minutes: 15, max_stores: 1 });
  if (problem === "protected") changeInvestigationControl(app, "investigate-protect-budget", true);
  app.submit("investigation-form"); await tick();
  assert.equal(app.window.document.querySelectorAll("[data-investigation-apply]").length, 0);
  assert.match(app.get("investigation-status").textContent, /could not be verified|did not disclose/);
  assert.equal(app.get("active-budget").value, "10.00"); assert.equal(app.get("active-time").value, "15"); assert.equal(app.choices.length, 1);
});

test("explicitly unavailable transport produces no proposal and never silently enables driving", async (t) => {
  const app = boot(t); await initialInvestigationSession(app);
  changeInvestigationControl(app, "investigate-mode-drive", false);
  app.submit("investigation-form"); await tick();
  const body = JSON.parse(app.calls.find((call) => call.url.endsWith("/investigate")).request.body);
  assert.deepEqual(body.allowed_travel_modes, []);
  assert.equal(app.get("investigate-mode-drive").checked, false); assert.equal(app.get("investigate-mode-walk").checked, false);
  assert.equal(app.window.document.querySelectorAll("[data-investigation-apply]").length, 0);
  assert.equal(app.get("investigation-question-wrap").hidden, true);
  assert.match(app.get("investigation-explanation").textContent, /No permitted plan/);
});

test("draft, settings and saved-choice changes invalidate report actions", async (t) => {
  const app = boot(t); await initialInvestigationSession(app);
  app.submit("investigation-form"); await tick();
  let button = app.window.document.querySelector("[data-investigation-apply]"); assert.equal(button.disabled, false);
  changeInvestigationControl(app, "investigate-protect-time", true);
  assert.equal(button.disabled, true); assert.match(app.get("investigation-status").textContent, /settings changed/);
  app.submit("investigation-form"); await tick(); button = app.window.document.querySelector("[data-investigation-apply]");
  await app.selectStore("value"); assert.equal(button.disabled, true); assert.match(app.get("investigation-status").textContent, /draft changed/);
  app.submit("investigation-form"); await tick(); button = app.window.document.querySelector("[data-investigation-apply]");
  app.click("save-choice"); await tick(); assert.equal(button.disabled, true); assert.match(app.get("investigation-status").textContent, /newer choice was saved/);
  const requests = app.calls.filter((call) => call.url.endsWith("/investigate")).map((call) => JSON.parse(call.request.body));
  assert.equal(new Set(requests.map((body) => body.request_id)).size, 3);
});

test("a slower investigation cannot offer apply actions after the participant changes the draft", async (t) => {
  let release;
  const app = boot(t, { handler: async (url, request, next) => {
    const result = await next(url, request);
    return url.endsWith("/investigate") ? new Promise((resolve) => { release = () => resolve(result); }) : result;
  } }); await initialInvestigationSession(app);
  app.submit("investigation-form"); await tick();
  assert.equal(app.get("investigate-button").disabled, true);
  await app.selectStore("value"); release(); await tick();
  assert.match(app.get("investigation-status").textContent, /superseded/);
  assert.ok([...app.window.document.querySelectorAll("[data-investigation-apply]")].every((button) => button.disabled));
  assert.equal(app.get("item-0").value, "value"); assert.equal(app.choices.length, 1);
});

for (const reason of ["stale", "anchor"]) test("server marks an investigation unusable: " + reason, async (t) => {
  const app = boot(t, { investigation: (body, anchor) => { const report = investigationReport(body, anchor); if (reason === "stale") report.stale = true; else report.anchor_choice_id = "newer-choice"; return report; } });
  await initialInvestigationSession(app); app.submit("investigation-form"); await tick();
  assert.match(app.get("investigation-status").textContent, /superseded/);
  assert.ok([...app.window.document.querySelectorAll("[data-investigation-apply]")].every((button) => button.disabled));
  assert.equal(app.choices.length, 1);
});

test("using a proposal verifies the latest server choice and rejects a report superseded in another tab", async (t) => {
  const app = boot(t); await initialInvestigationSession(app); app.submit("investigation-form"); await tick();
  app.choices.push({ id: "choice-from-another-tab", stage: "revised", evaluation: evaluation(), created_at: "2026-11-14T12:01:00Z" });
  const button = app.window.document.querySelector('[data-investigation-apply="plan_value"]'); button.click(); await tick();
  assert.equal(app.get("item-0").value, "corner"); assert.equal(button.disabled, true);
  assert.match(app.get("investigation-status").textContent, /newer choice was saved/);
  assert.equal(app.calls.filter((call) => call.url.endsWith("/choices")).length, 1);
  app.submit("investigation-form"); await tick();
  assert.equal(app.window.document.querySelector('[data-investigation-apply="plan_value"]').disabled, false);
});

test("lost investigation responses reuse durable UUIDs and explicit controls after reload", async (t) => {
  let lost = true;
  const app = boot(t, { handler: async (url, request, next) => { const result = await next(url, request); if (url.endsWith("/investigate") && lost) { lost = false; throw new Error("Lost investigation response"); } return result; } });
  await initialInvestigationSession(app); changeInvestigationControl(app, "investigate-protect-time", true); changeInvestigationControl(app, "investigate-mode-walk", true);
  app.submit("investigation-form"); await tick();
  assert.match(app.get("investigation-status").textContent, /Retry the same check safely/);
  const original = JSON.parse(app.calls.find((call) => call.url.endsWith("/investigate")).request.body);
  const sessionStorage = Object.fromEntries(Array.from({ length: app.window.sessionStorage.length }, (_, index) => { const key = app.window.sessionStorage.key(index); return [key, app.window.sessionStorage.getItem(key)]; }));
  const resumed = boot(t, { stored: JSON.parse(app.window.localStorage.getItem("blockwise.participant.v1")), initial: app.choices[0], sessionStorage }); await tick();
  assert.equal(resumed.get("investigate-protect-time").checked, true); assert.equal(resumed.get("investigate-mode-walk").checked, true);
  resumed.submit("investigation-form"); await tick();
  const retried = JSON.parse(resumed.calls.find((call) => call.url.endsWith("/investigate")).request.body);
  assert.deepEqual(retried, original);
  assert.equal(resumed.get("investigation-report").hidden, false);
  assert.equal(resumed.choices.length, 1);
});

test("wallet review and calculated fallback never become AI claims or usable proposals", async (t) => {
  const app = boot(t, { investigation: (body, anchor) => { const report = investigationReport(body, anchor); report.mode = "fallback"; report.status = "needs_review"; report.wallet = { status: "needs_review", spendable_now_cents: null }; report.feasible_plans = []; report.question = null; report.explanation = 'Wallet entries need review. <img src=x onerror="alert(1)">'; return report; } });
  await initialInvestigationSession(app); app.submit("investigation-form"); await tick();
  assert.equal(app.get("investigation-mode").textContent, "Calculated check");
  assert.equal(app.window.document.querySelectorAll("[data-investigation-apply]").length, 0);
  assert.equal(app.get("investigation-explanation").querySelector("img"), null);
  assert.match(app.get("investigation-explanation").textContent, /Wallet entries need review/);
  assert.equal(app.get("investigation-question-wrap").hidden, true);
});
