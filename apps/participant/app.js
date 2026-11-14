(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORE_KEY = "blockwise.participant.v1";
  const requestedDemo = new URL(location.href).searchParams.get("demo") === "1";
  const PENDING_KEY = "blockwise.pending.v1";
  const CHAT_PENDING_KEY = "blockwise.chat-pending.v1";
  const INVESTIGATION_PENDING_KEY = "blockwise.investigation-pending.v1";
  const INVESTIGATION_SETTINGS_KEY = "blockwise.investigation-settings.v1";
  const investigationLimits = { budget_cents: ["budget", "Shopping budget"], time_budget_minutes: ["time", "Whole-trip time"], max_stores: ["stops", "Store limit"], travel_mode: ["travel", "Travel mode"] };
  const colors = ["#d5ee87", "#d0dfc4", "#edd4b7", "#c4d9da", "#e3c7b8"];
  const icons = { milk: "◒", eggs: "◉", bread: "▰", rice: "▤", chicken: "◈", apples: "●", greens: "❧", coffee: "▥" };
  const state = { setupInitialized: false, investigation: null, investigationBusy: false, investigationSequence: 0, investigationRevision: 0, investigationNeedsSync: false, wallets: [], walletProfileId: "", walletLoading: false, walletSequence: 0, scenarios: [], scenario: null, session: null, token: "", constraints: null, assignments: {}, choices: [], evaluation: null, evaluating: false, saving: false, starting: false, epoch: 0, evalSequence: 0, recommendationSequence: 0, pending: {}, lastSavedKey: "", chatMessages: [], chatBusy: false, chatSequence: 0, chatProposal: null, chatPending: {} };
  const money = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
  const minutes = (value) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 1 }) + " min";
  const escape = (value) => String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const constraintsKey = (c) => JSON.stringify([c.budget_cents, c.time_budget_minutes, c.travel_mode, c.max_stores]);
  const draftKey = () => JSON.stringify([state.session?.id, state.choices.some((c) => c.stage === "initial") ? "revised" : "initial", constraintsKey(state.constraints), state.scenario.basket.map((p) => [p.id, state.assignments[p.id] || ""])]);
  const uuid = () => crypto.randomUUID ? crypto.randomUUID() : "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => (+c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> +c / 4).toString(16));
  function notice(id, text) { $(id).textContent = text || ""; $(id).hidden = !text; }
  function storageRead(key, storage = localStorage) { try { return JSON.parse(storage.getItem(key) || "null"); } catch { return null; } }
  function storageWrite(key, value, storage = localStorage) { try { if (value === null) storage.removeItem(key); else storage.setItem(key, JSON.stringify(value)); return true; } catch { return false; } }
  function persist() {
    if (!state.session) return;
    if (!storageWrite(STORE_KEY, { id: state.session.id, token: state.token, assignments: state.assignments, constraints: state.constraints })) notice("session-notice", "This browser cannot remember your session. Keep this tab open to finish your challenge.");
  }
  function pendingId(key) {
    if (!state.pending[key]) {
      state.pending = { [key]: uuid() };
      storageWrite(PENDING_KEY, state.pending, sessionStorage);
    }
    return state.pending[key];
  }
  function clearPending() { state.pending = {}; storageWrite(PENDING_KEY, null, sessionStorage); }
  async function api(path, { method = "GET", body, token = state.token } = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const headers = { Accept: "application/json" };
      if (body !== undefined) headers["Content-Type"] = "application/json";
      if (token) headers.Authorization = "Bearer " + token;
      const response = await fetch("/api/v1" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store", redirect: "error", signal: controller.signal });
      let data;
      try { data = await response.json(); } catch { throw new Error("The app returned an unreadable response. Please try again."); }
      if (!response.ok) {
        const message = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail) ? data.detail.map((e) => e.msg).join(" · ") : "We could not complete that request.";
        const error = new Error(message); error.status = response.status; throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("That request took too long. Your request may still have completed; retrying will not duplicate it.");
      if (error instanceof TypeError) throw new Error("Cannot reach the app right now. Check the connection and try again.");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function setFields(prefix, c) {
    $(prefix + "-budget").value = (c.budget_cents / 100).toFixed(2);
    $(prefix + "-time").value = c.time_budget_minutes;
    $(prefix + "-mode").value = c.travel_mode;
    $(prefix + "-stops").value = c.max_stores;
  }
  function readFields(prefix) {
    const amount = $(prefix + "-budget").value.trim();
    if (!/^\d+(\.\d{1,2})?$/.test(amount)) throw new Error("Enter a positive budget with no more than two decimal places.");
    const [whole, fraction = ""] = amount.split(".");
    const budget = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
    const time = Number($(prefix + "-time").value);
    const mode = $(prefix + "-mode").value;
    const stops = Number($(prefix + "-stops").value);
    if (!Number.isSafeInteger(budget) || budget < 1 || budget > 1000000 || !Number.isFinite(time) || time <= 0 || time > 1440 || !["walk", "drive"].includes(mode) || ![1, 2].includes(stops)) throw new Error("Choose a budget from $0.01–$10,000, a positive time limit up to 1,440 minutes, and one or two stores.");
    return { budget_cents: budget, time_budget_minutes: time, travel_mode: mode, max_stores: stops };
  }
  function renderMap(target, scenario, route = []) {
    const points = [{ ...scenario.home, id: "home" }, ...scenario.stores];
    const coords = points.filter((p) => Number.isFinite(p.latitude) && Number.isFinite(p.longitude));
    if (coords.length !== points.length) { $(target).textContent = "Map unavailable for this scenario."; return; }
    const xs = points.map((p) => p.longitude), ys = points.map((p) => p.latitude);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.min(330 / Math.max(maxX - minX, .001), 210 / Math.max(maxY - minY, .001));
    const midpointX = (minX + maxX) / 2, midpointY = (minY + maxY) / 2;
    const positions = Object.fromEntries(points.map((p) => [p.id, [250 + (p.longitude - midpointX) * scale, 160 - (p.latitude - midpointY) * scale]]));
    const path = route.filter((id) => positions[id]).map((id) => positions[id].join(",")).join(" ");
    const markers = points.map((p, i) => {
      const [x, y] = positions[p.id];
      return '<g transform="translate(' + x.toFixed(1) + ' ' + y.toFixed(1) + ')"><title>' + escape(i ? p.name : "Your starting home") + '</title>' + (i ? '<circle r="14" fill="' + colors[(i - 1) % colors.length] + '" stroke="#fffdf7" stroke-width="3"/><text y="4" text-anchor="middle" font-size="10" font-weight="700" fill="#173f35">' + i + '</text>' : '<circle r="22" fill="#173f35" stroke="#e9eddc" stroke-width="5"/><path d="M-9 0 0-8 9 0v9H3V3h-6v6h-6Z" fill="#d5ee87"/><rect x="-29" y="26" width="58" height="19" rx="9" fill="#fffdf7"/><text y="39" text-anchor="middle" font-size="9" fill="#173f35">Your home</text>') + '</g>';
    }).join("");
    $(target).innerHTML = '<svg viewBox="0 0 500 320" role="img" aria-label="' + escape("Schematic locations for " + scenario.name + ". Numbers match the store cards. Not a road map.") + '"><defs><pattern id="' + target + '-grid" width="65" height="58" patternUnits="userSpaceOnUse" patternTransform="rotate(-13 250 160)"><rect x="6" y="6" width="48" height="41" rx="6" fill="#e0e6d1"/><path d="M0 53H65M59 0V58" stroke="#f6f6e9" stroke-width="8"/></pattern></defs><rect width="500" height="320" fill="url(#' + target + '-grid)"/><path d="M-15 270C90 180 95 210 185 245S360 280 525 215" fill="none" stroke="#c7dcda" stroke-width="18" opacity=".75"/><ellipse cx="403" cy="80" rx="40" ry="26" fill="#c9d8af"/><ellipse cx="107" cy="109" rx="27" ry="34" fill="#c9d8af"/>' + (path ? '<polyline points="' + path + '" fill="none" stroke="#527d57" stroke-width="3" stroke-dasharray="6 5" opacity=".75"/>' : "") + markers + '<text x="469" y="29" font-size="9" fill="#647761" text-anchor="middle">N</text><path d="m469 36-4 10 4-3 4 3Z" fill="#647761"/></svg>';
  }
  function recordedRent(home) {
    return home.housing_price_basis === "monthly_rent" && Number.isSafeInteger(home.monthly_rent_cents) && home.monthly_rent_cents > 0 ? home.monthly_rent_cents : null;
  }
  function homeFacts(home) {
    const range = (min, max, unit) => Number.isFinite(min) && Number.isFinite(max) && min >= 0 && max >= min ? (min === max ? min.toLocaleString("en-US") : min.toLocaleString("en-US") + "–" + max.toLocaleString("en-US")) + " " + unit : "";
    return [home.bedrooms_min === 0 && home.bedrooms_max === 0 ? "Studio" : range(home.bedrooms_min, home.bedrooms_max, "bedrooms"), home.living_area_sqft_min > 0 ? range(home.living_area_sqft_min, home.living_area_sqft_max, "sq ft") : ""].filter(Boolean).join(" · ");
  }
  function homeAddress(home) {
    return home.recorded_address ? home.recorded_address + (home.unit_number ? ", unit " + home.unit_number : "") : "";
  }
  function homeNotice(home) {
    return recordedRent(home) !== null ? "Historical CoStar asking rent. Current availability is unverified; fees and utilities are unknown. This is not an all-in housing cost." : home.housing_context || "Historical home location. Housing costs are not available for this scenario.";
  }
  function worldContext(scenario) {
    return scenario.world_id && scenario.world_version ? "The same " + scenario.stores.length + " fictional stores and prices appear for each home in this world. Your starting home changes modeled travel. Your shopping budget is set separately." : "Stores, prices and travel are simulated for this scenario. Your shopping budget is set separately.";
  }
  function chooseScenario(id) {
    state.scenario = state.scenarios.find((s) => s.id === id);
    if (!state.scenario) return;
    if (!state.setupInitialized) { setFields("setup", state.scenario.defaults); state.setupInitialized = true; }
    [...$("scenario-cards").children].forEach((button) => { const selected = button.dataset.scenario === id; button.classList.toggle("selected", selected); button.setAttribute("aria-pressed", String(selected)); });
    $("preview-location").textContent = state.scenario.home.label;
    $("selected-home").textContent = homeAddress(state.scenario.home);
    $("selected-home").hidden = !$("selected-home").textContent;
    $("home-source-note").textContent = homeNotice(state.scenario.home);
    $("world-context").textContent = worldContext(state.scenario);
    renderMap("preview-map", state.scenario);
  }
  function renderScenarios() {
    $("scenario-cards").replaceChildren();
    state.scenarios.forEach((scenario, i) => {
      const button = document.createElement("button");
      button.type = "button"; button.className = "scenario-card"; button.dataset.scenario = scenario.id;
      const rent = recordedRent(scenario.home), facts = homeFacts(scenario.home);
      const detail = rent !== null ? '<p class="scenario-rent">' + money(rent) + '<span>/month · recorded rent</span></p>' + (facts ? '<p class="scenario-home-facts">' + escape(facts) + '</p>' : "") : '<p>' + escape(scenario.defaults.travel_mode === "walk" ? "A walking-mode challenge. How far is a saving worth?" : "A shopping day with room to explore your options.") + '</p>';
      button.innerHTML = '<span class="scenario-number">STARTING HOME ' + String(i + 1).padStart(2, "0") + '</span><span class="selection-dot" aria-hidden="true"></span><strong>' + escape(scenario.name.replace(/ shopping challenge$/i, "")) + '</strong>' + detail;
      button.addEventListener("click", () => chooseScenario(scenario.id)); $("scenario-cards").append(button);
    });
    chooseScenario(state.scenarios.some((scenario) => scenario.id === state.scenario?.id) ? state.scenario.id : state.scenarios[0]?.id);
  }
  function walletAmount(value) { return Number.isSafeInteger(value) ? money(value) : "Not available"; }
  function renderWalletSelection() {
    const wallet = state.wallets.find((item) => item.id === state.walletProfileId);
    $("wallet-profile").value = wallet?.id || "";
    $("wallet-profile").disabled = state.starting || state.walletLoading || !state.wallets.length;
    $("setup-wallet-context").hidden = !wallet;
    if (!wallet) return;
    $("setup-wallet-description").textContent = wallet.description || wallet.name;
    $("setup-wallet-checking").textContent = walletAmount(wallet.available_cents);
    $("setup-wallet-bills").textContent = walletAmount(wallet.scheduled_obligations_cents);
    $("setup-wallet-capacity").textContent = walletAmount(wallet.spendable_now_cents);
  }
  async function loadWallets() {
    if (state.walletLoading) return;
    const sequence = ++state.walletSequence;
    state.walletLoading = true; $("wallet-retry").hidden = true;
    $("wallet-status").textContent = "Loading optional wallet profiles…"; renderWalletSelection();
    try {
      const result = await api("/wallets", { token: "" });
      if (sequence !== state.walletSequence) return;
      if (!Array.isArray(result.wallets) || result.wallets.some((item) => !item || typeof item.id !== "string" || !item.id || typeof item.name !== "string" || item.source !== "fixture") || new Set(result.wallets.map((item) => item.id)).size !== result.wallets.length) throw new Error("The optional wallet profiles could not be verified.");
      state.wallets = result.wallets;
      $("wallet-profile").replaceChildren(new Option("Choose your own budget (no wallet)", ""));
      state.wallets.forEach((item) => $("wallet-profile").append(new Option(item.name, item.id)));
      if (!state.wallets.some((item) => item.id === state.walletProfileId)) state.walletProfileId = "";
      $("wallet-status").textContent = state.wallets.length ? "Fictional examples created for this experiment; no live bank connection." : "No optional wallet profiles are available. You can still start with your own budget.";
      $("wallet-retry").hidden = Boolean(state.wallets.length);
    } catch (error) {
      if (sequence !== state.walletSequence) return;
      $("wallet-status").textContent = "Optional wallet profiles are unavailable. You can still start with your own budget. " + error.message;
      $("wallet-retry").hidden = false;
    } finally {
      if (sequence === state.walletSequence) { state.walletLoading = false; renderWalletSelection(); }
    }
  }
  function renderWalletContext(verifiedProjection) {
    const context = state.scenario?.financial_context;
    $("wallet-summary").hidden = !context;
    if (!context) return;
    const projection = verifiedProjection || context.projection || {};
    $("wallet-heading").textContent = context.snapshot?.source === "fixture" ? "Fictional wallet" : "Saved wallet context";
    $("active-wallet-description").textContent = context.profile_name || context.description || "Saved financial context";
    $("active-wallet-checking").textContent = walletAmount(projection.baseline_available_cents);
    $("active-wallet-bills").textContent = walletAmount(projection.scheduled_obligations_cents);
    $("active-wallet-capacity").textContent = projection.status === "ready" ? walletAmount(projection.spendable_now_cents) : "Needs review";
    renderWalletPreview();
  }
  function renderWalletPreview(evaluation) {
    if (!state.scenario?.financial_context) return;
    const target = $("wallet-after-choice"), after = evaluation?.wallet?.after_choice;
    target.classList.remove("over-text");
    if (!evaluation) { target.textContent = "Complete your basket to preview this one decision."; return; }
    if (!after || after.status !== "ready" || after.local_spending_cents !== evaluation.total_cost_cents || !Number.isSafeInteger(after.spendable_now_cents) || !Number.isSafeInteger(after.ending_available_cents)) { target.textContent = "Wallet preview is unavailable or needs review for this plan."; return; }
    target.textContent = "After this one shop: " + money(after.spendable_now_cents) + " shopping capacity left; " + money(after.ending_available_cents) + " modeled cash at the end of the example week.";
    if (Number.isSafeInteger(after.shortfall_cents) && after.shortfall_cents > 0) { target.textContent += " Cash shortfall: " + money(after.shortfall_cents) + "."; target.classList.add("over-text"); }
  }
  function storeName(id) { return id === "home" ? "Home" : state.scenario.stores.find((s) => s.id === id)?.name || "Store"; }
  function renderStores() {
    $("store-cards").replaceChildren();
    state.scenario.stores.forEach((store, i) => {
      const price = state.scenario.basket.reduce((sum, product) => sum + store.prices[product.id] * product.quantity, 0);
      const button = document.createElement("button"); button.type = "button"; button.className = "store-card"; button.dataset.store = store.id;
      button.style.setProperty("--store-color", colors[i % colors.length]);
      button.innerHTML = '<span class="store-icon" aria-hidden="true">' + (i + 1) + '</span><strong>' + escape(store.name) + '</strong><span class="store-price">' + money(price) + '</span><small>Whole basket · before transport</small><span class="store-cta">Choose this store →</span>';
      button.setAttribute("aria-label", "Buy the whole basket at " + store.name + " for " + money(price) + " before transport");
      button.addEventListener("click", () => { state.assignments = Object.fromEntries(state.scenario.basket.map((p) => [p.id, store.id])); draftChanged(); });
      $("store-cards").append(button);
    });
  }
  function renderBasket() {
    $("basket-count").textContent = state.scenario.basket.length + " items";
    $("basket-items").replaceChildren();
    state.scenario.basket.forEach((product, i) => {
      const row = document.createElement("div"); row.className = "basket-row";
      row.innerHTML = '<span class="item-icon" aria-hidden="true">' + (icons[product.id] || "◈") + '</span><label class="item-name" for="item-' + i + '">' + escape(product.name) + '<span class="item-quantity">' + product.quantity + ' × ' + escape(product.unit) + '</span></label><select id="item-' + i + '" aria-label="Store for ' + escape(product.name) + '"><option value="">Choose a store</option></select><span class="item-price" id="price-' + i + '">—</span>';
      const select = row.querySelector("select");
      state.scenario.stores.forEach((store) => { const option = document.createElement("option"); option.value = store.id; option.textContent = store.name + " · " + money(store.prices[product.id] * product.quantity); select.append(option); });
      select.addEventListener("change", () => { if (select.value) state.assignments[product.id] = select.value; else delete state.assignments[product.id]; draftChanged(); });
      $("basket-items").append(row);
    });
    syncAssignments();
  }
  function syncAssignments() {
    state.scenario.basket.forEach((p, i) => {
      const store = state.scenario.stores.find((s) => s.id === state.assignments[p.id]);
      $("item-" + i).value = store?.id || "";
      $("price-" + i).textContent = store ? money(store.prices[p.id] * p.quantity) : "—";
    });
    [...$("store-cards").children].forEach((button) => { const selected = state.scenario.basket.every((p) => state.assignments[p.id] === button.dataset.store); button.classList.toggle("chosen", selected); button.setAttribute("aria-pressed", String(selected)); });
  }
  function complete() { return state.scenario.basket.every((p) => state.scenario.stores.some((s) => s.id === state.assignments[p.id])); }
  function renderProgress() {
    const initial = state.choices.some((c) => c.stage === "initial"), revised = state.choices.some((c) => c.stage === "revised");
    $("step-build").className = initial ? "done" : "active";
    $("step-compare").className = revised ? "done" : initial ? "active" : "";
    $("step-save").className = revised ? "active" : "";
    [$("step-build"), $("step-compare"), $("step-save")].forEach((el) => { if (el.classList.contains("active")) el.setAttribute("aria-current", "step"); else el.removeAttribute("aria-current"); });
    $("save-choice").textContent = state.saving ? "Saving your choice…" : state.lastSavedKey === draftKey() ? "Choice saved ✓" : initial ? "Save my revised choice →" : "Save my first choice →";
    $("save-explainer").textContent = initial ? "Trying a plan does not save it. Save when you are ready." : "Save your first choice to reveal alternatives.";
    $("save-choice").disabled = !state.evaluation || state.evaluating || state.saving || state.lastSavedKey === draftKey();
    updateInvestigationControls();
  }
  function clearEvaluation() {
    state.evaluation = null; renderWalletPreview();
    ["total-cost", "total-time", "basket-cost", "transport-cost", "time-breakdown"].forEach((id) => $(id).textContent = "—");
    $("money-meter").style.width = "0"; $("time-meter").style.width = "0";
    $("money-meter").parentElement.classList.remove("over"); $("time-meter").parentElement.classList.remove("over");
    $("money-remaining").classList.remove("over-text"); $("time-remaining").classList.remove("over-text");
    $("money-remaining").textContent = "Choose a store for every item.";
    $("time-remaining").textContent = "Includes getting back home.";
    $("route-display").textContent = "Home → your stores → home";
    $("plan-state").textContent = "Build your basket"; $("plan-heading").textContent = "Room to work with.";
    renderProgress();
  }
  function validEvaluation(e) {
    return e && ["basket_cost_cents", "transport_cost_cents", "total_cost_cents"].every((k) => Number.isSafeInteger(e[k]) && e[k] >= 0) && ["travel_minutes", "shopping_minutes", "total_minutes"].every((k) => Number.isFinite(e[k]) && e[k] >= 0) && typeof e.feasible === "boolean" && Array.isArray(e.violations) && Array.isArray(e.route) && e.constraints && e.assignments;
  }
  function displayEvaluation(e) {
    if (!validEvaluation(e)) throw new Error("We could not verify this plan. Please recalculate.");
    state.evaluation = e;
    $("total-cost").textContent = money(e.total_cost_cents); $("total-time").textContent = minutes(e.total_minutes);
    $("basket-cost").textContent = money(e.basket_cost_cents); $("transport-cost").textContent = money(e.transport_cost_cents);
    $("time-breakdown").textContent = minutes(e.travel_minutes) + " / " + minutes(e.shopping_minutes);
    const remainingMoney = state.constraints.budget_cents - e.total_cost_cents, remainingTime = state.constraints.time_budget_minutes - e.total_minutes;
    $("money-remaining").textContent = remainingMoney >= 0 ? money(remainingMoney) + " under your chosen " + money(state.constraints.budget_cents) + " budget" : money(-remainingMoney) + " over your budget";
    $("time-remaining").textContent = remainingTime >= 0 ? minutes(remainingTime) + " left from " + minutes(state.constraints.time_budget_minutes) : minutes(-remainingTime) + " over your time limit";
    [["money", e.total_cost_cents / state.constraints.budget_cents, remainingMoney], ["time", e.total_minutes / state.constraints.time_budget_minutes, remainingTime]].forEach(([name, ratio, left]) => { $(name + "-meter").style.width = Math.min(100, ratio * 100) + "%"; $(name + "-meter").parentElement.classList.toggle("over", left < 0); $(name + "-remaining").classList.toggle("over-text", left < 0); });
    $("plan-state").textContent = e.feasible ? "Within your limits" : "Outside your limits";
    $("plan-heading").textContent = e.feasible ? "This could work." : "Something has to give.";
    $("route-display").textContent = e.route.map(storeName).join(" → ");
    notice("evaluation-error", e.feasible ? "" : e.violations.join(" ") + " You can still save this as your choice in the experiment.");
    if (e.wallet?.summary) renderWalletContext(e.wallet.summary);
    renderWalletPreview(e); renderMap("active-map", state.scenario, e.route); renderProgress();
  }
  function draftChanged() {
    invalidateInvestigation("Your draft changed. Check again before applying a proposal.");
    state.evalSequence++; state.evaluation = null; state.evaluating = false;
    $("save-status").textContent = ""; $("saved-result").hidden = true;
    syncAssignments(); persist(); clearEvaluation(); notice("evaluation-error", ""); $("retry-evaluation").hidden = true;
    evaluate();
  }
  async function evaluate() {
    const sequence = ++state.evalSequence, epoch = state.epoch;
    if (!complete()) { clearEvaluation(); return; }
    const stores = new Set(Object.values(state.assignments));
    if (stores.size > 2) { clearEvaluation(); notice("evaluation-error", "Choose no more than two stores for this challenge. You can use a whole-basket shortcut above."); return; }
    state.evaluating = true; state.evaluation = null; renderProgress(); $("plan-state").textContent = "Calculating…"; $("retry-evaluation").hidden = true;
    try {
      const evaluation = await api("/sessions/" + encodeURIComponent(state.session.id) + "/evaluate", { method: "POST", body: { assignments: copy(state.assignments), constraints: copy(state.constraints) } });
      if (sequence !== state.evalSequence || epoch !== state.epoch) return;
      displayEvaluation(evaluation);
    } catch (error) {
      if (sequence !== state.evalSequence || epoch !== state.epoch) return;
      notice("evaluation-error", error.message); $("plan-state").textContent = "Needs a retry"; $("retry-evaluation").hidden = false;
    } finally { if (sequence === state.evalSequence && epoch === state.epoch) { state.evaluating = false; renderProgress(); } }
  }
  function renderChallenge() {
    $("setup").hidden = true; $("challenge").hidden = false; $("how-link").hidden = true;
    $("scenario-eyebrow").textContent = state.scenario.name.toUpperCase() + (state.session.is_demo ? " · DEMO" : "");
    $("challenge-description").textContent = state.scenario.description;
    setFields("active", state.constraints);
    $("limits-summary").textContent = money(state.constraints.budget_cents) + " · " + minutes(state.constraints.time_budget_minutes);
    const home = state.scenario.home, rent = recordedRent(home), facts = homeFacts(home);
    $("active-home-context").hidden = rent === null;
    $("active-home-context").textContent = rent !== null ? "Recorded asking rent: " + money(rent) + "/month · historical record · fees and utilities unknown" : "";
    $("source-home").textContent = (homeAddress(home) || home.label) + (facts ? " · " + facts : "") + (home.source === "costar" ? " — recorded CoStar location." : "");
    const listingDate = typeof home.source_listing_date === "string" && /^\d{4}-\d{2}-\d{2}T/.test(home.source_listing_date) ? " Source listing date: " + home.source_listing_date.slice(0, 10) + "." : "";
    $("source-housing").textContent = homeNotice(home) + listingDate;
    $("source-world").textContent = worldContext(state.scenario);
    $("source-notes").replaceChildren();
    Object.values(state.scenario.provenance || {}).forEach((text) => { const p = document.createElement("p"); p.textContent = text; $("source-notes").append(p); });
    resetInvestigation(); renderWalletContext(); renderStores(); renderBasket(); renderMap("active-map", state.scenario); clearEvaluation();
    $("recommendations-section").hidden = !state.choices.some((c) => c.stage === "initial");
    if (complete()) evaluate();
    if (state.choices.some((c) => c.stage === "initial")) { recommendations(); loadChat(); } else { $("chat-section").hidden = true; }
  }
  async function start(event) {
    event.preventDefault(); if (state.starting) return;
    if (!$("consent").checked) { $("start-status").textContent = "Please agree to save your anonymous choices before starting."; return; }
    let constraints;
    try { constraints = readFields("setup"); } catch (error) { $("start-status").textContent = error.message; return; }
    if (!state.scenario) return;
    const body = { scenario_id: state.scenario.id, constraints, is_demo: requestedDemo, consent: true };
    if (state.walletProfileId) body.wallet_profile_id = state.walletProfileId;
    body.request_id = pendingId("create:" + JSON.stringify(body));
    state.starting = true; renderWalletSelection(); $("start-button").disabled = true; $("start-status").textContent = "Preparing your challenge…";
    try {
      const session = await api("/sessions", { method: "POST", body, token: "" });
      if (!session.id || !session.token || !session.scenario?.basket) throw new Error("Your session could not be verified. Please try again.");
      state.epoch++; state.session = session; state.token = session.token; state.scenario = session.scenario; state.constraints = session.constraints; state.assignments = {}; state.choices = session.choices || []; state.lastSavedKey = "";
      clearPending(); persist(); $("start-status").textContent = ""; renderChallenge(); $("main").scrollIntoView?.({ behavior: "smooth" });
    } catch (error) { $("start-status").textContent = error.message; }
    finally { state.starting = false; renderWalletSelection(); $("start-button").disabled = false; }
  }
  async function saveChoice() {
    if (state.saving || !state.evaluation || state.evaluating || state.lastSavedKey === draftKey()) return;
    const epoch = state.epoch, key = draftKey();
    const stage = state.choices.some((c) => c.stage === "initial") ? "revised" : "initial";
    const body = { request_id: pendingId("choice:" + key), stage, assignments: copy(state.assignments), constraints: copy(state.constraints) };
    state.saving = true; renderProgress(); $("save-status").textContent = "";
    try {
      const choice = await api("/sessions/" + encodeURIComponent(state.session.id) + "/choices", { method: "POST", body });
      if (epoch !== state.epoch) return;
      if (!choice.id || !validEvaluation(choice.evaluation)) throw new Error("We could not verify that your choice saved. Retry to check the same submission.");
      if (!state.choices.some((c) => c.id === choice.id)) state.choices.push(choice);
      invalidateInvestigation("A newer choice was saved. Check again before applying a proposal.");
      clearPending(); state.lastSavedKey = stage === "revised" ? key : ""; persist();
      $("save-status").textContent = stage === "initial" ? "First choice saved. Now explore your alternatives below." : "Your revised choice is saved.";
      if (stage === "initial") {
        $("recommendations-section").hidden = false; loadChat(); await recommendations(); $("recommendations-section").scrollIntoView?.({ behavior: "smooth", block: "start" });
      } else {
        const initial = state.choices.find((c) => c.stage === "initial").evaluation, latest = choice.evaluation;
        const comparable = constraintsKey(initial.constraints) === constraintsKey(latest.constraints);
        const delta = initial.total_cost_cents - latest.total_cost_cents, timeDelta = latest.total_minutes - initial.total_minutes;
        $("saved-summary").textContent = "Your saved plan: " + money(latest.total_cost_cents) + " and " + minutes(latest.total_minutes) + ". " + (comparable ? (delta >= 0 ? money(delta) + " less" : money(-delta) + " more") + " than your first choice, with " + minutes(Math.abs(timeDelta)) + (timeDelta >= 0 ? " more time." : " less time.") : "You changed your limits, so this is a new set of tradeoffs rather than a like-for-like saving.") + (latest.feasible ? "" : " This saved choice is outside your current limits.");
        $("saved-result").hidden = false; $("saved-result").scrollIntoView?.({ behavior: "smooth", block: "start" });
      }
    } catch (error) { if (epoch === state.epoch) $("save-status").textContent = error.message; }
    finally { if (epoch === state.epoch) { state.saving = false; renderProgress(); } }
  }
  async function recommendations() {
    if (!state.choices.some((c) => c.stage === "initial")) return;
    const sequence = ++state.recommendationSequence, epoch = state.epoch;
    $("recommendations-section").hidden = false; $("recommendation-cards").replaceChildren(); $("recommendations-note").textContent = "";
    notice("recommendations-status", "Finding plans within your limits…"); $("refresh-recommendations").disabled = true;
    try {
      const result = await api("/sessions/" + encodeURIComponent(state.session.id) + "/recommendations", { method: "POST", body: { constraints: copy(state.constraints) } });
      if (sequence !== state.recommendationSequence || epoch !== state.epoch) return;
      if (!Array.isArray(result.plans) || result.plans.some((p) => !validEvaluation(p) || !p.feasible)) throw new Error("We could not verify these alternatives. Please refresh the options.");
      if (result.status === "needs_review") notice("recommendations-status", "Your saved financial context needs review, so verified shopping options are unavailable. Your original choice remains saved.");
      else if (result.status === "no_feasible_plan" || !result.plans.length) notice("recommendations-status", "No complete shopping plan fits all your current limits. " + (state.scenario.financial_context ? "Your wallet's cash cap is separate from your chosen budget; increasing that budget does not increase the cash cap. " : "Open Your limits to allow more time, a larger budget, or another store. ") + "We will keep your original choice.");
      else notice("recommendations-status", "");
      result.plans.forEach((plan) => {
        const card = document.createElement("article"); card.className = "recommendation-card";
        card.innerHTML = '<p class="eyebrow">' + escape(plan.label || "Another option") + '</p><div class="recommendation-metrics"><div><strong>' + money(plan.total_cost_cents) + '</strong><span>Complete basket + transport</span></div><div><strong>' + minutes(plan.total_minutes) + '</strong><span>Door to door</span></div></div><p>' + escape(plan.route.map(storeName).join(" → ")) + '</p><button class="button secondary" type="button">Try this plan <span aria-hidden="true">→</span></button>';
        card.querySelector("button").addEventListener("click", () => { state.assignments = copy(plan.assignments); draftChanged(); $("save-status").textContent = "Plan loaded. Review it, then save your revised choice."; $("plan-heading").scrollIntoView?.({ behavior: "smooth", block: "center" }); });
        $("recommendation-cards").append(card);
      });
      $("recommendations-note").textContent = (Number.isInteger(result.feasible_plan_count) ? result.feasible_plan_count + " feasible plans checked. " : "") + (result.notice || "Prices and travel are modeled. No limits were relaxed.");
    } catch (error) { if (sequence === state.recommendationSequence && epoch === state.epoch) notice("recommendations-status", error.message); }
    finally { if (sequence === state.recommendationSequence && epoch === state.epoch) $("refresh-recommendations").disabled = false; }
  }
  function investigationSettings() {
    return {
      protected_constraints: Object.keys(investigationLimits).filter((key) => $("investigate-protect-" + investigationLimits[key][0]).checked).sort(),
      allowed_travel_modes: ["drive", "walk"].filter((mode) => $("investigate-mode-" + mode).checked && !$("investigate-mode-" + mode).dataset.unavailable),
    };
  }
  function investigationBody() { return { constraints: copy(state.constraints), assignments: copy(state.assignments), ...investigationSettings() }; }
  function investigationKey(body = investigationBody()) {
    return JSON.stringify([state.session?.id, state.choices.at(-1)?.id, constraintsKey(body.constraints), state.scenario.basket.map((item) => [item.id, body.assignments[item.id] || ""]), body.protected_constraints, body.allowed_travel_modes]);
  }
  function investigationUsable() {
    const value = state.investigation;
    return Boolean(value && !value.invalidated && !value.report.stale && value.report.anchor_choice_id === state.choices.at(-1)?.id && value.key === investigationKey() && !state.saving);
  }
  function updateInvestigationControls() {
    const allowed = Boolean(state.session && state.choices.some((choice) => choice.stage === "initial"));
    $("investigation-panel").hidden = !allowed;
    $("investigate-button").disabled = !allowed || state.investigationBusy || state.saving || !complete();
    $("investigate-button").textContent = state.investigationBusy ? "Checking…" : "Check possible changes";
    $("investigation-form").querySelectorAll("input").forEach((input) => { input.disabled = state.investigationBusy || Boolean(input.dataset.unavailable); });
    $("investigation-report").querySelectorAll("button").forEach((button) => { button.disabled = state.investigationBusy || !investigationUsable(); });
  }
  function invalidateInvestigation(message) {
    state.investigationRevision++;
    if (state.investigation) { state.investigation.invalidated = true; $("investigation-status").textContent = message; }
    updateInvestigationControls();
  }
  function resetInvestigation() {
    state.investigationSequence++; state.investigationRevision++;
    state.investigation = null; state.investigationBusy = false; state.investigationNeedsSync = false;
    $("investigation-panel").open = false; $("investigation-report").hidden = true; $("investigation-status").textContent = "";
    const saved = storageRead(INVESTIGATION_SETTINGS_KEY, sessionStorage);
    const prior = saved?.session_id === state.session.id ? saved : null;
    Object.entries(investigationLimits).forEach(([key, names]) => { $("investigate-protect-" + names[0]).checked = Array.isArray(prior?.protected_constraints) && prior.protected_constraints.includes(key); });
    const available = state.scenario.financial_context?.projection?.allowed_travel_modes;
    ["walk", "drive"].forEach((mode) => {
      const input = $("investigate-mode-" + mode), unavailable = Array.isArray(available) && !available.includes(mode);
      if (unavailable) input.dataset.unavailable = "true"; else delete input.dataset.unavailable;
      input.checked = !unavailable && (Array.isArray(prior?.allowed_travel_modes) ? prior.allowed_travel_modes.includes(mode) : state.constraints.travel_mode === mode);
    });
    $("investigation-transport-note").textContent = Array.isArray(available) && available.length < 2 ? "Only checked modes can be proposed. Unavailable modes are excluded by this session's wallet context." : "Only checked modes can be proposed. Leaving both unchecked means no transport is available.";
    updateInvestigationControls();
  }
  function investigationSettingChanged() {
    storageWrite(INVESTIGATION_SETTINGS_KEY, { session_id: state.session.id, ...investigationSettings() }, sessionStorage);
    invalidateInvestigation("Your investigation settings changed. Check again before applying a proposal.");
  }
  function validInvestigationPlan(plan, constraints, request, report) {
    if (!validEvaluation(plan) || !plan.feasible || plan.violations.length || !validConstraints(constraints) || constraintsKey(plan.constraints) !== constraintsKey(constraints)) return false;
    const itemIds = state.scenario.basket.map((item) => item.id), storeIds = state.scenario.stores.map((store) => store.id);
    if (!plan.assignments || Object.keys(plan.assignments).length !== itemIds.length || !itemIds.every((id) => storeIds.includes(plan.assignments[id]))) return false;
    const visited = [...new Set(Object.values(plan.assignments))];
    if (visited.length > constraints.max_stores || !Array.isArray(plan.store_ids) || plan.store_ids.length !== visited.length || !visited.every((id) => plan.store_ids.includes(id))) return false;
    if (plan.route[0] !== "home" || plan.route.at(-1) !== "home" || plan.route.some((id) => id !== "home" && !visited.includes(id)) || !visited.every((id) => plan.route.includes(id))) return false;
    if (plan.total_cost_cents !== plan.basket_cost_cents + plan.transport_cost_cents || plan.total_cost_cents > constraints.budget_cents || plan.total_minutes > constraints.time_budget_minutes) return false;
    if (!report.allowed_travel_modes.includes(constraints.travel_mode) || request.protected_constraints.some((key) => constraints[key] !== request.constraints[key])) return false;
    if (report.wallet && (report.wallet.status !== "ready" || !Number.isSafeInteger(report.wallet.spendable_now_cents) || plan.total_cost_cents > report.wallet.spendable_now_cents)) return false;
    return true;
  }
  function checkedInvestigation(report, request, requestId) {
    const sameSet = (a, b) => Array.isArray(a) && a.length === b.length && new Set(a).size === a.length && a.every((value) => b.includes(value));
    if (!report || !["deterministic", "langchain", "fallback"].includes(report.mode) || !["feasible", "changes_available", "no_feasible_plan", "needs_review"].includes(report.status) || report.request_id !== requestId || typeof report.anchor_choice_id !== "string" || typeof report.stale !== "boolean" || typeof report.explanation !== "string" || !validConstraints(report.constraints) || constraintsKey(report.constraints) !== constraintsKey(request.constraints)) throw new Error("We could not verify this investigation. Your draft is unchanged.");
    if (!sameSet(report.protected_constraints, request.protected_constraints) || !Array.isArray(report.allowed_travel_modes) || new Set(report.allowed_travel_modes).size !== report.allowed_travel_modes.length || report.allowed_travel_modes.some((mode) => !request.allowed_travel_modes.includes(mode))) throw new Error("The investigation did not preserve your protected limits or available transport.");
    if (report.wallet && Array.isArray(report.wallet.allowed_travel_modes) && report.allowed_travel_modes.some((mode) => !report.wallet.allowed_travel_modes.includes(mode))) throw new Error("The investigation proposed unavailable transport.");
    if (!Array.isArray(report.alternatives) || report.alternatives.length > 8 || !Array.isArray(report.feasible_plans) || report.feasible_plans.length > 8 || !Array.isArray(report.evidence) || report.evidence.length > 40 || report.evidence.some((entry) => !entry || typeof entry.id !== "string" || typeof entry.text !== "string")) throw new Error("The investigation returned an unreadable report.");
    const entries = report.feasible_plans.map((plan) => ({ id: plan.id, kind: "feasible", changes: {}, constraints: report.constraints, plan, required_minutes: plan.total_minutes }));
    report.alternatives.forEach((alternative) => {
      if (!alternative || !alternative.changes || typeof alternative.changes !== "object" || !validConstraints(alternative.constraints)) throw new Error("A proposal has incomplete changes.");
      const changed = Object.keys(investigationLimits).filter((key) => alternative.constraints[key] !== request.constraints[key]);
      if (!changed.length || !sameSet(Object.keys(alternative.changes), changed) || changed.some((key) => alternative.changes[key] !== alternative.constraints[key])) throw new Error("A proposal did not disclose every required change.");
      if (!Array.isArray(alternative.evidence_ids) || alternative.evidence_ids.some((id) => !report.evidence.some((entry) => entry.id === id))) throw new Error("A proposal has unverified evidence.");
      entries.push(alternative);
    });
    if ((report.status === "needs_review" || report.status === "no_feasible_plan") && entries.length) throw new Error("This report cannot offer a verified draft.");
    if (new Set(entries.map((entry) => entry.id)).size !== entries.length || entries.some((entry) => typeof entry.id !== "string" || !/^[a-zA-Z0-9_-]{1,80}$/.test(entry.id) || !Number.isFinite(entry.required_minutes) || entry.required_minutes < 0 || !validInvestigationPlan(entry.plan, entry.constraints, request, report))) throw new Error("A proposed plan could not be verified against your rules.");
    if (report.question !== null && report.question !== undefined && (typeof report.question.id !== "string" || typeof report.question.text !== "string")) throw new Error("The investigation question could not be read.");
    return entries;
  }
  function investigationLimitText(key, value) {
    if (key === "budget_cents") return money(value);
    if (key === "time_budget_minutes") return minutes(value);
    if (key === "travel_mode") return value === "walk" ? "Walking" : "Driving";
    return value + (value === 1 ? " store" : " stores");
  }
  function investigationCard(entry) {
    const card = document.createElement("article"), heading = document.createElement("h3"), changes = document.createElement("ul"), limits = document.createElement("p"), result = document.createElement("p"), route = document.createElement("p"), button = document.createElement("button");
    card.className = "investigation-card"; card.dataset.investigationId = entry.id; changes.className = "investigation-changes";
    heading.textContent = Object.keys(entry.changes).length ? "Changes to consider" : "A plan within your limits";
    if (!Object.keys(entry.changes).length) { const item = document.createElement("li"); item.textContent = "No limit changes."; changes.append(item); }
    Object.keys(investigationLimits).filter((key) => key in entry.changes).forEach((key) => { const item = document.createElement("li"); item.textContent = investigationLimits[key][1] + ": " + investigationLimitText(key, state.investigation.request.constraints[key]) + " → " + investigationLimitText(key, entry.changes[key]); changes.append(item); });
    limits.className = "investigation-limits"; limits.textContent = "Resulting limits: " + Object.keys(investigationLimits).map((key) => investigationLimitText(key, entry.constraints[key])).join(" · ");
    result.className = "investigation-result"; result.textContent = money(entry.plan.total_cost_cents) + " total · " + minutes(entry.plan.total_minutes) + " whole trip";
    route.className = "quiet"; route.textContent = entry.plan.route.map(storeName).join(" → ");
    card.append(heading, changes, limits, result, route);
    const changedItems = state.scenario.basket.filter((item) => state.investigation.request.assignments[item.id] !== entry.plan.assignments[item.id]);
    if (changedItems.length) {
      const details = document.createElement("details"), summary = document.createElement("summary"), list = document.createElement("ul");
      details.className = "investigation-store-changes"; summary.textContent = "Store changes (" + changedItems.length + ")";
      changedItems.forEach((item) => { const line = document.createElement("li"); line.textContent = item.name + ": " + storeName(state.investigation.request.assignments[item.id]) + " → " + storeName(entry.plan.assignments[item.id]); list.append(line); });
      details.append(summary, list); card.append(details);
    }
    button.type = "button"; button.className = "button secondary"; button.dataset.investigationApply = entry.id; button.textContent = "Use this draft";
    button.addEventListener("click", () => applyInvestigation(entry.id)); card.append(button); return card;
  }
  function renderInvestigationOptions(preferred = []) {
    const entries = state.investigation.entries, first = entries.filter((entry) => preferred.includes(entry.id));
    const featured = (first.length ? first : entries).slice(0, 3), rest = entries.filter((entry) => !featured.includes(entry));
    $("investigation-alternatives").replaceChildren(...featured.map(investigationCard));
    $("investigation-more-list").replaceChildren(...rest.map(investigationCard)); $("investigation-more").hidden = !rest.length;
    updateInvestigationControls();
  }
  function renderInvestigationQuestion() {
    const question = state.investigation.report.question; $("investigation-answers").replaceChildren();
    $("investigation-question-wrap").hidden = !question; if (!question) return;
    $("investigation-question").textContent = question.text;
    const answer = (id, label, action) => { const button = document.createElement("button"); button.type = "button"; button.className = "investigation-answer"; button.dataset.investigationAnswer = id; button.textContent = label; button.setAttribute("aria-pressed", "false"); button.addEventListener("click", () => { if (!investigationUsable() || state.investigationBusy) return; $("investigation-answers").querySelectorAll("button").forEach((item) => item.setAttribute("aria-pressed", String(item === button))); action(); }); $("investigation-answers").append(button); };
    const prefer = (kind, label) => { renderInvestigationOptions(state.investigation.entries.filter((entry) => entry.kind === kind).map((entry) => entry.id)); $("investigation-status").textContent = label + " highlighted. Review every required change before using a draft."; };
    const protect = (key) => { $("investigate-protect-" + investigationLimits[key][0]).checked = true; investigationSettingChanged(); runInvestigation(); };
    if (question.id === "money_or_time") { answer("more-time", "More time", () => prefer("time", "Time changes")); answer("more-budget", "A higher budget", () => prefer("budget", "Budget changes")); }
    else if (question.id === "extra_stop") { answer("extra-stop", "Compare an extra stop", () => prefer("stops", "Store-limit changes")); answer("protect-stops", "Check again with store limit fixed", () => protect("max_stores")); }
    else if (question.id === "transport") { answer("other-transport", "Compare other transport", () => prefer("transport", "Transport changes")); answer("protect-travel", "Check again with travel mode fixed", () => protect("travel_mode")); }
    else if (question.id === "protect_one") Object.entries(investigationLimits).forEach(([key, names]) => answer("protect-" + names[0], "Check again with " + names[1].toLowerCase() + " fixed", () => protect(key)));
    else if (question.id === "cost_or_time") [["lower-cost", "Spend less", "total_cost_cents"], ["less-time", "Finish sooner", "total_minutes"]].forEach(([id, label, metric]) => answer(id, label, () => { const ranked = [...state.investigation.entries].sort((a, b) => a.plan[metric] - b.plan[metric]); renderInvestigationOptions(ranked.length ? [ranked[0].id] : []); $("investigation-status").textContent = "Your preferred tradeoff is highlighted. Your draft is unchanged."; }));
  }
  function renderInvestigation() {
    const value = state.investigation, report = value.report;
    $("investigation-report").hidden = false;
    $("investigation-mode").textContent = report.mode === "langchain" ? "AI-prioritized check" : "Calculated check";
    $("investigation-explanation").textContent = report.explanation;
    renderInvestigationQuestion(); renderInvestigationOptions(Array.isArray(report.highlighted_alternative_ids) ? report.highlighted_alternative_ids : []);
    $("investigation-more").open = false; $("investigation-evidence").open = false; $("investigation-evidence-list").replaceChildren();
    report.evidence.forEach((entry) => { const line = document.createElement("p"); line.textContent = entry.text; $("investigation-evidence-list").append(line); });
    const minima = report.minimum_requirements || {};
    [["minimum_budget_cents", "Minimum cost with time, stops and travel fixed: ", money], ["minimum_time_minutes", "Modeled minimum time with budget, stops and travel fixed: ", minutes]].forEach(([key, label, format]) => { if (Number.isFinite(minima[key])) { const line = document.createElement("p"); line.textContent = label + format(minima[key]) + ". These facts alone do not authorize a change."; $("investigation-evidence-list").append(line); } });
    updateInvestigationControls();
  }
  async function refreshInvestigationAnchor() {
    const session = await api("/sessions/" + encodeURIComponent(state.session.id));
    if (!Array.isArray(session.choices) || session.choices.some((choice) => !choice || typeof choice.id !== "string")) throw new Error("The latest saved choice could not be verified.");
    return session.choices;
  }
  async function runInvestigation(event) {
    event?.preventDefault();
    if (!chatAllowed() || state.investigationBusy || state.saving) return;
    if (!complete()) { $("investigation-status").textContent = "Choose a store for every item before checking possible changes."; return; }
    const epoch = state.epoch, sequence = ++state.investigationSequence, revision = state.investigationRevision, body = investigationBody();
    state.investigationBusy = true; if (state.investigation) state.investigation.invalidated = true;
    $("investigation-status").textContent = "Checking the complete basket against your rules…"; updateInvestigationControls();
    let key, pending;
    try {
      if (state.investigationNeedsSync) {
        const choices = await refreshInvestigationAnchor(); if (epoch !== state.epoch || sequence !== state.investigationSequence) return;
        state.choices = choices; state.investigationNeedsSync = false; renderProgress();
      }
      key = investigationKey(body); pending = storageRead(INVESTIGATION_PENDING_KEY, sessionStorage) || {};
      if (!pending[key]) { pending = Object.fromEntries(Object.entries(pending).slice(-9)); pending[key] = uuid(); storageWrite(INVESTIGATION_PENDING_KEY, pending, sessionStorage); }
      const requestId = pending[key];
      const result = await api("/sessions/" + encodeURIComponent(state.session.id) + "/investigate", { method: "POST", body: { ...body, request_id: requestId } });
      if (epoch !== state.epoch || sequence !== state.investigationSequence) return;
      const entries = checkedInvestigation(result, body, requestId);
      const changed = revision !== state.investigationRevision || key !== investigationKey() || result.stale || result.anchor_choice_id !== state.choices.at(-1)?.id;
      state.investigation = { report: result, entries, request: body, key, invalidated: changed };
      state.investigationNeedsSync = result.stale || result.anchor_choice_id !== state.choices.at(-1)?.id;
      delete pending[key]; storageWrite(INVESTIGATION_PENDING_KEY, pending, sessionStorage);
      renderInvestigation();
      $("investigation-status").textContent = changed ? "This report was superseded by a draft, setting, or saved-choice change. Check again before applying." : result.mode === "langchain" ? "AI prioritized these calculated options. Your draft is unchanged." : "Calculated from your explicit controls. Your draft is unchanged.";
    } catch (error) {
      if (epoch === state.epoch && sequence === state.investigationSequence) $("investigation-status").textContent = error.message + " Retry the same check safely; your draft is unchanged.";
    } finally { if (epoch === state.epoch && sequence === state.investigationSequence) { state.investigationBusy = false; updateInvestigationControls(); } }
  }
  async function applyInvestigation(id) {
    if (!investigationUsable() || state.investigationBusy) return;
    const value = state.investigation, entry = value.entries.find((item) => item.id === id), epoch = state.epoch, revision = state.investigationRevision, sequence = ++state.investigationSequence;
    if (!entry || !validInvestigationPlan(entry.plan, entry.constraints, value.request, value.report)) return;
    state.investigationBusy = true; $("investigation-status").textContent = "Confirming the latest saved choice before updating your draft…"; updateInvestigationControls();
    try {
      const choices = await refreshInvestigationAnchor();
      if (epoch !== state.epoch || sequence !== state.investigationSequence) return;
      if (choices.at(-1)?.id !== value.report.anchor_choice_id) { state.choices = choices; invalidateInvestigation("A newer choice was saved. Check again before applying this proposal."); renderProgress(); return; }
      if (revision !== state.investigationRevision || !investigationUsable()) { invalidateInvestigation("Your draft or settings changed. Check again before applying."); return; }
      state.constraints = copy(entry.constraints); state.assignments = copy(entry.plan.assignments); setFields("active", state.constraints);
      $("limits-summary").textContent = money(state.constraints.budget_cents) + " · " + minutes(state.constraints.time_budget_minutes);
      draftChanged(); recommendations();
      notice("session-notice", "Proposal applied to your draft. Review the totals, then save your revised choice separately.");
      $("investigation-status").textContent = "Applied to your draft only. Your saved choices have not changed.";
      $("save-status").textContent = "Proposal applied. Your revised choice has not been saved.";
      $("plan-heading").scrollIntoView?.({ behavior: "smooth", block: "center" });
    } catch (error) { if (epoch === state.epoch && sequence === state.investigationSequence) $("investigation-status").textContent = error.message + " Your draft is unchanged. Retry using the proposal when ready."; }
    finally { if (epoch === state.epoch && sequence === state.investigationSequence) { state.investigationBusy = false; updateInvestigationControls(); } }
  }
  function validConstraints(value) {
    return value && Number.isSafeInteger(value.budget_cents) && value.budget_cents >= 1 && value.budget_cents <= 1000000 && Number.isFinite(value.time_budget_minutes) && value.time_budget_minutes > 0 && value.time_budget_minutes <= 1440 && ["walk", "drive"].includes(value.travel_mode) && [1, 2].includes(value.max_stores);
  }
  function chatAllowed() { return state.session && state.choices.some((choice) => choice.stage === "initial"); }
  function chatBusy(busy) {
    state.chatBusy = busy;
    $("chat-send").disabled = busy;
    $("chat-input").disabled = busy;
    $("chat-send").textContent = busy ? "Working on it…" : "Ask the guide →";
    document.querySelectorAll("[data-chat-message]").forEach((button) => button.disabled = busy);
  }
  function renderChatHistory() {
    $("chat-history").replaceChildren();
    if (!state.chatMessages.length) {
      const empty = document.createElement("p"); empty.className = "chat-empty";
      empty.textContent = "What would make this shop work better for you? Try one of the quick requests below.";
      $("chat-history").append(empty);
    }
    state.chatMessages.slice(-40).forEach((message) => {
      const bubble = document.createElement("div");
      bubble.className = "chat-message " + (message.role === "user" ? "user" : "assistant");
      const role = document.createElement("span"); role.className = "chat-role"; role.textContent = message.role === "user" ? "You" : "Shopping guide";
      const content = document.createElement("span"); content.textContent = message.content;
      bubble.append(role, content); $("chat-history").append(bubble);
    });
    $("chat-history").scrollTop = $("chat-history").scrollHeight;
  }
  function renderChatProposal(constraints, plans = [], { restored = false } = {}) {
    state.chatProposal = validConstraints(constraints) ? { constraints: copy(constraints), plans: copy(plans) } : null;
    $("chat-proposal").hidden = !state.chatProposal;
    $("chat-plan-options").replaceChildren();
    if (!state.chatProposal) return;
    $("chat-proposal-summary").textContent = (restored ? "Restored guide limits: " : "") + money(constraints.budget_cents) + " budget · " + minutes(constraints.time_budget_minutes) + " whole trip · " + (constraints.travel_mode === "walk" ? "walking" : "driving") + " · " + constraints.max_stores + (constraints.max_stores === 1 ? " store maximum" : " stores maximum");
    plans.forEach((plan) => {
      const button = document.createElement("button"); button.type = "button"; button.className = "chat-plan-option";
      const text = document.createElement("span"), title = document.createElement("strong"), detail = document.createElement("small"), action = document.createElement("span");
      title.textContent = (plan.label || "Suggested plan") + " · " + money(plan.total_cost_cents) + " · " + minutes(plan.total_minutes);
      detail.textContent = plan.route.map(storeName).join(" → "); action.textContent = "Apply plan →";
      text.append(title, detail); button.append(text, action);
      button.addEventListener("click", () => applyChatProposal(plan.assignments));
      $("chat-plan-options").append(button);
    });
  }
  function applyChatProposal(assignments) {
    if (!state.chatProposal || !chatAllowed()) return;
    state.constraints = copy(state.chatProposal.constraints);
    if (assignments) state.assignments = copy(assignments);
    setFields("active", state.constraints);
    $("limits-summary").textContent = money(state.constraints.budget_cents) + " · " + minutes(state.constraints.time_budget_minutes);
    notice("session-notice", "Suggested " + (assignments ? "plan and limits" : "limits") + " applied to your draft. Your saved choices have not changed.");
    draftChanged(); recommendations();
    $("chat-status").textContent = "Applied to your draft. Review the totals, then save a revised choice when you are ready.";
    $("save-status").textContent = "Suggestion applied. Your revised choice has not been saved.";
    $("plan-heading").scrollIntoView?.({ behavior: "smooth", block: "center" });
  }
  async function loadChat() {
    if (!chatAllowed()) { $("chat-section").hidden = true; return; }
    const epoch = state.epoch, sequence = ++state.chatSequence;
    $("chat-section").hidden = false; chatBusy(true); $("chat-status").textContent = "Loading your conversation…"; $("chat-retry-history").hidden = true;
    try {
      const result = await api("/sessions/" + encodeURIComponent(state.session.id) + "/chat");
      if (epoch !== state.epoch || sequence !== state.chatSequence) return;
      if (!Array.isArray(result.messages) || result.messages.some((m) => !["user", "assistant"].includes(m.role) || typeof m.content !== "string")) throw new Error("Your conversation could not be read. You can still use the shopping controls.");
      state.chatMessages = result.messages.slice(-40); renderChatHistory();
      const previousSuggestion = state.chatMessages.some((message) => message.role === "assistant") && validConstraints(result.constraints);
      renderChatProposal(previousSuggestion ? result.constraints : null, [], { restored: true });
      $("chat-status").textContent = "";
    } catch (error) {
      if (epoch !== state.epoch || sequence !== state.chatSequence) return;
      renderChatHistory(); $("chat-status").textContent = error.message + " The basket controls still work."; $("chat-retry-history").hidden = false;
    } finally { if (epoch === state.epoch && sequence === state.chatSequence) chatBusy(false); }
  }
  async function sendChat(event) {
    event?.preventDefault();
    if (!chatAllowed() || state.chatBusy) return;
    const message = $("chat-input").value.trim();
    if (!message || message.length > 1500) { $("chat-status").textContent = "Enter a request between 1 and 1,500 characters."; return; }
    const epoch = state.epoch, sequence = ++state.chatSequence;
    const key = JSON.stringify([state.session.id, message]);
    state.chatPending = storageRead(CHAT_PENDING_KEY, sessionStorage) || {};
    const wasPending = Boolean(state.chatPending[key]);
    if (!state.chatPending[key]) {
      const entries = Object.entries(state.chatPending).slice(-9);
      state.chatPending = Object.fromEntries(entries); state.chatPending[key] = uuid();
      storageWrite(CHAT_PENDING_KEY, state.chatPending, sessionStorage);
    }
    const requestId = state.chatPending[key];
    chatBusy(true); $("chat-status").textContent = "Checking the tradeoffs…"; $("chat-retry-history").hidden = true;
    try {
      const result = await api("/sessions/" + encodeURIComponent(state.session.id) + "/chat", { method: "POST", body: { request_id: requestId, message } });
      if (epoch !== state.epoch || sequence !== state.chatSequence) return;
      if (typeof result.reply !== "string" || !validConstraints(result.constraints) || !["ok", "clarification", "no_feasible_plan"].includes(result.status) || !Array.isArray(result.plans) || result.plans.some((plan) => !validEvaluation(plan) || !plan.feasible || constraintsKey(plan.constraints) !== constraintsKey(result.constraints))) throw new Error("We could not verify that suggestion. Please retry your request.");
      const tail = state.chatMessages.slice(-2);
      const alreadyRestored = wasPending && tail.length === 2 && tail[0].role === "user" && tail[0].content === message && tail[1].role === "assistant" && tail[1].content === result.reply;
      if (!alreadyRestored) state.chatMessages.push({ role: "user", content: message }, { role: "assistant", content: result.reply });
      state.chatMessages = state.chatMessages.slice(-40); renderChatHistory();
      renderChatProposal(result.status === "clarification" ? null : result.constraints, result.plans);
      $("chat-mode").textContent = result.mode === "offline" ? "Rule-based help" : result.mode === "fallback" ? "Quick help" : "AI-assisted guide";
      $("chat-status").textContent = result.status === "clarification" ? "Try a specific money, time, travel, or stop limit. Your draft is unchanged." : result.status === "no_feasible_plan" ? "No plan fits these suggested limits. Your draft is unchanged; adjust your request or apply the limits to explore." : result.notice || "Review the suggestion before applying it. Your draft is unchanged.";
      delete state.chatPending[key]; storageWrite(CHAT_PENDING_KEY, state.chatPending, sessionStorage);
      $("chat-input").value = "";
    } catch (error) {
      if (epoch === state.epoch && sequence === state.chatSequence) { $("chat-status").textContent = error.message + " Send the same request to retry safely; your draft is unchanged."; $("chat-retry-history").hidden = false; }
    } finally { if (epoch === state.epoch && sequence === state.chatSequence) chatBusy(false); }
  }
  function applyLimits(event) {
    event.preventDefault();
    try {
      const constraints = readFields("active");
      state.constraints = constraints;
      $("limits-summary").textContent = money(constraints.budget_cents) + " · " + minutes(constraints.time_budget_minutes);
      notice("session-notice", "Limits updated. Your previous saved choices keep their original limits.");
      draftChanged(); if (state.choices.some((c) => c.stage === "initial")) recommendations();
    } catch (error) { notice("session-notice", error.message); }
  }
  function newSession() {
    if (state.saving || state.starting) return;
    if (Object.keys(state.assignments).length && !window.confirm("Start a new challenge? Saved choices stay in the experiment; this browser will leave the current session.")) return;
    state.epoch++; state.evalSequence++; state.recommendationSequence++; state.chatSequence++; state.investigationSequence++;
    state.investigation = null; state.investigationBusy = false; state.investigationNeedsSync = false; state.setupInitialized = false;
    state.walletProfileId = ""; renderWalletSelection();
    state.session = null; state.token = ""; state.assignments = {}; state.choices = []; state.evaluation = null; state.constraints = null; state.evaluating = false; state.saving = false; state.lastSavedKey = "";
    storageWrite(STORE_KEY, null); clearPending(); state.chatMessages = []; state.chatProposal = null; state.chatPending = {}; chatBusy(false); $("chat-input").value = ""; $("chat-proposal").hidden = true; $("chat-history").replaceChildren(); $("chat-status").textContent = "";
    $("challenge").hidden = true; $("setup").hidden = false; $("how-link").hidden = false; $("saved-result").hidden = true; $("consent").checked = false;
    notice("session-notice", ""); renderScenarios(); $("hero-title").scrollIntoView?.({ behavior: "smooth" });
  }
  async function load() {
    $("loading").hidden = false; $("reload").hidden = true; notice("global-error", "");
    state.pending = storageRead(PENDING_KEY, sessionStorage) || {};
    try {
      const result = await api("/scenarios", { token: "" });
      if (!Array.isArray(result.scenarios) || !result.scenarios.length) throw new Error("No shopping scenarios are available yet. Please try again shortly.");
      state.scenarios = result.scenarios; renderScenarios(); loadWallets();
      const stored = storageRead(STORE_KEY);
      if (stored?.id && stored?.token) {
        try {
          const session = await api("/sessions/" + encodeURIComponent(stored.id), { token: stored.token });
          state.session = session; state.token = stored.token; state.scenario = session.scenario;
          state.choices = session.choices || []; const latest = state.choices.at(-1)?.evaluation;
          state.constraints = stored.constraints || latest?.constraints || session.constraints;
          state.assignments = stored.assignments || latest?.assignments || {};
          notice("session-notice", "Welcome back. Your challenge has been restored." + (Boolean(session.is_demo) !== requestedDemo ? " This saved session is in " + (session.is_demo ? "demo" : "participant") + " mode. Start a new challenge to use the mode in this link." : session.is_demo ? " Demo choices stay separate from participant results." : "")); renderChallenge(); return;
        } catch (error) {
          if (![401, 403, 404].includes(error.status)) throw error;
          storageWrite(STORE_KEY, null); clearPending(); notice("global-error", "Your previous session is no longer available in this browser. You can start a new challenge.");
        }
      }
      $("setup").hidden = false; $("challenge").hidden = true;
    } catch (error) { notice("global-error", error.message); $("reload").hidden = false; }
    finally { $("loading").hidden = true; }
  }
  $("investigation-form").addEventListener("submit", runInvestigation);
  $("investigation-form").querySelectorAll("input").forEach((input) => input.addEventListener("change", investigationSettingChanged));
  $("wallet-profile").addEventListener("change", () => {
    if (state.starting) { renderWalletSelection(); return; }
    state.walletProfileId = state.wallets.some((item) => item.id === $("wallet-profile").value) ? $("wallet-profile").value : "";
    renderWalletSelection();
  });
  $("wallet-retry").addEventListener("click", loadWallets);
  $("start-form").addEventListener("submit", start);
  $("limits-form").addEventListener("submit", applyLimits);
  $("save-choice").addEventListener("click", saveChoice);
  $("new-session").addEventListener("click", newSession);
  $("refresh-recommendations").addEventListener("click", recommendations);
  $("retry-evaluation").addEventListener("click", evaluate);
  $("reload").addEventListener("click", load);
  $("chat-form").addEventListener("submit", sendChat);
  $("chat-apply-limits").addEventListener("click", () => applyChatProposal());
  $("chat-retry-history").addEventListener("click", loadChat);
  document.querySelectorAll("[data-chat-message]").forEach((button) => button.addEventListener("click", () => { $("chat-input").value = button.dataset.chatMessage; sendChat(); }));
  if (requestedDemo) {
    const banner = document.createElement("p"); banner.className = "notice"; banner.id = "demo-notice"; banner.textContent = "Demo mode — try the full challenge. These choices are kept separate from participant results."; $("setup").prepend(banner);
    $("consent").nextElementSibling.textContent = "I agree to save these anonymous demo choices and optional guide conversation. They will be labeled as demonstration data, not participant observations.";
  }
  const analyticsUrl = new URL(location.href); analyticsUrl.port = "5184"; analyticsUrl.pathname = "/"; analyticsUrl.search = ""; analyticsUrl.hash = ""; $("analytics-link").href = analyticsUrl.href;
  load();
})();
