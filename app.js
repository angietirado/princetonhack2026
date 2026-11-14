(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const money = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
  const shortMoney = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(cents / 100);
  const label = (value) => String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const dateLabel = (value) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(value.slice(0, 10) + "T00:00:00Z")) : "No activity";
  const colors = ["#8e74e8", "#f2a65a", "#6ca4e5", "#79bfa8", "#d898ad", "#bcc8c0"];
  const pageSize = 25;
  let token = "";
  let data = null;
  let statusFilter = "all";
  let page = 0;
  let range = {};
  let appliedPreset = "all";
  let sequence = 0;
  let controller = null;
  let toastTimer = null;
  let askSequence = 0;
  let askController = null;
  let askStatus = null;
  let askStatusLoading = false;
  let askPending = false;
  let askTimer = null;
  let dashboardLoading = false;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function message(text) {
    $("toast").textContent = text;
    $("toast").classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("toast").classList.remove("show"), 2400);
  }
  function showView(view) {
    document.querySelectorAll(".page").forEach((item) => item.classList.toggle("hidden", item.id !== view + "-view"));
    document.querySelectorAll(".nav-item").forEach((item) => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    const active = document.querySelector('.nav-item[data-view="' + view + '"]');
    $("page-title").textContent = active ? active.getAttribute("aria-label") : "Overview";
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (view === "ask") void loadAskStatus();
  }
  document.querySelectorAll("[data-view], [data-view-link]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view || button.dataset.viewLink));
  });
  function clearData() {
    data = null;
    resetAsk();
    $("dashboard-data").classList.add("hidden");
    for (const id of ["balance-value", "balance-foot", "spend-value", "spend-foot", "credit-value", "credit-foot", "gross-total", "refund-note", "largest-category-title", "largest-category-copy", "largest-category-share", "refund-insight", "pending-insight", "uncategorized-insight", "report-total", "report-period", "connection-source", "connection-customer", "connection-time", "transaction-count", "page-count", "period-caption", "overview-subheading"]) $(id).textContent = "";
    for (const id of ["category-legend", "monthly-chart", "recent-activity", "coverage-details", "transaction-rows", "recurring-list", "report-categories", "accounts-list"]) $(id).replaceChildren();
    $("category-donut").style.background = "#e4e9e5";
    $("category-donut").setAttribute("aria-label", "No expense data loaded");
    $("transaction-badge").textContent = "—";
    $("transaction-search").value = "";
  }
  function lock() {
    sequence += 1;
    controller?.abort();
    token = "";
    dashboardLoading = false;
    clearData();
    $("app-token").value = "";
    $("access-error").textContent = "";
    $("access-panel").classList.remove("hidden");
    $("status-banner").classList.add("hidden");
    $("lock-button").classList.add("hidden");
    $("source-badge").textContent = "Locked";
    $("sync-title").textContent = "Dashboard locked";
    $("sync-account-name").textContent = "Enter your app token";
    $("sync-dot").classList.add("inactive");
    $("refresh-button").disabled = false;
    $("app-token").focus();
  }
  $("lock-button").addEventListener("click", lock);

  async function refresh() {
    const requestId = ++sequence;
    dashboardLoading = true;
    resetAsk();
    controller?.abort();
    controller = new AbortController();
    $("refresh-button").disabled = true;
    $("unlock-button").disabled = true;
    $("dashboard-data").classList.add("hidden");
    $("status-banner").textContent = "Loading your spending…";
    $("status-banner").className = "status-banner";
    $("access-error").textContent = "";
    $("sync-title").textContent = "Updating";
    $("sync-dot").classList.add("inactive");
    try {
      const query = new URLSearchParams(range);
      const response = await fetch("/api/v1/dashboard" + (query.size ? "?" + query : ""), {
        headers: token ? { Authorization: "Bearer " + token } : {},
        cache: "no-store",
        signal: controller.signal,
      });
      if (requestId !== sequence) return;
      if (response.status === 401) {
        const rejected = Boolean(token);
        lock();
        if (rejected) $("access-error").textContent = "That access token was not accepted. Try again.";
        return;
      }
      if (!response.ok) throw new Error("The data connection is unavailable. Check the backend and try Refresh.");
      const payload = await response.json();
      if (requestId !== sequence) return;
      if (!payload.summary || !Number.isSafeInteger(payload.summary.net_spending_cents) ||
          !["accounts", "transactions", "monthly_spending", "expense_categories", "recurring"].every((key) => Array.isArray(payload[key]))) {
        throw new Error("The backend returned an unexpected response. Check that both services are up to date.");
      }
      data = payload;
      updateAskControls();
      page = 0;
      $("dashboard-data").classList.remove("hidden");
      render();
      $("access-panel").classList.add("hidden");
      $("dashboard-data").classList.remove("hidden");
      $("status-banner").classList.add("hidden");
      $("lock-button").classList.toggle("hidden", !token);
    } catch (error) {
      if (requestId !== sequence || error.name === "AbortError") return;
      clearData();
      $("status-banner").textContent = error instanceof Error && error.message.startsWith("The ") ? error.message : "Unable to reach the backend. Try Refresh when the connection is available.";
      $("status-banner").className = "status-banner error";
      $("sync-title").textContent = "Connection unavailable";
      $("sync-account-name").textContent = "Refresh to try again";
      $("source-badge").textContent = "Unavailable";
    } finally {
      if (requestId === sequence) {
        dashboardLoading = false;
        updateAskControls();
        $("refresh-button").disabled = false;
        $("unlock-button").disabled = false;
      }
      // lock() increments the sequence and needs a usable form after a 401.
      if (!data && $("access-panel").classList.contains("hidden") === false) $("unlock-button").disabled = false;
    }
  }
  $("refresh-button").addEventListener("click", () => void refresh());
  $("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const entered = $("app-token").value.trim();
    if (!entered) return;
    token = entered;
    $("app-token").value = "";
    void refresh();
  });

  function clearAskAnswer() {
    $("ask-result").classList.add("hidden");
    for (const id of ["ask-outcome", "ask-asked-question", "ask-answer", "ask-usage", "ask-source"]) $(id).textContent = "";
    $("ask-caveats").replaceChildren();
    $("ask-evidence").replaceChildren();
  }
  function updateAskControls() {
    const ready = Boolean(data && !dashboardLoading && askStatus?.enabled && !askStatusLoading && !askPending);
    $("ask-question").disabled = !ready;
    $("ask-submit").disabled = !ready || !$("ask-question").value.trim();
    $("ask-submit").textContent = askPending ? "Working…" : "Ask →";
    $("ask-check").disabled = !data || dashboardLoading || askPending || askStatusLoading;
    $("ask-example-spending").disabled = !ready;
    $("ask-example-housing").disabled = !ready;
    $("ask-example-housing").classList.toggle("hidden", askStatus?.costar_enabled !== true);
    $("ask-counter").textContent = $("ask-question").value.length + " / 2000 characters";
    $("ask-form").setAttribute("aria-busy", String(askPending));
  }
  function resetAsk() {
    askSequence += 1;
    askController?.abort();
    clearTimeout(askTimer);
    askController = null;
    askStatus = null;
    askStatusLoading = false;
    askPending = false;
    $("ask-question").value = "";
    $("ask-provider").textContent = "Check availability to start.";
    $("ask-state").textContent = "";
    $("ask-error").textContent = "";
    clearAskAnswer();
    updateAskControls();
  }
  function askAuthHeaders(json = false) {
    return { ...(token ? { Authorization: "Bearer " + token } : {}), ...(json ? { "Content-Type": "application/json" } : {}) };
  }
  function startAskDeadline(requestId, milliseconds, checkingStatus) {
    clearTimeout(askTimer);
    askTimer = setTimeout(() => {
      if (requestId !== askSequence) return;
      askSequence += 1;
      askController?.abort();
      askStatusLoading = false;
      askPending = false;
      $("ask-state").textContent = "";
      if (checkingStatus) $("ask-provider").textContent = "AI availability could not be checked.";
      $("ask-error").textContent = checkingStatus ? "The availability check timed out. Try checking again." : "The answer request timed out. Check the connection before submitting again.";
      updateAskControls();
    }, milliseconds);
  }
  async function loadAskStatus(force = false) {
    if (!data || dashboardLoading || askPending || askStatusLoading || (askStatus && !force)) return;
    const requestId = ++askSequence;
    askController?.abort();
    askController = new AbortController();
    askStatusLoading = true;
    askStatus = null;
    $("ask-provider").textContent = "Checking AI availability…";
    $("ask-error").textContent = "";
    updateAskControls();
    startAskDeadline(requestId, 15000, true);
    try {
      const response = await fetch("/api/v1/agent/status", { headers: askAuthHeaders(), cache: "no-store", signal: askController.signal });
      if (requestId !== askSequence) return;
      if (response.status === 401) { lock(); $("access-error").textContent = "Unlock with a valid app token to use AI."; return; }
      if (!response.ok) throw new Error("status unavailable");
      const status = await response.json();
      if (requestId !== askSequence) return;
      if (!status || typeof status.enabled !== "boolean" || typeof status.provider !== "string" || typeof status.model !== "string" || status.provider.length > 160 || status.model.length > 160) throw new Error("invalid status");
      askStatus = status;
      $("ask-provider").textContent = status.enabled ? label(status.provider) + " · " + status.model + " · Read-only tools" : "AI is disabled for this workspace. Your spending dashboard remains available.";
    } catch (error) {
      if (requestId !== askSequence || error.name === "AbortError") return;
      $("ask-provider").textContent = "AI availability could not be checked.";
      $("ask-error").textContent = "Check the backend connection, then check availability again.";
    } finally {
      if (requestId === askSequence) { clearTimeout(askTimer); askStatusLoading = false; updateAskControls(); }
    }
  }
  $("ask-check").addEventListener("click", () => void loadAskStatus(true));
  $("ask-question").addEventListener("input", () => { $("ask-error").textContent = ""; updateAskControls(); });
  $("ask-example-spending").addEventListener("click", () => {
    if (!askStatus?.enabled || askPending) return;
    const start = data?.summary.start || "[start date]";
    const end = data?.summary.end || "[end date]";
    $("ask-question").value = "Summarize my observed spending from " + start + " through " + end + " in UTC. Include refunds, exclusions, and missing-history caveats.";
    updateAskControls();
    $("ask-question").focus();
  });
  $("ask-example-housing").addEventListener("click", () => {
    if (!askStatus?.enabled || !askStatus.costar_enabled || askPending) return;
    $("ask-question").value = "Find up to 3 one-bedroom apartments in TX under $1,800 monthly asking rent. Show each full rent range and recorded availability limitations." + (askStatus.matterport_enabled ? " For one unambiguous floor plan, inspect reported room measurements and state the units and missing information." : "");
    updateAskControls();
    $("ask-question").focus();
  });
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  function validAskAnswer(value) {
    return object(value) && ["answered", "limited", "unavailable"].includes(value.status) &&
      typeof value.answer === "string" && value.answer.length <= 8000 &&
      ["provider", "model", "source", "source_hash"].every((key) => typeof value[key] === "string" && value[key].length <= 256) &&
      ["model_calls", "tool_calls"].every((key) => Number.isSafeInteger(value[key]) && value[key] >= 0 && value[key] <= 32) &&
      Array.isArray(value.caveats) && value.caveats.length <= 64 && value.caveats.every((item) => typeof item === "string" && item.length <= 2000) &&
      Array.isArray(value.evidence) && value.evidence.length <= 12 && value.evidence.every((item) => object(item) && typeof item.tool === "string" && item.tool.length <= 80 && object(item.arguments) && object(item.result)) &&
      (value.status !== "answered" || value.evidence.some((item) => item.result.status === "ok")) &&
      JSON.stringify(value).length <= 512000;
  }
  const reported = (value, unit) => typeof value === "number" && Number.isFinite(value) ? value + " " + unit : "unknown";
  function renderToolEvidence(entry) {
    const result = entry.result;
    const host = node("article", undefined, "ask-evidence-item");
    const titles = { summarize_spending: "Observed spending", get_transactions: "Transaction records", find_recurring_charges: "Recurring candidates", costar_search: "Apartment matches", inspect_floor_plan: "Room measurements" };
    host.append(node("h4", Object.hasOwn(titles, entry.tool) ? titles[entry.tool] : label(entry.tool)));
    if (result.status !== "ok") {
      host.append(node("p", "This read did not return usable evidence. No zero total or empty-market conclusion follows."));
    } else if (entry.tool === "summarize_spending" && Number.isSafeInteger(result.net_spending_cents)) {
      host.append(node("p", "Observed net spending: " + money(result.net_spending_cents) + ". History completeness is unknown."));
    } else if (entry.tool === "get_transactions") {
      host.append(node("p", result.returned_rows + " of " + result.total_rows + " matching observed records returned. Signed contributions and excluded records are in the details."));
    } else if (entry.tool === "find_recurring_charges") {
      host.append(node("p", "Pattern candidates only; these do not confirm subscriptions, savings, or a next bill."));
    } else if (entry.tool === "costar_search" && Array.isArray(result.results)) {
      host.append(node("p", result.results.length + " recorded matches shown. Availability is not live; the local catalog is not the whole market."));
      if (result.results.length > 20) host.append(node("p", "The first 20 matches are summarized below; all returned records remain in the exact evidence."));
      for (const row of result.results.slice(0, 20)) {
        if (!object(row)) continue;
        const listing = node("div", undefined, "ask-record");
        listing.append(node("strong", String(row.property_name ?? "Property") + " · " + String(row.floor_plan_name ?? "Floor plan")));
        const rent = row.asking_rent;
        if (object(rent) && rent.currency === "USD" && rent.period === "month" && rent.basis === "per_unit" && Number.isSafeInteger(rent.minimum)) {
          const dollars = (value) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
          listing.append(node("p", dollars(rent.minimum) + " – " + (Number.isSafeInteger(rent.maximum) ? dollars(rent.maximum) : "upper bound unknown") + " per month, per unit."));
          if (row.price_range_within_budget !== true) listing.append(node("p", "The full range is not confirmed within the requested budget."));
        } else listing.append(node("p", "Rent units or range are unavailable; see the exact evidence."));
        host.append(listing);
      }
    } else if (entry.tool === "inspect_floor_plan" && Array.isArray(result.rooms)) {
      host.append(node("p", "Model " + String(result.model_id ?? "unknown") + ". Missing or zero measurements do not establish room size. Furniture fit and clear routes were not verified."));
      if (result.rooms_truncated) host.append(node("p", "Only " + result.rooms_returned + " of " + result.room_count + " rooms are displayed."));
      if (result.rooms.length > 20) host.append(node("p", "The first 20 returned rooms are summarized below; review the exact evidence for the remaining records."));
      for (const room of result.rooms.slice(0, 20)) {
        if (!object(room)) continue;
        const record = node("div", undefined, "ask-record");
        record.append(node("strong", String(room.label || room.room_id || "Room")));
        const estimate = room.dimension_estimates;
        record.append(node("p", object(estimate) ? "Dimension estimate — area: " + reported(estimate.area_square_meters, "square meters") + "; width: " + reported(estimate.width_meters, "meters") + "; depth: " + reported(estimate.depth_meters, "meters") + "." : "Dimension estimates unavailable."));
        const legacy = room.legacy_dimensions;
        if (object(legacy)) record.append(node("p", "Legacy estimate — area: " + reported(legacy.area_floor, String(legacy.area_unit).replaceAll("_", " ")) + "; width: " + reported(legacy.width, String(legacy.length_unit)) + "; depth: " + reported(legacy.depth, String(legacy.length_unit)) + "."));
        host.append(record);
      }
    }
    if (Array.isArray(result.limitations)) {
      const list = node("ul", undefined, "ask-caveats");
      for (const item of result.limitations) if (typeof item === "string") list.append(node("li", item));
      host.append(list);
    }
    const details = node("details");
    details.append(node("summary", "Exact tool evidence"), node("pre", JSON.stringify(entry, null, 2)));
    host.append(details);
    return host;
  }
  function renderAskAnswer(result, question) {
    $("ask-outcome").textContent = { answered: "Answered", limited: "Request limit reached", unavailable: "Answer unavailable" }[result.status];
    $("ask-answer-title").textContent = result.status === "answered" ? "Answer" : "What was completed";
    $("ask-asked-question").textContent = question;
    $("ask-answer").textContent = result.answer;
    $("ask-usage").textContent = label(result.provider) + " · " + result.model + " · " + result.model_calls + " model calls · " + result.tool_calls + " tool reads";
    $("ask-source").textContent = "Spending source: " + result.source + " · Revision: " + result.source_hash;
    for (const caveat of result.caveats) $("ask-caveats").append(node("li", caveat));
    for (const entry of result.evidence) $("ask-evidence").append(renderToolEvidence(entry));
    if (!result.evidence.length) $("ask-evidence").append(node("p", "No tool evidence was returned."));
    $("ask-result").classList.remove("hidden");
  }
  $("ask-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!data || dashboardLoading || !askStatus?.enabled || askStatusLoading || askPending) return;
    const question = $("ask-question").value.trim();
    if (!question || $("ask-question").value.length > 2000) { $("ask-error").textContent = "Enter a question of 1 to 2000 characters."; return; }
    const requestId = ++askSequence;
    askController?.abort();
    askController = new AbortController();
    askPending = true;
    clearAskAnswer();
    $("ask-error").textContent = "";
    $("ask-state").textContent = "Reading evidence and preparing an answer…";
    updateAskControls();
    startAskDeadline(requestId, 130000, false);
    try {
      const response = await fetch("/api/v1/agent/answer", { method: "POST", headers: askAuthHeaders(true), body: JSON.stringify({ question }), cache: "no-store", signal: askController.signal });
      if (requestId !== askSequence) return;
      if (response.status === 401) { lock(); $("access-error").textContent = "Your app token was not accepted. Unlock again to continue."; return; }
      if (!response.ok) {
        $("ask-error").textContent = ({ 429: "An AI request is already running. Wait briefly, then submit again.", 503: "The AI service is unavailable. Check availability before trying again.", 504: "The request reached its time limit. Try a narrower question." })[response.status] || "The backend could not answer this question. Check availability and try again.";
        return;
      }
      const result = await response.json();
      if (requestId !== askSequence) return;
      if (!validAskAnswer(result)) throw new Error("invalid answer");
      renderAskAnswer(result, question);
    } catch (error) {
      if (requestId !== askSequence || error.name === "AbortError") return;
      $("ask-error").textContent = "A valid answer could not be received. Check the connection and try again.";
    } finally {
      if (requestId === askSequence) { clearTimeout(askTimer); askPending = false; $("ask-state").textContent = ""; updateAskControls(); }
    }
  });

  function renderCategories() {
    const all = data.expense_categories;
    const categories = all.length > 6 ? all.slice(0, 5).concat([{ category: "Other categories", amount_cents: all.slice(5).reduce((sum, item) => sum + item.amount_cents, 0) }]) : all;
    const legend = $("category-legend");
    legend.replaceChildren();
    let position = 0;
    const segments = [];
    categories.forEach((item, index) => {
      const percent = data.gross_spending_cents ? item.amount_cents / data.gross_spending_cents * 100 : 0;
      const color = colors[index % colors.length];
      segments.push(color + " " + position + "% " + (position + percent) + "%");
      position += percent;
      const row = node("div");
      const swatch = node("span", "", "legend-swatch");
      swatch.style.backgroundColor = color;
      const amount = node("strong", money(item.amount_cents));
      amount.append(node("small", percent.toFixed(1) + "%"));
      row.append(swatch, node("span", label(item.category)), amount);
      legend.append(row);
    });
    if (!categories.length) legend.append(node("p", "No completed expenses in this period.", "empty-state"));
    $("category-donut").style.background = data.gross_spending_cents > 0 ? "conic-gradient(" + segments.join(",") + ")" : "#e4e9e5";
    $("category-donut").setAttribute("aria-label", "Completed expenses before refunds: " + money(data.gross_spending_cents));
    $("gross-total").textContent = shortMoney(data.gross_spending_cents);
    $("gross-total").title = money(data.gross_spending_cents);
    $("refund-note").textContent = money(data.gross_spending_cents) + " in expenses − " + money(data.refunds_cents) + " in refunds = " + money(data.summary.net_spending_cents) + " net spending.";
  }
  function svgNode(tag, attrs, text) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function renderMonths() {
    const host = $("monthly-chart");
    host.replaceChildren();
    const months = data.monthly_spending;
    if (!months.length) {
      host.append(node("p", "No transaction activity in this period.", "empty-state"));
      return;
    }
    const width = Math.max(host.clientWidth || 420, months.length * 65 + 55);
    const high = Math.max(0, ...months.map((row) => row.net_spending_cents));
    const low = Math.min(0, ...months.map((row) => row.net_spending_cents));
    const span = high - low || 1;
    const y = (value) => 24 + (high - value) / span * 130;
    const zero = y(0);
    const step = (width - 65) / months.length;
    const svg = svgNode("svg", { viewBox: "0 0 " + width + " 200", role: "img", "aria-label": "Monthly net spending in USD. " + months.map((row) => row.month + ": " + money(row.net_spending_cents)).join("; ") });
    svg.style.minWidth = width + "px";
    svg.append(svgNode("line", { x1: 48, x2: width - 8, y1: zero, y2: zero, stroke: "#dce6df" }));
    svg.append(svgNode("text", { x: 0, y: 28, class: "chart-axis" }, shortMoney(high)));
    if (low < 0) svg.append(svgNode("text", { x: 0, y: 157, class: "chart-axis" }, shortMoney(low)));
    months.forEach((row, index) => {
      const x = 55 + step * index + step * 0.15;
      const valueY = y(row.net_spending_cents);
      const bar = svgNode("rect", { x, y: Math.min(zero, valueY), width: Math.min(48, step * 0.7), height: Math.abs(zero - valueY), rx: 4, fill: row.net_spending_cents < 0 ? "#6ca4e5" : "#4eb28a" });
      bar.append(svgNode("title", {}, row.month + " · " + money(row.net_spending_cents)));
      svg.append(bar);
      svg.append(svgNode("text", { x, y: row.net_spending_cents < 0 ? valueY + 15 : valueY - 7, class: "chart-value" }, shortMoney(row.net_spending_cents)));
      const monthName = new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(new Date(row.month + "-01T00:00:00Z"));
      svg.append(svgNode("text", { x, y: 190, class: "chart-axis" }, monthName));
    });
    host.append(svg);
  }
  window.addEventListener("resize", () => { if (data) renderMonths(); });
  function transactionName(row) {
    return row.merchant !== "Unknown merchant" ? row.merchant : row.description || label(row.kind);
  }
  function statusBadge(status) {
    return node("span", label(status), "status " + (status === "completed" ? "categorized" : status === "pending" ? "needs-context" : "neutral-status"));
  }
  function renderActivity() {
    const host = $("recent-activity");
    host.replaceChildren();
    data.transactions.slice(0, 5).forEach((tx) => {
      const row = node("div", undefined, "activity-row");
      const info = node("div", undefined, "activity-info");
      info.append(node("strong", transactionName(tx)), node("span", dateLabel(tx.occurred_at) + " · " + label(tx.kind)));
      row.append(node("div", transactionName(tx).slice(0, 1).toUpperCase(), "merchant-icon whole"), info, node("strong", money(tx.amount_cents), "amount"), statusBadge(tx.status));
      host.append(row);
    });
    if (!data.transactions.length) host.append(node("p", "No transactions in this period.", "empty-state"));
  }
  function filteredTransactions() {
    if (!data) return [];
    const search = $("transaction-search").value.trim().toLowerCase();
    return data.transactions.filter((row) => {
      const statusMatches = statusFilter === "all" || (statusFilter === "other" ? !["completed", "pending"].includes(row.status) : row.status === statusFilter);
      const text = [row.merchant, row.description, row.category, label(row.kind), row.id].join(" ").toLowerCase();
      return statusMatches && (!search || text.includes(search));
    });
  }
  function renderTransactions() {
    if (!data) return;
    const rows = filteredTransactions();
    const pageTotal = Math.max(1, Math.ceil(rows.length / pageSize));
    page = Math.min(page, pageTotal - 1);
    const host = $("transaction-rows");
    host.replaceChildren();
    rows.slice(page * pageSize, (page + 1) * pageSize).forEach((tx) => {
      const row = node("tr");
      row.dataset.status = tx.status;
      const merchant = node("td");
      merchant.append(node("strong", transactionName(tx)), node("small", tx.description || tx.id));
      const amount = node("td", money(tx.amount_cents), "amount");
      const status = node("td");
      status.append(statusBadge(tx.status), node("small", label(tx.kind)));
      row.append(merchant, node("td", dateLabel(tx.occurred_at)), node("td", label(tx.category)), amount, status);
      host.append(row);
    });
    if (!rows.length) {
      const cell = node("td", data.transactions.length ? "No transactions match these filters." : "No transactions in this period.", "empty-state");
      cell.colSpan = 5;
      const row = node("tr");
      row.append(cell);
      host.append(row);
    }
    $("transaction-count").textContent = rows.length + " matching transactions · " + data.transactions.length + " in this period";
    $("page-count").textContent = "Page " + (page + 1) + " of " + pageTotal;
    $("previous-page").disabled = page === 0;
    $("next-page").disabled = page + 1 >= pageTotal;
    $("export-transactions").disabled = !rows.length;
  }
  document.querySelectorAll(".tab[data-filter]").forEach((tab) => tab.addEventListener("click", () => {
    statusFilter = tab.dataset.filter;
    page = 0;
    document.querySelectorAll(".tab[data-filter]").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    renderTransactions();
  }));
  $("transaction-search").addEventListener("input", () => { page = 0; renderTransactions(); });
  $("previous-page").addEventListener("click", () => { page = Math.max(0, page - 1); renderTransactions(); });
  $("next-page").addEventListener("click", () => { page += 1; renderTransactions(); });

  function amountRow(title, value) {
    const row = node("div", undefined, "amount-row");
    row.append(node("span", title), node("strong", value));
    return row;
  }
  function render() {
    const sourceName = data.source === "nessie" ? "Nessie sandbox" : "Synthetic fixture";
    const period = data.summary.start && data.summary.end ? dateLabel(data.summary.start) + " – " + dateLabel(data.summary.end) : "No transaction history";
    $("source-badge").textContent = sourceName;
    $("sync-title").textContent = "Snapshot loaded";
    $("sync-account-name").textContent = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" }).format(new Date(data.as_of));
    $("sync-dot").classList.remove("inactive");
    $("period-caption").textContent = period + " · UTC dates";
    $("overview-subheading").textContent = data.accounts.length + (data.accounts.length === 1 ? " account · " : " accounts · ") + data.transactions.length + " transactions in this period";
    $("balance-value").textContent = money(data.cash_balance_cents);
    const cashAccounts = data.accounts.filter((account) => ["Checking", "Savings"].includes(account.type)).length;
    const creditAccounts = data.accounts.filter((account) => account.type === "Credit Card").length;
    $("balance-foot").textContent = cashAccounts + " checking / savings " + (cashAccounts === 1 ? "account" : "accounts");
    $("spend-value").textContent = money(data.summary.net_spending_cents);
    $("spend-foot").textContent = data.summary.included_count + " completed expenses and refunds";
    $("credit-value").textContent = money(data.credit_balance_cents);
    $("credit-foot").textContent = creditAccounts ? "Amount owed across " + creditAccounts + " credit card " + (creditAccounts === 1 ? "account" : "accounts") : "No credit card accounts";
    $("transaction-badge").textContent = String(data.transactions.length);
    renderCategories();
    renderMonths();
    renderActivity();
    renderTransactions();
    $("coverage-details").replaceChildren(
      amountRow("Completed expenses", money(data.gross_spending_cents)),
      amountRow("Refunds", "−" + money(data.refunds_cents)),
      amountRow("Net spending", money(data.summary.net_spending_cents)),
      amountRow("Included / excluded records", data.summary.included_count + " / " + data.summary.excluded_count),
    );
    const largest = data.expense_categories[0];
    const share = largest && data.gross_spending_cents ? largest.amount_cents / data.gross_spending_cents * 100 : 0;
    $("largest-category-title").textContent = largest ? label(largest.category) + " leads your expenses." : "No expenses in this period.";
    $("largest-category-copy").textContent = largest ? money(largest.amount_cents) + " of completed expenses are in this category, before refunds." : "Try a wider date range to explore your spending.";
    $("largest-category-share").textContent = share.toFixed(1) + "%";
    $("refund-insight").textContent = money(data.refunds_cents);
    $("pending-insight").textContent = String(data.transactions.filter((row) => row.status === "pending").length);
    $("uncategorized-insight").textContent = money(data.expense_categories.find((row) => row.category === "uncategorized")?.amount_cents || 0);
    $("recurring-list").replaceChildren();
    data.recurring.forEach((item) => {
      const row = node("div", undefined, "recurring-row");
      const text = node("div");
      text.append(node("strong", item.merchant), node("p", item.occurrences + " charges, about every " + item.interval_days + " days", "chart-note"));
      row.append(text, node("strong", money(item.amount_cents)), node("span", "Candidate", "status needs-context"));
      $("recurring-list").append(row);
    });
    if (!data.recurring.length) $("recurring-list").append(node("p", "No monthly recurring candidates found in this period.", "empty-state"));
    $("report-total").textContent = money(data.summary.net_spending_cents);
    $("report-period").textContent = period + " · " + sourceName;
    $("report-categories").replaceChildren(...data.summary.categories.map((item) => amountRow(label(item.category), money(item.amount_cents))));
    if (!data.summary.categories.length) $("report-categories").append(node("p", "No expense or refund activity in this period.", "empty-state"));
    $("connection-source").textContent = sourceName;
    $("connection-customer").textContent = data.customer_id || "No accounts returned";
    $("connection-time").textContent = new Date(data.as_of).toLocaleString();
    $("accounts-list").replaceChildren();
    data.accounts.forEach((account) => {
      const card = node("section", undefined, "panel");
      card.append(node("span", account.type === "Credit Card" ? "▱" : "◈", "insight-icon green"), node("h2", account.nickname), node("p", account.type), node("p", money(account.balance_cents), "insight-number"), node("p", account.type === "Credit Card" ? "Current card balance; negative means credit." : "Current posted balance."));
      $("accounts-list").append(card);
    });
  }
  function isoDay(value) { return value.toISOString().slice(0, 10); }
  $("date-button").addEventListener("change", () => {
    const preset = $("date-button").value;
    $("custom-date-form").classList.toggle("hidden", preset !== "custom");
    $("date-error").textContent = "";
    if (preset === "custom") return;
    appliedPreset = preset;
    const today = new Date(data?.as_of || Date.now());
    if (preset === "all") range = {};
    else if (preset === "month") range = { start: isoDay(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))), end: isoDay(today) };
    else {
      const start = new Date(today);
      start.setUTCDate(start.getUTCDate() - 29);
      range = { start: isoDay(start), end: isoDay(today) };
    }
    void refresh();
  });
  $("custom-date-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const start = $("date-start").value;
    const end = $("date-end").value;
    if (!start || !end || start > end) {
      $("date-error").textContent = "Choose a start date on or before the end date.";
      return;
    }
    range = { start, end };
    appliedPreset = "custom";
    $("date-error").textContent = "";
    $("custom-date-form").classList.add("hidden");
    void refresh();
  });
  $("cancel-date-button").addEventListener("click", () => {
    $("custom-date-form").classList.add("hidden");
    $("date-button").value = appliedPreset;
  });

  function csvCell(value) {
    // Preserve numeric cells; neutralize formulas in provider-supplied text.
    const text = typeof value === "number" ? String(value) : String(value ?? "").replace(/^(\s*[=+\-@])/, "'$1");
    return '"' + text.replaceAll('"', '""') + '"';
  }
  function download(rows, filename) {
    const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const link = node("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message("CSV downloaded");
  }
  $("download-report").addEventListener("click", () => {
    if (!data) return;
    download([
      ["Ledgerly spending report"], ["Source", data.source], ["Customer", data.customer_id],
      ["Start (UTC)", data.summary.start], ["End (UTC)", data.summary.end], [],
      ["Category", "Net spending (cents)", "Currency"],
      ...data.summary.categories.map((row) => [row.category, row.amount_cents, "USD"]),
      [], ["Gross expenses (cents)", data.gross_spending_cents], ["Refunds (cents)", data.refunds_cents],
      ["Net spending (cents)", data.summary.net_spending_cents], ["Included records", data.summary.included_count],
      ["Excluded records", data.summary.excluded_count],
    ], "ledgerly-spending-" + (data.summary.end || "empty") + ".csv");
  });
  $("export-transactions").addEventListener("click", () => {
    if (!data) return;
    download([
      ["ID", "Account", "Date (UTC)", "Merchant", "Description", "Category", "Type", "Status", "Amount (cents)", "Currency"],
      ...filteredTransactions().map((row) => [row.id, row.account_id, row.occurred_at, row.merchant, row.description, row.category, row.kind, row.status, row.amount_cents, "USD"]),
    ], "ledgerly-transactions-" + (data.summary.end || "empty") + ".csv");
  });
  void refresh();
})();
