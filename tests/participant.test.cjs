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
  const chatReplies = new Map(); let chatConstraints = clone(constraints);
  let sequence = 0;
  const serverSession = { id: "session-1", token: "scoped-session-token", scenario: clone(options.snapshot || scenarios[0]), constraints: clone(constraints), is_demo: false, created_at: "2026-11-14T12:00:00Z", choices };
  const virtualConsole = new VirtualConsole();
  virtualConsole.on("jsdomError", (e) => errors.push(e));
  const dom = new JSDOM(html, { url: options.url || "http://localhost:5183/", runScripts: "outside-only", virtualConsole });
  const window = dom.window;
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.confirm = () => true;
  if (options.stored) window.localStorage.setItem("blockwise.participant.v1", JSON.stringify(options.stored));
  if (options.initial) choices.push(clone(options.initial));
  const defaultHandler = async (url, request) => {
    const body = request.body ? JSON.parse(request.body) : null;
    if (url === "/api/v1/scenarios") return response({ scenarios, simulation_notice: "Simulation." });
    if (url === "/api/v1/sessions") { serverSession.scenario = clone(scenarios.find((item) => item.id === body.scenario_id) || serverSession.scenario); serverSession.constraints = body.constraints; serverSession.is_demo = body.is_demo; return response(serverSession); }
    if (request.headers.Authorization !== "Bearer scoped-session-token") return response({ detail: "Not authorized" }, 401);
    if (url === "/api/v1/sessions/session-1") return response(serverSession);
    if (url.endsWith("/chat")) {
      if (request.method === "GET") return response({ messages: chatHistory, constraints: chatConstraints });
      if (chatReplies.has(body.request_id)) return response(chatReplies.get(body.request_id));
      let status = "ok";
      if (body.message === "one store only") chatConstraints.max_stores = 1;
      else if (body.message === "walking") chatConstraints.travel_mode = "walk";
      else if (body.message === "under 30 minutes") chatConstraints.time_budget_minutes = 30;
      else if (body.message === "budget $60") chatConstraints.budget_cents = 6000;
      else status = "clarification";
      const plan = evaluation({ milk: "value", bread: "value" }, chatConstraints);
      const reply = status === "clarification" ? "Please give a specific budget, time, travel, or stop limit." : "Here are your options with those limits. Apply a proposal when ready.";
      const result = { reply, mode: "offline", constraints: clone(chatConstraints), plans: status === "ok" && plan.feasible ? [{ ...plan, id: "chat-plan", label: "Lowest cost" }] : [], status, notice: "Suggestions do not save choices." };
      chatHistory.push({ role: "user", content: body.message }, { role: "assistant", content: reply });
      chatReplies.set(body.request_id, clone(result));
      return response(result);
    }
    if (url.endsWith("/evaluate")) return response(evaluation(body.assignments, body.constraints));
    if (url.endsWith("/choices")) {
      const existing = choices.find((c) => c.request_id === body.request_id);
      if (existing) return response(existing);
      const choice = { id: "choice-" + ++sequence, request_id: body.request_id, stage: body.stage, evaluation: evaluation(body.assignments, body.constraints), created_at: "2026-11-14T12:00:00Z" };
      choices.push(choice); return response(choice);
    }
    if (url.endsWith("/recommendations")) {
      const plan = evaluation({ milk: "value", bread: "value" }, body.constraints);
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
  assert.equal(app.calls.length, 1);
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
