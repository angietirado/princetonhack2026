(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const currencyFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const money = (cents) => {
    if (!Number.isSafeInteger(cents)) return "Unavailable";
    const exact = BigInt(cents);
    const magnitude = exact < 0n ? -exact : exact;
    const fraction = (magnitude % 100n).toString().padStart(2, "0");
    const formatted = currencyFormat.formatToParts(magnitude / 100n).map((part) => part.type === "fraction" ? fraction : part.value).join("");
    return exact < 0n ? "-" + formatted : formatted;
  };
  const shortMoney = (cents) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 }).format(cents / 100);
  const percentFromBps = (bps) => {
    if (!Number.isSafeInteger(bps)) return "Unavailable";
    const exact = BigInt(bps);
    const magnitude = exact < 0n ? -exact : exact;
    return (exact < 0n ? "-" : "") + (magnitude / 100n).toString() + "." + (magnitude % 100n).toString().padStart(2, "0") + "%";
  };
  const label = (value) => String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  const dateLabel = (value) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(new Date(value.slice(0, 10) + "T00:00:00Z")) : "No activity";
  const colors = ["#8e74e8", "#f2a65a", "#6ca4e5", "#79bfa8", "#d898ad", "#bcc8c0"];
  const pageSize = 25;
  let token = "";
  let data = null;
  let categoryMappings = new Map();
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
  let askConversation = null;
  let askContextBlocked = false;
  let askContextNotice = "";
  let askContextTimer = null;
  let contextClearController = null;
  let contextClearTimer = null;
  let dashboardLoading = false;
  let answerContext = 0;
  let catalogSequence = 0;
  let catalogController = null;
  let catalogTimer = null;
  let catalogPending = false;
  let comparisonSequence = 0;
  let comparisonController = null;
  let comparisonTimer = null;
  let comparisonPending = false;

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
    const destination = $(view + "-view");
    if (!destination?.classList.contains("page")) return;
    document.querySelectorAll(".page").forEach((item) => item.classList.toggle("hidden", item.id !== view + "-view"));
    document.querySelector(".range-toolbar").classList.toggle("hidden", view === "ask" || view === "settings");
    document.querySelectorAll(".nav-item").forEach((item) => {
      const active = item.dataset.view === view;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    const active = document.querySelector('.nav-item[data-view="' + view + '"]');
    $("page-title").textContent = active ? active.getAttribute("aria-label") : "Overview";
    if (view === "overview" && data && !dashboardLoading) renderMonths();
    const heading = destination.querySelector("h1");
    if (heading && data && !dashboardLoading && !$("dashboard-data").classList.contains("hidden")) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
    }
    window.scrollTo({ top: 0, behavior: "auto" });
    if (view === "ask") void loadAskStatus();
  }
  document.querySelectorAll("[data-view], [data-view-link]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view || button.dataset.viewLink));
  });
  function clearData() {
    data = null;
    categoryMappings = new Map();
    resetTransactionFilters();
    resetAsk();
    resetComparison(true);
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
    forgetAskContext();
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
    updateTransactionFilterAvailability();
    resetAsk();
    resetComparison();
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
      const mappings = validateCategoryMapping(payload);
      if (!mappings) throw new Error("The backend returned unsupported transaction categories. Update both services, then refresh.");
      data = payload;
      categoryMappings = mappings;
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
        updateTransactionFilterAvailability();
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
    forgetAskContext();
    token = entered;
    $("app-token").value = "";
    void refresh();
  });

  // Only question text and validated missing-input codes survive an accepted turn.
  // Context is deliberately absent from browser storage, URLs, and tool evidence.
  const contextInputCodes = new Set(["date_range", "location", "monthly_rent_budget", "bedrooms", "minimum_square_feet", "housing_preferences", "financial_goal", "comparison_options", "household_size", "income", "savings_target"]);
  function contextBytes(questions, neededInputs) {
    let bytes = 0;
    for (const character of JSON.stringify({ questions, needed_inputs: neededInputs })) {
      const point = character.codePointAt(0);
      bytes += point <= 0x7f ? 1 : point <= 0x7ff ? 2 : point <= 0xffff ? 3 : 4;
    }
    return bytes;
  }
  function clearRemoteContext(id, capturedToken) {
    contextClearController?.abort();
    clearTimeout(contextClearTimer);
    const pending = new AbortController();
    contextClearController = pending;
    contextClearTimer = setTimeout(() => pending.abort(), 5000);
    void (async () => {
      try {
        await fetch("/api/v1/agent/context/clear", {
          method: "POST", headers: { "Content-Type": "application/json", ...(capturedToken ? { Authorization: "Bearer " + capturedToken } : {}) },
          body: JSON.stringify({ id }), cache: "no-store", redirect: "error", signal: pending.signal,
        });
      } catch { /* Local forgetting never depends on a network acknowledgment. */ }
      finally {
        if (contextClearController === pending) { clearTimeout(contextClearTimer); contextClearController = null; }
      }
    })();
  }
  function forgetAskContext({ blocked = false, notice = "", resetChoice = false } = {}) {
    const identifier = askConversation?.receipt.state === "active" ? askConversation.receipt.id : null;
    askConversation = null;
    askContextBlocked = blocked && $("ask-keep-context").checked;
    askContextNotice = notice;
    clearTimeout(askContextTimer);
    if (resetChoice) $("ask-keep-context").checked = false;
    if (identifier) clearRemoteContext(identifier, token);
    renderAskContext();
  }
  function renderAskContext() {
    const checked = $("ask-keep-context").checked;
    const receipt = askConversation?.receipt;
    $("ask-context-note").textContent = askContextNotice || (!checked ? "Off: each question starts fresh." : receipt?.state === "active"
      ? receipt.turns_used + " of 4 questions kept, for up to 10 minutes from the first question. Each answer reads fresh evidence."
      : "Keep up to 4 questions for 10 minutes, within an 8 KB text limit. Earlier answers are not retained; each answer reads fresh evidence.");
    $("ask-context-questions").replaceChildren();
    for (const question of askConversation?.questions || []) $("ask-context-questions").append(node("li", question));
    $("ask-context-questions").classList.toggle("hidden", !askConversation?.questions.length);
  }
  function restartAskQuestion() {
    if (askPending) {
      askSequence += 1;
      askController?.abort();
      clearTimeout(askTimer);
      askController = null;
      askPending = false;
    }
    forgetAskContext();
    clearAskAnswer();
    $("ask-error").textContent = "";
    $("ask-state").textContent = "New question ready. Your draft is kept; nothing has been submitted.";
    updateAskControls();
    restoreCancelledFocus($("ask-question"), answerContext);
  }
  function validatedConversation(result, question, previous) {
    const receipt = result.conversation;
    const accepted = ["answered", "clarification"].includes(result.status);
    const neededInputs = result.needed_inputs || [];
    const priorTurns = previous?.receipt.turns_used || 0;
    const turns = priorTurns + (accepted ? 1 : 0);
    const questions = accepted ? [...(previous?.questions || []), question] : [...(previous?.questions || [])];
    const invalid = () => { throw new Error("invalid conversation receipt"); };
    if (!/^[a-f0-9]{64}$/.test(result.source_hash) || (previous && (result.source_hash !== previous.sourceHash || result.source !== previous.source))) invalid();
    if (neededInputs.some((code) => !contextInputCodes.has(code)) || (result.status === "answered" && neededInputs.length)) invalid();
    if (!receipt && !accepted && !previous) return null;
    if (!object(receipt)) invalid();
    if (receipt.state === "active") {
      if (Object.keys(receipt).sort().join() !== "expires_at,id,revision,state,turns_used" || !/^[a-f0-9]{64}$/.test(receipt.id) ||
          !Number.isSafeInteger(receipt.revision) || receipt.revision !== turns || receipt.turns_used !== turns || turns < 1 || turns > 3 ||
          typeof receipt.expires_at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(receipt.expires_at) || !Number.isFinite(Date.parse(receipt.expires_at)) || Date.parse(receipt.expires_at) <= Date.now() ||
          (previous && (receipt.id !== previous.receipt.id || receipt.expires_at !== previous.receipt.expires_at))) invalid();
      if (new Date(receipt.expires_at).toISOString().slice(0, 19) !== receipt.expires_at.slice(0, 19)) invalid();
      const codes = accepted ? neededInputs : previous?.neededInputs || [];
      if (questions.length !== turns || contextBytes(questions, codes) > 8192) invalid();
      return { receipt, questions, neededInputs: codes, source: result.source, sourceHash: result.source_hash };
    }
    if (receipt.state !== "closed" || Object.keys(receipt).sort().join() !== "reason,state,turns_used" || !Number.isSafeInteger(receipt.turns_used)) invalid();
    if (receipt.reason === "scope_unavailable") {
      if (previous || receipt.turns_used !== 0) invalid();
      return { receipt, questions: [], neededInputs: [], source: result.source, sourceHash: result.source_hash };
    }
    if (["expired", "cleared"].includes(receipt.reason)) {
      if (receipt.turns_used !== priorTurns) invalid();
      return { receipt, questions: [], neededInputs: [], source: result.source, sourceHash: result.source_hash };
    }
    if (!accepted || receipt.turns_used !== turns || turns > 4 ||
        (receipt.reason === "turn_limit" ? turns !== 4 || contextBytes(questions, neededInputs) > 8192 :
          receipt.reason !== "byte_limit" || contextBytes(questions, neededInputs) <= 8192)) invalid();
    return { receipt, questions: receipt.reason === "byte_limit" ? [] : questions, neededInputs: [], source: result.source, sourceHash: result.source_hash };
  }
  function acceptAskContext(next) {
    clearTimeout(askContextTimer);
    askConversation = next;
    askContextNotice = "";
    askContextBlocked = next?.receipt.state === "closed";
    if (next?.receipt.state === "active") {
      const expires = Date.parse(next.receipt.expires_at);
      if (expires <= Date.now()) {
        forgetAskContext({ blocked: true, notice: "Recent questions expired. Choose New question to restart, or turn off Keep recent questions." });
      } else {
        // A local timer is a convenience; the server independently enforces expiry.
        askContextTimer = setTimeout(() => {
          if (askPending) {
            askSequence += 1;
            askController?.abort();
            clearTimeout(askTimer);
            askController = null;
            askPending = false;
            $("ask-state").textContent = "Recent questions expired while waiting. No result will be accepted; your draft is kept.";
          }
          forgetAskContext({ blocked: true, notice: "Recent questions expired. Choose New question to restart, or turn off Keep recent questions." });
          updateAskControls();
        }, Math.min(expires - Date.now(), 600000));
      }
    } else if (next?.receipt.reason === "scope_unavailable") {
      forgetAskContext({ resetChoice: true, notice: "This data source could not support follow-ups. Recent questions are off; include all details in each question." });
    } else if (next) {
      askContextNotice = "Recent questions are closed (" + ({ turn_limit: "4-question limit", byte_limit: "text limit", expired: "expired", cleared: "cleared" })[next.receipt.reason] + "). Choose New question to restart, or turn off Keep recent questions.";
    }
    renderAskContext();
  }
  function clearAskAnswer() {
    answerContext += 1;
    clearCatalog();
    $("ask-result").classList.add("hidden");
    for (const id of ["ask-outcome", "ask-asked-question", "ask-answer", "ask-usage", "ask-source", "ask-revision"]) $(id).textContent = "";
    $("ask-clarification").classList.add("hidden");
    $("ask-question").setAttribute("aria-describedby", "ask-guidance ask-counter");
    $("ask-technical-details").open = false;
    $("ask-caveats").replaceChildren();
    $("ask-evidence").replaceChildren();
    $("ask-activity").replaceChildren();
    $("ask-needed-inputs").replaceChildren();
  }
  function updateAskControls() {
    updateComparisonControls();
    const idle = Boolean(data && !dashboardLoading && !askStatusLoading && !askPending && !catalogPending);
    const ready = idle && askStatus?.enabled;
    $("ask-question").disabled = !ready;
    $("ask-submit").disabled = !ready || !$("ask-question").value.trim() || ($("ask-keep-context").checked && askContextBlocked);
    $("ask-keep-context").disabled = !data || dashboardLoading || askStatusLoading || !askStatus?.enabled;
    $("ask-new-question").disabled = !data || dashboardLoading || askStatusLoading || !askStatus?.enabled;
    $("ask-submit").textContent = askPending ? "Working…" : "Ask →";
    $("ask-cancel").classList.toggle("hidden", !askPending);
    $("ask-cancel").disabled = !askPending;
    $("ask-edit-question").disabled = !ready;
    $("ask-check").disabled = !idle;
    $("ask-example-spending").disabled = !ready;
    $("ask-example-housing").disabled = !ready;
    $("ask-example-housing").classList.toggle("hidden", askStatus?.costar_enabled !== true);
    $("ask-counter").textContent = $("ask-question").value.length + " / 2000 characters";
    $("ask-form").setAttribute("aria-busy", String(askPending));
    const catalogEnabled = askStatus?.costar_enabled === true;
    const catalogReady = idle && catalogEnabled;
    $("catalog-controls").classList.toggle("hidden", !catalogEnabled);
    for (const id of ["catalog-state", "catalog-city", "catalog-bedrooms", "catalog-rent", "catalog-area", "catalog-submit"]) $(id).disabled = !catalogReady;
    $("catalog-submit").textContent = catalogPending ? "Searching…" : "Search catalog";
    $("catalog-cancel").classList.toggle("hidden", !catalogPending);
    $("catalog-form").setAttribute("aria-busy", String(catalogPending));
    $("catalog-availability").textContent = askStatus?.costar_enabled === true ? "Direct catalog search is available, including when AI is disabled." : askStatus ? "The apartment catalog is disabled for this workspace." : "Check availability above to search.";
  }
  function resetAsk() {
    forgetAskContext({ resetChoice: true });
    askSequence += 1;
    askController?.abort();
    clearTimeout(askTimer);
    askController = null;
    askStatus = null;
    askStatusLoading = false;
    askPending = false;
    $("catalog-form").reset();
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
      if (!checkingStatus && $("ask-keep-context").checked) forgetAskContext({ blocked: true, notice: "The answer was not confirmed. Choose New question before continuing." });
      $("ask-state").textContent = "";
      if (checkingStatus) $("ask-provider").textContent = "AI availability could not be checked.";
      $("ask-error").textContent = checkingStatus ? "The availability check timed out. Try checking again." : "The answer request timed out. Check the connection before submitting again.";
      updateAskControls();
    }, milliseconds);
  }
  async function loadAskStatus(force = false) {
    if (!data || dashboardLoading || askPending || catalogPending || askStatusLoading || (askStatus && !force)) return;
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
      $("ask-provider").textContent = status.enabled ? "AI questions are enabled · Read-only tools" : "AI is disabled for this workspace. Your spending dashboard remains available.";
    } catch (error) {
      if (requestId !== askSequence || error.name === "AbortError") return;
      $("ask-provider").textContent = "AI availability could not be checked.";
      $("ask-error").textContent = "Check the backend connection, then check availability again.";
    } finally {
      if (requestId === askSequence) { clearTimeout(askTimer); askStatusLoading = false; updateAskControls(); }
    }
  }
  $("ask-check").addEventListener("click", () => void loadAskStatus(true));
  $("ask-new-question").addEventListener("click", restartAskQuestion);
  $("ask-keep-context").addEventListener("change", () => {
    restartAskQuestion();
  });
  $("ask-question").addEventListener("input", () => { $("ask-error").textContent = ""; updateAskControls(); });
  $("ask-edit-question").addEventListener("click", () => {
    if ($("ask-edit-question").disabled || $("ask-result").classList.contains("hidden")) return;
    $("ask-question").value = $("ask-asked-question").textContent;
    $("ask-error").textContent = "";
    $("ask-state").textContent = $("ask-keep-context").checked && askConversation?.receipt.state === "active" ? "Add the requested details, then submit. Your recent questions will supply context; the answer will read fresh evidence." : "Add the requested details to your original question, then submit it. Each question starts fresh.";
    updateAskControls();
    $("ask-question").focus();
  });
  $("ask-example-spending").addEventListener("click", () => {
    if (!askStatus?.enabled || askPending || catalogPending) return;
    restartAskQuestion();
    const start = data?.summary.start || "[start date]";
    const end = data?.summary.end || "[end date]";
    $("ask-question").value = "Summarize my observed spending from " + start + " through " + end + " in UTC. Include refunds, exclusions, and missing-history caveats.";
    updateAskControls();
    $("ask-question").focus();
  });
  $("ask-example-housing").addEventListener("click", () => {
    if (!askStatus?.enabled || !askStatus.costar_enabled || askPending || catalogPending) return;
    restartAskQuestion();
    $("ask-question").value = "Find up to 3 one-bedroom apartments in TX under $1,800 monthly asking rent. Show each full rent range and recorded availability limitations.";
    updateAskControls();
    $("ask-question").focus();
  });
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  function canonicalCriteria(criteria) {
    const fields = ["state", "city", "bedrooms", "max_monthly_rent_usd", "min_square_feet", "limit", "include_specialized_housing"];
    if (!object(criteria) || Object.keys(criteria).length !== fields.length || !fields.every((key) => Object.hasOwn(criteria, key))) return false;
    return ["TX", "CA"].includes(criteria.state) &&
      (criteria.city === null || (typeof criteria.city === "string" && criteria.city.length > 0 && criteria.city.length <= 80 && criteria.city === criteria.city.trim() && !/[\u0000-\u001f]/.test(criteria.city))) &&
      [["bedrooms", 0, 6], ["max_monthly_rent_usd", 1, 100000], ["min_square_feet", 0, 10000], ["limit", 1, 20]].every(([key, low, high]) => Number.isSafeInteger(criteria[key]) && criteria[key] >= low && criteria[key] <= high) &&
      typeof criteria.include_specialized_housing === "boolean";
  }
  function clearCatalog() {
    catalogSequence += 1;
    catalogController?.abort();
    clearTimeout(catalogTimer);
    catalogController = null;
    catalogPending = false;
    $("catalog-result").classList.add("hidden");
    for (const id of ["catalog-state-message", "catalog-error", "catalog-source"]) $(id).textContent = "";
    $("catalog-evidence").replaceChildren();
  }
  function readCatalogCriteria() {
    const integer = (id, optional = false) => {
      const text = $(id).value.trim();
      if (optional && !text) return 0;
      return /^\d+$/.test(text) ? Number(text) : NaN;
    };
    return {
      state: $("catalog-state").value, city: $("catalog-city").value.trim() || null,
      bedrooms: integer("catalog-bedrooms"), max_monthly_rent_usd: integer("catalog-rent"),
      min_square_feet: integer("catalog-area", true), limit: 3,
      include_specialized_housing: false,
    };
  }
  function sameCatalogCriteria(actual, expected) {
    return canonicalCriteria(actual) && Object.keys(expected).every((key) => actual[key] === expected[key]);
  }
  function validCatalogResult(result) {
    return object(result) && result.status === "ok" && result.source_kind === "costar_local_snapshot" &&
      typeof result.source_hash === "string" && /^[a-f0-9]{64}$/.test(result.source_hash) &&
      Array.isArray(result.results) && result.results.length <= 3 && result.results.every(object) &&
      Array.isArray(result.limitations) && result.limitations.every((item) => typeof item === "string") &&
      JSON.stringify(result).length <= 96000;
  }
  function restoreCancelledFocus(target, context) {
    if (context === answerContext && data && !dashboardLoading && !$("ask-view").classList.contains("hidden") &&
        !$("dashboard-data").classList.contains("hidden") && target?.isConnected && !target.disabled && !target.closest(".hidden")) target.focus();
  }
  $("ask-cancel").addEventListener("click", () => {
    if (!askPending) return;
    const context = answerContext;
    askSequence += 1;
    askController?.abort();
    clearTimeout(askTimer);
    askController = null;
    askPending = false;
    if ($("ask-keep-context").checked) forgetAskContext({ blocked: true, notice: "Stopped waiting and forgot recent questions. Choose New question to restart." });
    $("ask-state").textContent = "Stopped waiting for the answer. The server may still finish this request; no result will be accepted and no retry was sent.";
    $("ask-error").textContent = "";
    updateAskControls();
    restoreCancelledFocus($("ask-question"), context);
  });
  $("catalog-cancel").addEventListener("click", () => {
    if (!catalogPending) return;
    const context = answerContext;
    clearCatalog();
    $("catalog-state-message").textContent = "Stopped waiting for the catalog search. No result was accepted.";
    updateAskControls();
    restoreCancelledFocus($("catalog-submit"), context);
  });
  $("catalog-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!data || dashboardLoading || askStatus?.costar_enabled !== true || askStatusLoading || askPending || catalogPending) return;
    const criteria = readCatalogCriteria();
    if (!canonicalCriteria(criteria)) {
      $("catalog-error").textContent = "Use TX or CA, a city of at most 80 characters without control characters, 0–6 bedrooms, a whole-dollar starting rent from 1 to 100,000, and an optional whole-number area from 0 to 10,000 square feet.";
      return;
    }
    Object.freeze(criteria);
    clearAskAnswer();
    const requestId = catalogSequence;
    const context = answerContext;
    let completionMessage = "";
    catalogController = new AbortController();
    catalogPending = true;
    $("ask-error").textContent = "";
    $("ask-state").textContent = "";
    $("catalog-state-message").textContent = "Searching the local catalog with your submitted filters…";
    updateAskControls();
    catalogTimer = setTimeout(() => {
      if (requestId !== catalogSequence || context !== answerContext) return;
      catalogSequence += 1;
      catalogController?.abort();
      catalogPending = false;
      $("catalog-state-message").textContent = "";
      $("catalog-error").textContent = "The catalog search timed out. No result was accepted; you may try again.";
      updateAskControls();
    }, 60000);
    try {
      const response = await fetch("/api/v1/costar/search", { method: "POST", headers: askAuthHeaders(true), body: JSON.stringify(criteria), cache: "no-store", redirect: "error", signal: catalogController.signal });
      if (requestId !== catalogSequence || context !== answerContext) return;
      if (response.status === 401) { lock(); $("access-error").textContent = "Your app token was not accepted. Unlock again to search the catalog."; return; }
      if (!response.ok) {
        $("catalog-error").textContent = ({ 403: "The catalog search was not authorized.", 422: "The catalog rejected these filters. Check the fields before submitting again.", 429: "Another catalog search is running. Wait briefly before trying again.", 503: "The catalog is disabled or unavailable. Check availability before trying again.", 504: "The catalog search reached its time limit. No result was accepted." })[response.status] || "The catalog search could not be completed. No results were accepted.";
        return;
      }
      const result = await response.json();
      if (requestId !== catalogSequence || context !== answerContext) return;
      if (object(result) && result.status === "unavailable") { $("catalog-error").textContent = "The catalog is unavailable. No results were accepted."; return; }
      if (!object(result) || !sameCatalogCriteria(result.criteria, criteria)) {
        $("catalog-error").textContent = "The returned filters did not match your submitted search. No results were accepted; check availability before searching again.";
        return;
      }
      if (!validCatalogResult(result)) throw new Error("invalid catalog response");
      $("catalog-source").textContent = "Catalog revision: " + result.source_hash;
      $("catalog-evidence").append(renderToolEvidence({ tool: "costar_search", arguments: criteria, result }, true));
      $("catalog-result").classList.remove("hidden");
      completionMessage = result.results.length ? "Catalog search complete. " + result.results.length + " recorded matches returned. Review the direct results and rent ranges below." : "Catalog search complete. No recorded matches were returned for these filters; this is not a whole-market result.";
    } catch (error) {
      if (requestId !== catalogSequence || context !== answerContext || error.name === "AbortError") return;
      $("catalog-error").textContent = "A valid catalog response could not be received. No results were accepted.";
    } finally {
      if (requestId === catalogSequence && context === answerContext) {
        clearTimeout(catalogTimer);
        catalogPending = false;
        $("catalog-state-message").textContent = completionMessage;
        updateAskControls();
      }
    }
  });
  function validAskAnswer(value) {
    return object(value) && ["answered", "clarification", "limited", "unavailable"].includes(value.status) &&
      typeof value.answer === "string" && value.answer.length <= 8000 &&
      ["provider", "model", "source", "source_hash"].every((key) => typeof value[key] === "string" && value[key].length <= 256) &&
      ["model_calls", "tool_calls"].every((key) => Number.isSafeInteger(value[key]) && value[key] >= 0 && value[key] <= 32) &&
      Array.isArray(value.caveats) && value.caveats.length <= 64 && value.caveats.every((item) => typeof item === "string" && item.length <= 2000) &&
      Array.isArray(value.evidence) && value.evidence.length <= 12 && value.evidence.every((item) => object(item) && typeof item.tool === "string" && item.tool.length <= 80 && object(item.arguments) && object(item.result)) &&
      (value.status !== "answered" || value.evidence.some((item) => item.result.status === "ok")) &&
      (value.needed_inputs === undefined || (Array.isArray(value.needed_inputs) && value.needed_inputs.length <= 6 && value.needed_inputs.every((item) => typeof item === "string" && item.length <= 200))) &&
      (value.status !== "clarification" || value.needed_inputs?.length > 0) &&
      (value.activity === undefined || (Array.isArray(value.activity) && value.activity.length <= 24 && value.activity.every((item) => object(item) && ["role", "tool", "status"].every((key) => typeof item[key] === "string" && item[key].length <= 160)))) &&
      JSON.stringify(value).length <= 512000;
  }
  function renderToolEvidence(entry, directRequest = false) {
    const result = entry.result;
    const host = node("article", undefined, "ask-evidence-item");
    const titles = { summarize_spending: "Observed spending", compare_spending: "Period comparison", get_transactions: "Transaction records", find_recurring_charges: "Recurring candidates", costar_search: "Apartment matches" };
    host.append(node("h4", Object.hasOwn(titles, entry.tool) ? titles[entry.tool] : label(entry.tool)));
    if (result.status !== "ok") {
      host.append(node("p", "This read did not return usable evidence. No zero total or empty-market conclusion follows."));
    } else if (entry.tool === "summarize_spending" && Number.isSafeInteger(result.net_spending_cents)) {
      host.append(node("p", "Observed net spending: " + money(result.net_spending_cents) + ". History completeness is unknown."));
    } else if (entry.tool === "compare_spending" && validComparison(result, entry.arguments)) {
      host.append(node("p", "Observed net spending: " + money(result.total.baseline_cents) + " → " + money(result.total.current_cents) + "; change " + money(result.total.delta_cents) + "."));
      for (const finding of result.findings) host.append(node("p", finding.text));
      for (const caveat of result.caveats) host.append(node("p", caveat, "chart-note"));
    } else if (entry.tool === "get_transactions") {
      host.append(node("p", result.returned_rows + " of " + result.total_rows + " matching observed records returned. Signed contributions and excluded records are in the details."));
    } else if (entry.tool === "find_recurring_charges") {
      host.append(node("p", "Pattern candidates only; these do not confirm subscriptions, savings, or a next bill."));
    } else if (entry.tool === "costar_search" && Array.isArray(result.results)) {
      if (canonicalCriteria(result.criteria)) {
        const criteria = result.criteria;
        const budget = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(criteria.max_monthly_rent_usd);
        host.append(node("p", "Applied catalog filters: " + criteria.state + (criteria.city ? ", " + criteria.city : ", any city") +
          "; " + (criteria.bedrooms === 0 ? "studio" : criteria.bedrooms + " bedroom(s)") +
          "; advertised starting rent at most " + budget + " per month, per unit; " + (criteria.min_square_feet ? "at least " + criteria.min_square_feet + " square feet" : "no minimum area") + "; up to " + criteria.limit + " results; " +
          (criteria.include_specialized_housing ? "specialized housing included" : "specialized housing excluded") + (directRequest ? ". These are your submitted filters." : ". Compare these applied filters with your question."), "ask-applied-criteria"));
      } else host.append(node("p", "Applied catalog filters are unavailable or unrecognized. Review the exact evidence before relying on these matches.", "ask-applied-criteria"));
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
          if (row.price_range_within_budget !== true) listing.append(node("p", "The full range is not confirmed within the applied catalog budget."));
        } else listing.append(node("p", "Rent units or range are unavailable; see the exact evidence."));
        host.append(listing);
      }
    }
    if (Array.isArray(result.limitations)) {
      const list = node("ul", undefined, "ask-caveats");
      for (const item of result.limitations) if (typeof item === "string") list.append(node("li", item));
      host.append(list);
    }
    const details = node("details");
    details.append(node("summary", directRequest ? "Exact catalog request and response" : "Exact tool evidence"), node("pre", JSON.stringify(directRequest ? { request: entry.arguments, response: result } : entry, null, 2)));
    host.append(details);
    return host;
  }
  function renderAskAnswer(result, question) {
    $("ask-outcome").textContent = { answered: "Answered", clarification: "More information needed", limited: "Request limit reached", unavailable: "Answer unavailable" }[result.status];
    $("ask-answer-title").textContent = { answered: "Answer", clarification: "Clarify your question", limited: "What was completed", unavailable: "Unable to answer" }[result.status];
    $("ask-asked-question").textContent = question;
    $("ask-answer").textContent = result.answer;
    $("ask-usage").textContent = label(result.provider) + " · " + result.model + " · " + result.model_calls + " model calls · " + result.tool_calls + " tool calls";
    if (Number.isSafeInteger(result.output_tokens_reserved) && result.output_tokens_reserved >= 0) $("ask-usage").textContent += " · " + result.output_tokens_reserved + " output tokens reserved (not billed usage)";
    for (const item of result.activity || []) $("ask-activity").append(node("li", item.role + " · " + item.tool + " · " + item.status));
    const inputLabels = {
      date_range: "Start and end dates (UTC)", location: "City and state", monthly_rent_budget: "Monthly rent budget (USD)",
      bedrooms: "Number of bedrooms", minimum_square_feet: "Minimum area (square feet)", housing_preferences: "Housing preferences",
      financial_goal: "Financial goal", comparison_options: "Options to compare", household_size: "Household size",
      income: "Income and its time period", savings_target: "Savings target and time frame",
    };
    for (const item of result.needed_inputs || []) $("ask-needed-inputs").append(node("li", Object.hasOwn(inputLabels, item) ? inputLabels[item] : item));
    $("ask-clarification").classList.toggle("hidden", result.status !== "clarification");
    $("ask-clarification-guidance").textContent = $("ask-keep-context").checked && askConversation?.receipt.state === "active" ? "Reply with the missing details. Recent questions provide context; earlier answers are not retained." : "Edit and resubmit the complete question. Earlier questions and answers are not carried into a new request.";
    $("ask-question").setAttribute("aria-describedby", "ask-guidance ask-counter" + (result.status === "clarification" ? " ask-needed-inputs" : ""));
    const sourceName = { fixture: "Synthetic fixture", nessie: "Nessie sandbox" };
    $("ask-source").textContent = "Spending source: " + (Object.hasOwn(sourceName, result.source) ? sourceName[result.source] : result.source) + ". Each tool record shows the dates and filters used.";
    $("ask-revision").textContent = "Spending revision: " + result.source_hash;
    for (const caveat of result.caveats) $("ask-caveats").append(node("li", caveat));
    for (const entry of result.evidence) $("ask-evidence").append(renderToolEvidence(entry));
    if (!result.evidence.length) $("ask-evidence").append(node("p", "No tool evidence was returned."));
    $("ask-result").classList.remove("hidden");
  }
  $("ask-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!data || dashboardLoading || !askStatus?.enabled || askStatusLoading || askPending || catalogPending) return;
    const question = $("ask-question").value.trim();
    if (!question || $("ask-question").value.length > 2000) { $("ask-error").textContent = "Enter a question of 1 to 2000 characters."; return; }
    const contextual = $("ask-keep-context").checked;
    const previous = askConversation;
    if (contextual && askContextBlocked) { $("ask-error").textContent = "Choose New question to restart, or turn off Keep recent questions."; return; }
    if (contextual && previous?.receipt.state === "active" && Date.parse(previous.receipt.expires_at) <= Date.now()) {
      forgetAskContext({ blocked: true, notice: "Recent questions expired. Choose New question to restart." }); updateAskControls(); return;
    }
    if (contextual && ((previous?.questions.length || 0) >= 4 || contextBytes([...(previous?.questions || []), question], previous?.neededInputs || []) > 8192)) {
      forgetAskContext({ blocked: true, notice: "This question exceeds the recent-question text limit. Choose New question to restart, or turn off Keep recent questions." }); updateAskControls(); return;
    }
    const body = { question };
    if (contextual) body.conversation = previous ? { mode: "continue", id: previous.receipt.id, revision: previous.receipt.revision } : { mode: "new" };
    const requestId = ++askSequence;
    let completionMessage = "";
    askController?.abort();
    askController = new AbortController();
    askPending = true;
    clearAskAnswer();
    const context = answerContext;
    $("ask-error").textContent = "";
    $("ask-state").textContent = "Reading evidence and preparing an answer…";
    updateAskControls();
    startAskDeadline(requestId, 130000, false);
    try {
      const response = await fetch("/api/v1/agent/answer", { method: "POST", headers: askAuthHeaders(true), body: JSON.stringify(body), cache: "no-store", redirect: "error", signal: askController.signal });
      if (requestId !== askSequence || context !== answerContext) return;
      if (response.status === 401) { lock(); $("access-error").textContent = "Your app token was not accepted. Unlock again to continue."; return; }
      if (!response.ok) {
        if (contextual && response.status !== 429) forgetAskContext({ blocked: true, notice: "The conversation could not continue. Choose New question to restart; your draft is kept." });
        $("ask-error").textContent = ({ 409: "Recent questions are no longer available. Choose New question and include the needed details.", 429: "An AI request is already running. Wait briefly, then submit again.", 503: "The AI service is unavailable. Check availability before trying again.", 504: "The request reached its time limit. Try a narrower question." })[response.status] || "The backend could not answer this question. Check availability and try again.";
        return;
      }
      const result = await response.json();
      if (requestId !== askSequence || context !== answerContext) return;
      if (!validAskAnswer(result)) throw new Error("invalid answer");
      const nextContext = contextual ? validatedConversation(result, question, previous) : null;
      if (!contextual && result.conversation != null) throw new Error("unexpected conversation receipt");
      if (contextual) acceptAskContext(nextContext);
      renderAskAnswer(result, question);
      completionMessage = { answered: "Answer ready. Review the answer and supporting evidence below.", clarification: "More information is needed. Review the requested inputs and submit a complete question.", limited: "Request limit reached. Review the completed evidence and limits below.", unavailable: "Answer unavailable. Review any returned evidence and limitations below." }[result.status];
    } catch (error) {
      if (requestId !== askSequence || context !== answerContext || error.name === "AbortError") return;
      if (contextual) forgetAskContext({ blocked: true, notice: "The answer was not confirmed. Choose New question to restart; your draft is kept." });
      $("ask-error").textContent = "A valid answer could not be received. Check the connection and try again.";
    } finally {
      if (requestId === askSequence && context === answerContext) { clearTimeout(askTimer); askPending = false; $("ask-state").textContent = completionMessage; updateAskControls(); }
    }
  });

  function updateComparisonControls() {
    const ready = Boolean(data && !dashboardLoading && !comparisonPending);
    for (const id of ["comparison-baseline-start", "comparison-baseline-end", "comparison-current-start", "comparison-current-end", "comparison-submit"]) $(id).disabled = !ready;
    $("comparison-use-period").disabled = !ready || !data?.summary?.start || !data?.summary?.end;
    $("comparison-cancel").classList.toggle("hidden", !comparisonPending);
    $("comparison-form").setAttribute("aria-busy", String(comparisonPending));
    $("comparison-submit").textContent = comparisonPending ? "Comparing…" : "Compare periods";
  }
  function resetComparison(clearInputs = false) {
    comparisonSequence += 1;
    comparisonController?.abort();
    clearTimeout(comparisonTimer);
    comparisonController = null;
    comparisonPending = false;
    $("comparison-result").classList.add("hidden");
    for (const id of ["comparison-state", "comparison-error", "comparison-period-label", "comparison-source", "comparison-evidence"]) $(id).textContent = "";
    for (const id of ["comparison-totals", "comparison-findings", "comparison-categories", "comparison-merchants", "comparison-caveats"]) $(id).replaceChildren();
    $("comparison-details").open = false;
    if (clearInputs) $("comparison-form").reset();
    updateComparisonControls();
  }
  function calendarDay(text) {
    if (typeof text !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(text) || Number(text.slice(0, 4)) < 1) return false;
    const parsed = new Date(text + "T00:00:00Z");
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text;
  }
  function validPeriods(value) {
    return object(value) && ["baseline", "current"].every((key) => object(value[key]) && calendarDay(value[key].start) && calendarDay(value[key].end) && value[key].start <= value[key].end) && value.baseline.end < value.current.start;
  }
  function validComparison(result, expected) {
    const change = (row) => {
      if (!object(row) || !["baseline_cents", "current_cents", "delta_cents"].every((key) => Number.isSafeInteger(row[key]))) return false;
      if (BigInt(row.current_cents) - BigInt(row.baseline_cents) !== BigInt(row.delta_cents)) return false;
      if (row.baseline_cents <= 0) return row.percent_change_bps === null && row.percent_status === (row.baseline_cents === 0 ? "zero_baseline" : "negative_baseline");
      if (row.percent_status !== "defined" || !Number.isSafeInteger(row.percent_change_bps)) return false;
      const numerator = BigInt(row.delta_cents) * 10000n;
      const denominator = BigInt(row.baseline_cents);
      const magnitude = numerator < 0n ? -numerator : numerator;
      // Match the server's ROUND_HALF_UP without losing cents in intermediate arithmetic.
      const rounded = (magnitude * 2n + denominator) / (2n * denominator);
      return BigInt(row.percent_change_bps) === (numerator < 0n ? -rounded : rounded);
    };
    if (!object(result) || result.status !== "ok" || result.calculation_version !== "spending-comparison-v1" || result.currency !== "USD" || result.coverage !== "observed_records_only" || !validPeriods(expected) || !validPeriods(result) || !change(result.total)) return false;
    if (!["source_hash", "comparison_id"].every((key) => typeof result[key] === "string" && /^[a-f0-9]{64}$/.test(result[key])) || typeof result.source !== "string" || result.source.length > 256) return false;
    for (const key of ["baseline", "current"]) {
      const period = result[key];
      if (period.start !== expected[key].start || period.end !== expected[key].end || !Number.isSafeInteger(period.duration_days) || period.duration_days < 1 || period.net_spending_cents !== result.total[key + "_cents"]) return false;
      if (period.duration_days !== Math.round((new Date(period.end + "T00:00:00Z") - new Date(period.start + "T00:00:00Z")) / 86400000) + 1) return false;
    }
    if (result.equal_duration !== (result.baseline.duration_days === result.current.duration_days)) return false;
    for (const key of ["categories", "merchants"]) {
      const group = result[key];
      if (!object(group) || !Array.isArray(group.rows) || group.rows.length > 10 || !group.rows.every((row) => change(row) && typeof row.label === "string" && row.label.length <= 300)) return false;
      if (!Number.isSafeInteger(group.omitted_groups) || group.omitted_groups < 0) return false;
      for (const metric of ["baseline_cents", "current_cents", "delta_cents"]) {
        if (!Number.isSafeInteger(group["omitted_" + metric]) || group.rows.reduce((sum, row) => sum + BigInt(row[metric]), BigInt(group["omitted_" + metric])) !== BigInt(result.total[metric])) return false;
      }
    }
    return Array.isArray(result.findings) && result.findings.length <= 5 && result.findings.every((row) => object(row) && typeof row.text === "string" && row.text.length <= 1600) && Array.isArray(result.caveats) && result.caveats.length <= 20 && result.caveats.every((item) => typeof item === "string" && item.length <= 1000) && JSON.stringify(result).length <= 64000;
  }
  $("comparison-use-period").addEventListener("click", () => {
    if ($("comparison-use-period").disabled) return;
    resetComparison();
    const current = { start: data.summary.start, end: data.summary.end };
    const start = new Date(current.start + "T00:00:00Z");
    const end = new Date(current.end + "T00:00:00Z");
    const days = Math.round((end - start) / 86400000) + 1;
    if (!calendarDay(current.start) || !calendarDay(current.end) || days < 1) return;
    const baseline = { start: isoDay(new Date(start.getTime() - days * 86400000)), end: isoDay(new Date(start.getTime() - 86400000)) };
    if (!validPeriods({ baseline, current })) return;
    for (const [key, value] of Object.entries({ baseline, current })) for (const edge of ["start", "end"]) $("comparison-" + key + "-" + edge).value = value[edge];
    $("comparison-error").textContent = "";
    $("comparison-state").textContent = "Dates filled. Review both periods, then choose Compare periods.";
  });
  $("comparison-cancel").addEventListener("click", () => {
    if (!comparisonPending) return;
    resetComparison();
    $("comparison-state").textContent = "Stopped waiting. No comparison was accepted; the server may finish processing.";
    if (!$("insights-view").classList.contains("hidden") && data && !dashboardLoading) $("comparison-submit").focus();
  });
  function renderComparison(result) {
    $("comparison-period-label").textContent = "Earlier: " + result.baseline.start + " through " + result.baseline.end + " (" + result.baseline.duration_days + " days). Later: " + result.current.start + " through " + result.current.end + " (" + result.current.duration_days + " days). Inclusive UTC dates.";
    const total = result.total;
    for (const [title, value] of [["Earlier net spending", total.baseline_cents], ["Later net spending", total.current_cents], ["Change in net spending", total.delta_cents]]) {
      const card = node("div", undefined, "comparison-total");
      card.append(node("p", title), node("strong", money(value)));
      $("comparison-totals").append(card);
    }
    const percent = total.percent_status === "defined" ? "Total change: " + percentFromBps(total.percent_change_bps) + "." : "A percentage change is undefined because the earlier net total is " + (total.percent_status === "zero_baseline" ? "zero." : "negative.");
    $("comparison-totals").append(node("p", percent, "chart-note"));
    for (const finding of result.findings) $("comparison-findings").append(node("li", finding.text));
    for (const key of ["categories", "merchants"]) {
      const group = result[key];
      if (!group.rows.length) $("comparison-" + key).append(node("p", "No included records in these observed periods.", "chart-note"));
      for (const row of group.rows) {
        const element = node("div", undefined, "ask-record");
        element.append(node("strong", row.label + (row.label_truncated ? "…" : "")), node("p", money(row.baseline_cents) + " → " + money(row.current_cents) + "; change " + money(row.delta_cents) + "."));
        $("comparison-" + key).append(element);
      }
      if (group.omitted_groups) $("comparison-" + key).append(node("p", group.omitted_groups + " more groups, with combined change " + money(group.omitted_delta_cents) + ". All groups are included in the total.", "chart-note"));
    }
    for (const caveat of result.caveats) $("comparison-caveats").append(node("li", caveat));
    $("comparison-source").textContent = "Source: " + result.source + " · Revision: " + result.source_hash;
    $("comparison-evidence").textContent = JSON.stringify(result, null, 2);
    $("comparison-result").classList.remove("hidden");
  }
  $("comparison-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!data || dashboardLoading || comparisonPending) return;
    const periods = {};
    for (const key of ["baseline", "current"]) periods[key] = Object.freeze({ start: $("comparison-" + key + "-start").value, end: $("comparison-" + key + "-end").value });
    if (!validPeriods(periods)) { resetComparison(); $("comparison-error").textContent = "Choose valid dates for both periods. The earlier period must finish before the later period starts."; return; }
    Object.freeze(periods);
    resetComparison();
    const requestId = comparisonSequence;
    comparisonPending = true;
    comparisonController = new AbortController();
    $("comparison-state").textContent = "Calculating changes from observed records…";
    updateComparisonControls();
    comparisonTimer = setTimeout(() => {
      if (requestId !== comparisonSequence) return;
      resetComparison();
      $("comparison-error").textContent = "The comparison timed out. No result was accepted; you can try again.";
    }, 50000);
    let completion = "";
    try {
      const response = await fetch("/api/v1/spending/compare", { method: "POST", headers: askAuthHeaders(true), body: JSON.stringify(periods), cache: "no-store", redirect: "error", signal: comparisonController.signal });
      if (requestId !== comparisonSequence) return;
      if (response.status === 401) { lock(); $("access-error").textContent = "Unlock with a valid app token to compare spending."; return; }
      if (!response.ok) {
        $("comparison-error").textContent = ({ 422: "The date ranges were rejected. Review both periods.", 429: "Another comparison is running. Wait briefly before trying again.", 503: "Observed data is unavailable for this comparison.", 504: "The comparison reached its time limit." })[response.status] || "The comparison could not be completed. No result was accepted.";
        return;
      }
      const result = await response.json();
      if (requestId !== comparisonSequence) return;
      if (!validComparison(result, periods)) throw new Error("invalid comparison");
      renderComparison(result);
      completion = "Comparison ready. Review the exact totals, contributions and data limitations below.";
    } catch (error) {
      if (requestId !== comparisonSequence || error.name === "AbortError") return;
      $("comparison-error").textContent = "A valid comparison matching your periods could not be received. No result was accepted.";
    } finally {
      if (requestId === comparisonSequence) {
        clearTimeout(comparisonTimer);
        comparisonPending = false;
        $("comparison-state").textContent = completion;
        updateComparisonControls();
      }
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
    if (!data || $("overview-view").classList.contains("hidden")) return;
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
  function validateCategoryMapping(payload) {
    const mapping = payload.category_mapping;
    if (!object(mapping) || mapping.version !== "source-categories-v1" || !Array.isArray(mapping.entries)) return null;
    if (!payload.transactions.every((row) => object(row) && typeof row.category === "string" && Number.isSafeInteger(row.amount_cents) && row.amount_cents >= 0)) return null;
    const rawCategories = new Set(payload.transactions.map((row) => row.category));
    if (mapping.entries.length !== rawCategories.size) return null;
    const result = new Map(), categories = new Map(), details = new Map();
    const validId = (value) => typeof value === "string" && /^[a-z][a-z0-9_]{0,63}$/.test(value);
    const validLabel = (value) => typeof value === "string" && value.trim().length > 0 && value.length <= 100;
    for (const entry of mapping.entries) {
      if (!object(entry) || !rawCategories.has(entry.raw_category) || result.has(entry.raw_category) || !validId(entry.category_id) || !validLabel(entry.category_label) || !["source_mapping", "unclassified"].includes(entry.origin)) return null;
      const noDetail = entry.subcategory_id === null && entry.subcategory_label === null;
      if (!noDetail && !(validId(entry.subcategory_id) && validLabel(entry.subcategory_label))) return null;
      if (entry.origin === "unclassified" && (entry.category_id !== "unclassified" || entry.category_label !== "Unclassified" || !noDetail)) return null;
      if (entry.origin === "source_mapping" && entry.category_id === "unclassified") return null;
      if (categories.has(entry.category_id) && categories.get(entry.category_id) !== entry.category_label) return null;
      categories.set(entry.category_id, entry.category_label);
      if (!noDetail) {
        const identity = JSON.stringify([entry.category_id, entry.subcategory_label]);
        if (details.has(entry.subcategory_id) && details.get(entry.subcategory_id) !== identity) return null;
        details.set(entry.subcategory_id, identity);
      }
      result.set(entry.raw_category, entry);
    }
    return result;
  }
  function transactionAmountBounds() {
    const parse = (input) => {
      const text = input.trim();
      if (!text) return { value: null, invalid: false };
      if (text.length > 20 || !/^\d+(?:\.\d{1,2})?$/.test(text)) return { invalid: true };
      const [whole, fraction = ""] = text.split(".");
      const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, "0"));
      return cents <= BigInt(Number.MAX_SAFE_INTEGER) ? { value: Number(cents), invalid: false } : { invalid: true };
    };
    const minimum = parse($("transaction-min-amount").value);
    const maximum = parse($("transaction-max-amount").value);
    const reversed = !minimum.invalid && !maximum.invalid && minimum.value !== null && maximum.value !== null && minimum.value > maximum.value;
    return {
      minimum: minimum.value, maximum: maximum.value,
      invalidMinimum: minimum.invalid || reversed, invalidMaximum: maximum.invalid || reversed,
      error: minimum.invalid || maximum.invalid ? "Enter positive or zero USD amounts with at most two decimal places, within the supported cent range." : reversed ? "The minimum amount must not exceed the maximum amount." : "",
    };
  }
  function setFilterOptions(id, choices, allLabel, extraChoices = []) {
    const select = $(id);
    const selected = select.value;
    const previousLabel = select.selectedOptions[0]?.textContent.replace(/ \(not in this period\)$/, "") || selected;
    const options = [["", allLabel], ...extraChoices, ...choices];
    if (selected && !options.some(([value]) => value === selected)) options.push([selected, previousLabel + " (not in this period)"]);
    select.replaceChildren(...options.map(([value, text]) => {
      const option = node("option", text);
      option.value = value;
      return option;
    }));
    select.value = selected;
  }
  function updateTransactionFilterOptions() {
    const categories = new Map(), details = new Map();
    const selectedCategory = $("transaction-category").value;
    for (const entry of categoryMappings.values()) {
      categories.set(entry.category_id, entry.category_label);
      if (entry.subcategory_id && (!selectedCategory || selectedCategory === entry.category_id)) details.set(entry.subcategory_id, entry.subcategory_label);
    }
    const ordered = (map) => [...map].sort((left, right) => left[1].localeCompare(right[1], "en") || left[0].localeCompare(right[0], "en"));
    setFilterOptions("transaction-category", ordered(categories), "All categories");
    setFilterOptions("transaction-subcategory", ordered(details), "All subcategories", [["__none__", "No detail supplied"]]);
    const accounts = new Map((data?.accounts || []).map((row) => [row.id, row.nickname + " · " + row.id]));
    setFilterOptions("transaction-account", ordered(accounts), "All accounts");
    const kinds = new Map((data?.transactions || []).map((row) => [row.kind, label(row.kind)]));
    setFilterOptions("transaction-kind", ordered(kinds), "All types");
    updateTransactionFilterAvailability();
  }
  function updateTransactionFilterAvailability() {
    const ready = Boolean(data) && !dashboardLoading;
    for (const id of ["transaction-category", "transaction-subcategory", "transaction-account", "transaction-kind", "transaction-min-amount", "transaction-max-amount", "transaction-search", "transaction-reset-filters"]) $(id).disabled = !ready;
    document.querySelectorAll(".tab[data-filter]").forEach((tab) => { tab.disabled = !ready; });
    $("export-transactions").disabled = !ready || !filteredTransactions().length;
  }
  function resetTransactionFilters() {
    statusFilter = "all";
    page = 0;
    for (const id of ["transaction-category", "transaction-subcategory", "transaction-account", "transaction-kind", "transaction-min-amount", "transaction-max-amount", "transaction-search"]) $(id).value = "";
    document.querySelectorAll(".tab[data-filter]").forEach((tab) => {
      const active = tab.dataset.filter === "all";
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-pressed", String(active));
    });
    $("transaction-filter-error").textContent = "";
    $("transaction-filter-error").classList.add("hidden");
    for (const id of ["transaction-min-amount", "transaction-max-amount"]) $(id).removeAttribute("aria-invalid");
    updateTransactionFilterOptions();
    if (data) renderTransactions();
  }
  function filteredTransactions() {
    if (!data) return [];
    const bounds = transactionAmountBounds();
    if (bounds.error) return [];
    const search = $("transaction-search").value.trim().toLowerCase();
    const category = $("transaction-category").value, detail = $("transaction-subcategory").value;
    const account = $("transaction-account").value, kind = $("transaction-kind").value;
    return data.transactions.filter((row) => {
      const classification = categoryMappings.get(row.category);
      if (!classification) return false;
      const statusMatches = statusFilter === "all" || (statusFilter === "other" ? !["completed", "pending"].includes(row.status) : row.status === statusFilter);
      const detailMatches = !detail || (detail === "__none__" ? classification.subcategory_id === null : classification.subcategory_id === detail);
      const text = [row.merchant, row.description, row.category, classification.category_label, classification.subcategory_label || "", label(row.kind), row.id].join(" ").toLowerCase();
      return statusMatches && (!category || classification.category_id === category) && detailMatches && (!account || row.account_id === account) && (!kind || row.kind === kind) && (bounds.minimum === null || row.amount_cents >= bounds.minimum) && (bounds.maximum === null || row.amount_cents <= bounds.maximum) && (!search || text.includes(search));
    });
  }
  function renderTransactions() {
    if (!data) return;
    const bounds = transactionAmountBounds();
    $("transaction-filter-error").textContent = bounds.error;
    $("transaction-filter-error").classList.toggle("hidden", !bounds.error);
    $("transaction-min-amount").setAttribute("aria-invalid", String(bounds.invalidMinimum));
    $("transaction-max-amount").setAttribute("aria-invalid", String(bounds.invalidMaximum));
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
      const classification = categoryMappings.get(tx.category);
      const category = node("td", undefined, "transaction-category-cell");
      category.append(node("strong", classification.category_label), node("small", classification.subcategory_label || "No detail supplied"), node("small", "Source: " + tx.category, "source-category"));
      row.append(merchant, node("td", dateLabel(tx.occurred_at)), category, amount, status);
      host.append(row);
    });
    if (!rows.length) {
      const cell = node("td", bounds.error ? "Correct the amount range to see matching transactions." : data.transactions.length ? "No transactions match these filters." : "No transactions in this period.", "empty-state");
      cell.colSpan = 5;
      const row = node("tr");
      row.append(cell);
      host.append(row);
    }
    $("transaction-count").textContent = bounds.error ? "Amount filter needs correction · " + data.transactions.length + " transactions in this period" : rows.length + " matching transactions · " + data.transactions.length + " in this period";
    $("page-count").textContent = "Page " + (page + 1) + " of " + pageTotal;
    $("previous-page").disabled = page === 0;
    $("next-page").disabled = page + 1 >= pageTotal;
    $("export-transactions").disabled = !rows.length;
  }
  document.querySelectorAll(".tab[data-filter]").forEach((tab) => tab.addEventListener("click", () => {
    if (!data || dashboardLoading) return;
    statusFilter = tab.dataset.filter;
    page = 0;
    document.querySelectorAll(".tab[data-filter]").forEach((item) => {
      const active = item === tab;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    renderTransactions();
  }));
  function transactionFilterChanged() {
    if (!data || dashboardLoading) return;
    page = 0;
    renderTransactions();
  }
  $("transaction-category").addEventListener("change", () => {
    if (!data || dashboardLoading) return;
    const category = $("transaction-category").value;
    const detail = $("transaction-subcategory").value;
    if (category && detail && detail !== "__none__" && ![...categoryMappings.values()].some((entry) => entry.category_id === category && entry.subcategory_id === detail)) $("transaction-subcategory").value = "";
    updateTransactionFilterOptions();
    transactionFilterChanged();
  });
  for (const id of ["transaction-subcategory", "transaction-account", "transaction-kind"]) $(id).addEventListener("change", transactionFilterChanged);
  for (const id of ["transaction-search", "transaction-min-amount", "transaction-max-amount"]) $(id).addEventListener("input", transactionFilterChanged);
  $("transaction-reset-filters").addEventListener("click", () => { if (data && !dashboardLoading) resetTransactionFilters(); });
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
    updateTransactionFilterOptions();
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
    if (!data || dashboardLoading) return;
    const rows = filteredTransactions();
    if (!rows.length) return;
    download([
      ["ID", "Account", "Date (UTC)", "Merchant", "Description", "Category", "Type", "Status", "Amount (cents)", "Currency", "Broad category", "Subcategory", "Classification origin", "Classification version"],
      ...rows.map((row) => {
        const classification = categoryMappings.get(row.category);
        return [row.id, row.account_id, row.occurred_at, row.merchant, row.description, row.category, row.kind, row.status, row.amount_cents, "USD", classification.category_label, classification.subcategory_label || "", classification.origin, data.category_mapping.version];
      }),
    ], "ledgerly-transactions-" + (data.summary.end || "empty") + ".csv");
  });
  void refresh();
})();
