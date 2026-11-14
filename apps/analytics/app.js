/* Blockwise research desk: all runtime results come from the experiment API. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const API = '/api/v1', PAGE_SIZE = 50, TOKEN_KEY = 'blockwise.analytics.access';
  const moneyFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
  const numbers = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
  const dates = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  const state = { mode: 'participants', scenario: '', offset: 0, total: 0, token: '', generation: 0, controller: null, detailController: null, detailGeneration: 0, summary: null, scenarios: [], returnFocus: null, busy: false, pageBusy: false };
  const text = (id, value) => { $(id).textContent = value; };
  const finite = (v) => typeof v === 'number' && Number.isFinite(v);
  const count = (v) => finite(v) ? Math.max(0, Math.trunc(v)) : 0;
  const money = (v) => finite(v) ? moneyFormat.format(v / 100) : '—';
  const minutes = (v) => finite(v) ? numbers.format(v) + ' min' : '—';
  const signedMinutes = (v) => finite(v) ? (v > 0 ? '+' : '') + numbers.format(v) + ' min' : '—';
  const plural = (v, singular, multiple) => count(v) + ' ' + (count(v) === 1 ? singular : (multiple || singular + 's'));
  const shortId = (v) => String(v || '').slice(0, 8);
  function formattedDate(v) { const d = new Date(v); return Number.isFinite(d.getTime()) ? dates.format(d) : 'Unknown time'; }
  function make(tag, cls, value) { const n = document.createElement(tag); if (cls) n.className = cls; if (value !== undefined) n.textContent = value; return n; }
  function svgNode(tag, attrs) { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); Object.entries(attrs || {}).forEach(([k, v]) => n.setAttribute(k, String(v))); return n; }
  function svgText(x, y, value, cls, anchor) { const n = svgNode('text', { x, y, class: cls, 'text-anchor': anchor || 'middle' }); n.textContent = value; return n; }
  function emptyRow(id, columns, message) { const td = make('td', 'table-empty', message); td.colSpan = columns; const row = make('tr'); row.append(td); $(id).replaceChildren(row); }
  function query(extra) { const p = new URLSearchParams({ data_mode: state.mode }); if (state.scenario) p.set('scenario_id', state.scenario); Object.entries(extra || {}).forEach(([k, v]) => p.set(k, String(v))); return p.toString(); }
  function setError(message) { $('error-message').hidden = !message; text('error-message', message || ''); }
  function openAccess(focus) { $('access-panel').hidden = false; $('access-toggle').setAttribute('aria-expanded', 'true'); if (focus) $('access-token').focus(); }
  async function request(path, signal, asBlob) {
    let response;
    try { response = await fetch(API + path, { headers: state.token ? { Authorization: 'Bearer ' + state.token } : {}, signal, cache: 'no-store', credentials: 'same-origin' }); }
    catch (error) { if (error.name === 'AbortError') throw error; throw new Error('Could not reach the experiment. Check that the app is running, then try again.'); }
    if (!response.ok) {
      const error = new Error(response.status === 401 || response.status === 403
        ? 'This research desk requires an analytics access token. Open access settings and apply the token for this installation.'
        : response.status === 404 ? 'That experiment record is no longer available. Refresh to see the current sessions.'
          : 'The experiment could not load these results. Please try again.');
      error.status = response.status;
      throw error;
    }
    if (asBlob) return response.blob();
    try { return await response.json(); } catch (_) { throw new Error('The experiment returned an unreadable response. Please try refreshing.'); }
  }
  function updatePagination() {
    const current = Math.min(PAGE_SIZE, Math.max(0, state.total - state.offset));
    text('page-label', state.total ? (state.offset + 1) + '–' + (state.offset + current) + ' of ' + plural(state.total, 'session') : 'No sessions');
    $('previous-page').disabled = state.busy || state.pageBusy || state.offset <= 0;
    $('next-page').disabled = state.busy || state.pageBusy || state.offset + PAGE_SIZE >= state.total;
  }
  function setBusy(busy) {
    state.busy = busy; $('results').setAttribute('aria-busy', String(busy));
    $('refresh-button').disabled = busy; $('export-button').disabled = busy || !state.summary;
    text('loading-status', busy ? 'Loading experiment results.' : 'Experiment results updated.');
    updatePagination();
  }
  function clearResults() {
    state.summary = null; state.total = 0;
    $('session-table').setAttribute('aria-busy', 'false');
    ['metric-sessions', 'metric-cost', 'metric-savings', 'metric-time'].forEach((id) => text(id, '—'));
    text('metric-sessions-note', 'Loading the experiment'); text('metric-cost-note', 'Complete basket + modeled transport');
    text('metric-savings-note', 'Same scenario and constraints'); text('metric-time-note', 'Revised minus initial trip time');
    text('updated-at', 'Loading results…'); text('tradeoffs-count', '0 choices'); text('scenario-count', '0 scenarios'); text('session-total', '0 sessions');
    $('empty-state').hidden = true; $('summary-notices').replaceChildren();
    $('scatter-chart').replaceChildren(make('p', 'chart-empty', 'Loading submitted choices…'));
    $('store-chart').replaceChildren(make('p', 'chart-empty', 'Loading store choices…'));
    emptyRow('tradeoffs-table', 5, 'Loading choices…'); emptyRow('scenario-table', 5, 'Loading scenarios…'); emptyRow('session-table', 6, 'Loading sessions…');
  }
  function renderContext() {
    const challengeLink = new URL($('participant-link').href);
    challengeLink.search = state.mode === 'demo' ? '?demo=1' : '';
    $('participant-link').href = challengeLink.href;
    $('empty-challenge-link').href = challengeLink.href;
    const descriptions = { participants: 'Participant submissions only. Demo sessions are excluded.', demo: 'Demonstration sessions only. These are simulated choices, not participant observations.', all: 'Participant and demonstration sessions combined. Use Participants for experimental results.' };
    text('data-context', descriptions[state.mode]); $('mode-notice').hidden = state.mode === 'participants';
    text('mode-notice', state.mode === 'all' ? 'Mixed data: every metric below includes demonstration sessions. Do not report this view as participant findings.' : 'Demonstration data: use this view to explore the dashboard. These choices were generated for the demo.');
  }
  function renderSummary(summary) {
    state.summary = summary;
    const sessions = count(summary.session_count), initial = count(summary.initial_choice_count), revised = count(summary.revised_choice_count), pairs = count(summary.matched_pair_count);
    text('metric-sessions', sessions.toLocaleString('en-US'));
    text('metric-sessions-note', plural(initial, 'initial choice') + ' · ' + plural(revised, 'revision'));
    text('metric-cost', money(summary.mean_initial_cost_cents));
    text('metric-cost-note', initial ? 'Median ' + money(summary.median_initial_cost_cents) + ' · ' + plural(initial, 'initial choice') : 'No initial choices in this view');
    text('metric-savings', money(summary.mean_savings_cents));
    text('metric-savings-note', pairs ? plural(pairs, 'matched pair') + ' · positive means less spent' : 'Needs an initial and revised choice with matching constraints');
    text('metric-time', signedMinutes(summary.mean_time_change_minutes));
    text('metric-time-note', pairs ? (summary.mean_time_change_minutes > 0 ? 'Longer' : summary.mean_time_change_minutes < 0 ? 'Shorter' : 'Unchanged') + ' trips · ' + plural(pairs, 'matched pair') : 'No matching before-and-after pairs yet');
    $('empty-state').hidden = sessions > 0;
    const scenarioName = state.scenarios.find((item) => item.id === state.scenario)?.name;
    text('empty-title', state.mode === 'demo' ? 'A clean slate for demo data.' : state.mode === 'all' ? 'The notebook is ready.' : 'Your first choice starts the story.');
    text('empty-description', state.mode === 'demo' ? 'No demonstration sessions match this view. Demo data is added explicitly and is always kept separate from participant results.' : 'No ' + (state.mode === 'all' ? '' : 'participant ') + 'sessions ' + (scenarioName ? 'for ' + scenarioName + ' ' : '') + 'have been collected yet. Open the challenge and submit a shopping plan to see it here.');
    $('empty-challenge-link').hidden = state.mode === 'demo';
    renderScatter(Array.isArray(summary.tradeoffs) ? summary.tradeoffs : []);
    renderStores(Array.isArray(summary.store_choices) ? summary.store_choices : []);
    renderScenarios(Array.isArray(summary.scenario_breakdown) ? summary.scenario_breakdown : []);
    const notices = (Array.isArray(summary.notices) ? summary.notices : []).filter((v) => typeof v === 'string').map((v) => make('li', '', v));
    if (count(summary.infeasible_choice_count)) notices.push(make('li', '', plural(summary.infeasible_choice_count, 'submitted choice') + ' exceeded a stated limit. These choices remain visible as observations.'));
    $('summary-notices').replaceChildren(...notices);
  }
  function niceMaximum(value) {
    if (!finite(value) || value <= 0) return 10;
    const target = value * 1.15, power = Math.pow(10, Math.floor(Math.log10(target))), fraction = target / power;
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power;
  }
  function renderScatter(rawPoints) {
    const points = rawPoints.filter((p) => finite(p.cost_cents) && finite(p.minutes) && p.cost_cents >= 0 && p.minutes >= 0);
    text('tradeoffs-count', plural(points.length, 'choice'));
    const rows = points.map((p) => {
      const row = make('tr'), cell = make('td'), button = make('button', 'row-link', shortId(p.session_id) + (p.is_demo ? ' · demo' : ''));
      button.type = 'button'; button.dataset.sessionId = p.session_id; button.setAttribute('aria-label', 'Open session ' + shortId(p.session_id));
      cell.append(button); row.append(cell, make('td', '', p.stage === 'revised' ? 'Revised' : 'Initial'), make('td', '', money(p.cost_cents)), make('td', '', minutes(p.minutes)), make('td', '', p.feasible ? 'Yes' : 'No'));
      return row;
    });
    if (rows.length) $('tradeoffs-table').replaceChildren(...rows); else emptyRow('tradeoffs-table', 5, 'No submitted choices in this view.');
    if (!points.length) { $('scatter-chart').replaceChildren(make('p', 'chart-empty', 'Submitted plans will appear here, one point per choice.')); return; }
    const width = 600, height = 310, left = 62, top = 21, right = 23, bottom = 53;
    const pw = width - left - right, ph = height - top - bottom;
    const maxMinutes = niceMaximum(Math.max(...points.map((p) => p.minutes)));
    const maxCost = niceMaximum(Math.max(...points.map((p) => p.cost_cents / 100)));
    const svg = svgNode('svg', { viewBox: '0 0 ' + width + ' ' + height, role: 'img', 'aria-labelledby': 'plot-title plot-description' });
    const title = svgNode('title', { id: 'plot-title' }), description = svgNode('desc', { id: 'plot-description' });
    title.textContent = 'Money and time for submitted shopping plans';
    description.textContent = points.length + ' choices. Horizontal axis: total trip minutes. Vertical axis: total cost in dollars. Green is initial, coral is revised. Crosses exceed a limit. Exact values are in the chart values table.';
    svg.append(title, description);
    for (let tick = 0; tick <= 5; tick++) {
      const x = left + pw * tick / 5, y = top + ph * (1 - tick / 5);
      svg.append(svgNode('line', { x1: left, y1: y, x2: width - right, y2: y, class: 'chart-grid-line' }));
      svg.append(svgText(left - 10, y + 4, '$' + numbers.format(maxCost * tick / 5), 'chart-axis-text', 'end'));
      svg.append(svgText(x, height - bottom + 22, numbers.format(maxMinutes * tick / 5), 'chart-axis-text'));
    }
    svg.append(svgText(left + pw / 2, height - 9, 'Total trip time · minutes', 'chart-axis-label'), svgText(left, 11, 'Total cost', 'chart-axis-label', 'start'));
    points.forEach((p) => {
      const x = left + pw * p.minutes / maxMinutes, y = top + ph * (1 - p.cost_cents / 100 / maxCost);
      const label = (p.stage === 'revised' ? 'Revised' : 'Initial') + ' choice, ' + money(p.cost_cents) + ', ' + minutes(p.minutes) + ', ' + (p.feasible ? 'within limits' : 'over a limit') + (p.is_demo ? ', demo data' : '') + '. Open session ' + shortId(p.session_id);
      const g = svgNode('g', { tabindex: '0', role: 'button', 'aria-label': label, 'data-session-id': p.session_id, style: 'cursor:pointer' });
      const tip = svgNode('title'); tip.textContent = label;
      g.append(tip, svgNode('circle', { cx: x, cy: y, r: 7, fill: p.stage === 'revised' ? '#d97f62' : '#173f35', opacity: '.8', stroke: '#fffef9', 'stroke-width': '2' }));
      if (!p.feasible) g.append(svgNode('path', { d: 'M' + (x - 3) + ' ' + (y - 3) + 'L' + (x + 3) + ' ' + (y + 3) + 'M' + (x + 3) + ' ' + (y - 3) + 'L' + (x - 3) + ' ' + (y + 3), stroke: 'white', 'stroke-width': '1.5', fill: 'none', 'pointer-events': 'none' }));
      svg.append(g);
    });
    $('scatter-chart').replaceChildren(svg);
  }
  function renderStores(stores) {
    if (!stores.length) { $('store-chart').replaceChildren(make('p', 'chart-empty', 'Store choices appear after the first submitted plan.')); return; }
    const sorted = stores.slice().sort((a, b) => count(b.choice_count) - count(a.choice_count) || String(a.store_name).localeCompare(String(b.store_name)));
    const maximum = Math.max(1, ...sorted.map((s) => count(s.choice_count)));
    $('store-chart').replaceChildren(...sorted.map((s) => {
      const row = make('div', 'store-row'), heading = make('div', 'store-row-head'), track = make('div', 'store-track'), fill = make('span', 'store-fill');
      const label = make('div', 'store-label');
      label.append(make('strong', '', s.store_name || s.store_id));
      if (!state.scenario && s.scenario_id) {
        const scenarioName = state.summary?.scenario_breakdown?.find((item) => item.scenario_id === s.scenario_id)?.scenario_name
          || state.scenarios.find((item) => item.id === s.scenario_id)?.name
          || s.scenario_id;
        label.append(make('small', 'store-scenario', scenarioName));
      }
      heading.append(label, make('span', '', plural(s.choice_count, 'plan')));
      track.setAttribute('aria-hidden', 'true'); fill.style.width = (count(s.choice_count) / maximum * 100) + '%'; track.append(fill); row.append(heading, track); return row;
    }));
  }
  function renderScenarios(scenarios) {
    text('scenario-count', plural(scenarios.length, 'scenario'));
    if (!scenarios.length) { emptyRow('scenario-table', 5, 'No scenario sessions in this view.'); return; }
    $('scenario-table').replaceChildren(...scenarios.map((s) => {
      const row = make('tr'), action = make('td'), button = make('button', 'row-link', 'View →');
      button.type = 'button'; button.dataset.scenarioId = s.scenario_id; button.setAttribute('aria-label', 'Filter to ' + (s.scenario_name || s.scenario_id)); action.append(button);
      row.append(make('td', 'scenario-name', s.scenario_name || s.scenario_id), make('td', '', count(s.sessions)), make('td', '', count(s.initial_choices)), make('td', '', count(s.revised_choices)), action); return row;
    }));
  }
  function renderSessions(payload) {
    const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    state.total = count(payload.total); text('session-total', plural(state.total, 'session'));
    if (!sessions.length) { emptyRow('session-table', 6, state.offset ? 'No sessions on this page. Return to the previous page.' : 'No sessions match the current filters.'); updatePagination(); return; }
    $('session-table').replaceChildren(...sessions.map((s) => {
      const row = make('tr'), identity = make('td'), progress = make('td'), costs = make('td', 'cost-comparison'), detail = make('td');
      identity.append(make('span', 'session-id', shortId(s.id)), make('p', 'session-scenario', s.scenario_name || s.scenario_id));
      if (s.is_demo) identity.append(make('span', 'tag tag-demo', 'Demo'));
      const started = make('td', 'session-time', formattedDate(s.created_at)); started.title = 'Shown in your local time zone';
      progress.append(make('span', 'tag' + (!s.initial ? ' tag-muted' : ''), s.latest_revised ? 'Revised' : s.initial ? 'Initial submitted' : 'Started'));
      if (s.initial) { costs.append(document.createTextNode(money(s.initial.evaluation?.total_cost_cents))); if (s.latest_revised) costs.append(make('span', '', '→'), document.createTextNode(money(s.latest_revised.evaluation?.total_cost_cents))); } else costs.textContent = '—';
      const comparison = make('td', 'comparison-caption', s.latest_revised ? (s.comparable ? 'Matched pair' : 'Constraints changed') : 'No pair yet');
      const button = make('button', 'row-link', 'Open →'); button.type = 'button'; button.dataset.sessionId = s.id; button.setAttribute('aria-label', 'Open session ' + shortId(s.id)); detail.append(button);
      row.append(identity, started, progress, costs, comparison, detail); return row;
    }));
    updatePagination();
  }
  async function refresh() {
    const generation = ++state.generation;
    if (state.controller) state.controller.abort();
    state.controller = new AbortController(); state.offset = 0; state.pageBusy = false;
    setError(''); renderContext(); clearResults(); setBusy(true);
    try {
      const [summary, sessions] = await Promise.all([request('/analytics/summary?' + query(), state.controller.signal), request('/analytics/sessions?' + query({ limit: PAGE_SIZE, offset: 0 }), state.controller.signal)]);
      if (generation !== state.generation) return;
      if (!summary || typeof summary !== 'object' || !sessions || typeof sessions !== 'object') throw new Error('The experiment returned incomplete results. Please refresh.');
      renderSummary(summary); renderSessions(sessions);
      text('updated-at', 'Updated ' + new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date()));
    } catch (error) {
      if (generation !== state.generation || error.name === 'AbortError') return;
      setError(error.message); text('updated-at', 'Results unavailable'); text('metric-sessions-note', 'Results could not be loaded');
      $('scatter-chart').replaceChildren(make('p', 'chart-empty', 'Results unavailable. Refresh to try again.'));
      $('store-chart').replaceChildren(make('p', 'chart-empty', 'Results unavailable.'));
      emptyRow('tradeoffs-table', 5, 'Results unavailable.'); emptyRow('scenario-table', 5, 'Results unavailable.'); emptyRow('session-table', 6, 'Results unavailable.');
      if (error.status === 401 || error.status === 403) openAccess(true);
    } finally { if (generation === state.generation) { setBusy(false); if (!state.summary) text('loading-status', 'Results could not be loaded.'); } }
  }
  async function loadPage(nextOffset) {
    if (state.busy || state.pageBusy) return;
    const generation = state.generation, oldOffset = state.offset;
    state.offset = Math.max(0, nextOffset); state.pageBusy = true; updatePagination(); $('session-table').setAttribute('aria-busy', 'true'); setError('');
    try {
      const payload = await request('/analytics/sessions?' + query({ limit: PAGE_SIZE, offset: state.offset }), state.controller?.signal);
      if (generation !== state.generation) return;
      renderSessions(payload); text('loading-status', 'Session page updated.');
    } catch (error) {
      if (generation !== state.generation || error.name === 'AbortError') return;
      state.offset = oldOffset; setError(error.message); if (error.status === 401 || error.status === 403) openAccess(true);
    } finally { if (generation === state.generation) { state.pageBusy = false; $('session-table').setAttribute('aria-busy', 'false'); updatePagination(); } }
  }
  function closeDetail() {
    state.detailGeneration++; if (state.detailController) state.detailController.abort();
    const dialog = $('session-dialog'); if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
    state.returnFocus?.focus();
  }
  function closeDetailIfOpen() { if ($('session-dialog').open) closeDetail(); }
  async function openDetail(id, trigger) {
    if (!id) return;
    const generation = ++state.detailGeneration; state.returnFocus = trigger || document.activeElement;
    if (state.detailController) state.detailController.abort(); state.detailController = new AbortController();
    text('detail-title', 'Session ' + shortId(id)); $('detail-content').replaceChildren(make('p', 'detail-intro', 'Loading this session…'));
    const dialog = $('session-dialog'); if (!dialog.open) { if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', ''); }
    $('close-detail').focus();
    try { const detail = await request('/analytics/sessions/' + encodeURIComponent(id), state.detailController.signal); if (generation === state.detailGeneration) renderDetail(detail); }
    catch (error) { if (generation === state.detailGeneration && error.name !== 'AbortError') $('detail-content').replaceChildren(make('p', 'detail-error', error.message)); }
  }
  function renderDetail(detail) {
    const scenario = detail.scenario || detail.scenario_snapshot || {}, constraints = detail.constraints || {}, choices = Array.isArray(detail.choices) ? detail.choices : [];
    const storeNames = Object.fromEntries((scenario.stores || []).map((s) => [s.id, s.name]));
    const productNames = Object.fromEntries((scenario.basket || []).map((p) => [p.id, p.name + ' · ' + p.quantity + ' × ' + p.unit]));
    text('detail-title', scenario.name || detail.scenario_name || 'Session ' + shortId(detail.id));
    const content = $('detail-content'); content.replaceChildren();
    content.append(make('p', 'detail-intro', 'Session ' + shortId(detail.id) + ' · ' + formattedDate(detail.created_at) + ' · ' + (detail.is_demo ? 'Demonstration data' : 'Participant submission')));
    const meta = make('dl', 'detail-meta');
    [['Starting allowance', money(constraints.budget_cents)], ['Starting time limit', minutes(constraints.time_budget_minutes)], ['Transport', constraints.travel_mode === 'walk' ? 'Walking' : constraints.travel_mode === 'drive' ? 'Driving' : '—'], ['Store limit', finite(constraints.max_stores) ? plural(constraints.max_stores, 'store') : '—']].forEach(([label, value]) => { const group = make('div'); group.append(make('dt', '', label), make('dd', '', value)); meta.append(group); });
    content.append(meta);
    if (detail.is_demo) content.append(make('p', 'detail-notice', 'This is a demonstration session. It is excluded from participant-only findings.'));
    content.append(make('h3', 'detail-section-title', 'Choice history · ' + plural(choices.length, 'submission')));
    if (!choices.length) content.append(make('p', 'detail-intro', 'This session has started but no shopping plan has been submitted.'));
    choices.forEach((choice, index) => {
      const e = choice.evaluation || {}, card = make('article', 'choice-card'), heading = make('div', 'choice-heading');
      heading.append(make('h3', '', (choice.stage === 'initial' ? 'Initial choice' : 'Revised choice') + ' · ' + formattedDate(choice.created_at)), make('span', 'tag' + (e.feasible ? '' : ' tag-coral'), e.feasible ? 'Within limits' : 'Over a limit')); card.append(heading);
      const metrics = make('div', 'choice-metrics');
      [[money(e.total_cost_cents), 'Total cost'], [minutes(e.total_minutes), 'Complete trip'], [money(e.transport_cost_cents), 'Transport included']].forEach(([value, label]) => { const metric = make('div', '', value); metric.append(make('span', '', label)); metrics.append(metric); });
      card.append(metrics);
      card.append(make('p', 'choice-route', Array.isArray(e.route) ? e.route.map((s) => s === 'home' ? 'Home' : (storeNames[s] || s)).join(' → ') : 'Route unavailable'));
      const c = e.constraints || constraints;
      card.append(make('p', 'choice-route', 'Limits: ' + money(c.budget_cents) + ' · ' + minutes(c.time_budget_minutes) + ' · ' + (c.travel_mode === 'walk' ? 'walking' : 'driving') + ' · ' + plural(c.max_stores, 'store')));
      if (Array.isArray(e.violations) && e.violations.length) { const list = make('ul', 'choice-violations'); e.violations.forEach((v) => list.append(make('li', '', typeof v === 'string' ? v : 'This plan exceeds a stated limit.'))); card.append(list); }
      const disclosure = make('details'); disclosure.append(make('summary', '', 'Inspect basket assignments'));
      const table = make('table'), head = make('thead'), headRow = make('tr'), body = make('tbody');
      ['Basket item', 'Store'].forEach((v) => { const th = make('th', '', v); th.scope = 'col'; headRow.append(th); }); head.append(headRow);
      Object.entries(e.assignments || {}).forEach(([p, s]) => { const row = make('tr'); row.append(make('td', '', productNames[p] || p), make('td', '', storeNames[s] || s)); body.append(row); });
      table.append(make('caption', 'sr-only', 'Basket assignments for choice ' + (index + 1)), head, body); disclosure.append(table); card.append(disclosure); content.append(card);
    });
    const sources = Object.values(scenario.provenance || {}).filter((v) => typeof v === 'string');
    content.append(make('p', 'detail-source', sources.length ? sources.join(' ') : 'Choices were made inside a simulation with fictional prices and modeled travel. They are not real purchase records.'));
  }
  async function exportCsv() {
    if (!state.summary) return;
    const button = $('export-button'), mode = state.mode; button.disabled = true; setError('');
    try {
      const blob = await request('/analytics/export?' + query(), undefined, true), url = URL.createObjectURL(blob), link = make('a');
      link.href = url; link.download = 'blockwise-' + mode + '-' + new Date().toISOString().slice(0, 10) + '.csv'; document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000); text('loading-status', 'CSV export downloaded.');
    } catch (error) { setError(error.message); if (error.status === 401 || error.status === 403) openAccess(true); }
    finally { button.disabled = state.busy || !state.summary; }
  }
  function saveToken(value) {
    state.token = value.trim();
    try { if (state.token) sessionStorage.setItem(TOKEN_KEY, state.token); else sessionStorage.removeItem(TOKEN_KEY); } catch (_) { /* In-memory access still works when storage is unavailable. */ }
    $('access-token').value = state.token;
  }
  function init() {
    try { state.token = sessionStorage.getItem(TOKEN_KEY) || ''; } catch (_) { state.token = ''; }
    $('access-token').value = state.token;
    const participant = new URL(window.location.href); participant.port = '5183'; participant.pathname = '/'; participant.search = ''; participant.hash = '';
    if (['http:', 'https:'].includes(participant.protocol)) { $('participant-link').href = participant.href; $('empty-challenge-link').href = participant.href; }
    $('data-modes').addEventListener('change', (event) => { if (event.target.name !== 'data-mode' || !['participants', 'demo', 'all'].includes(event.target.value)) return; state.mode = event.target.value; closeDetailIfOpen(); refresh(); });
    $('scenario-filter').addEventListener('change', () => { state.scenario = $('scenario-filter').value; closeDetailIfOpen(); refresh(); });
    $('refresh-button').addEventListener('click', refresh); $('export-button').addEventListener('click', exportCsv);
    $('previous-page').addEventListener('click', () => loadPage(state.offset - PAGE_SIZE)); $('next-page').addEventListener('click', () => loadPage(state.offset + PAGE_SIZE));
    $('access-toggle').addEventListener('click', () => { const expanded = $('access-panel').hidden; $('access-panel').hidden = !expanded; $('access-toggle').setAttribute('aria-expanded', String(expanded)); if (expanded) $('access-token').focus(); });
    $('access-form').addEventListener('submit', (event) => { event.preventDefault(); saveToken($('access-token').value); closeDetailIfOpen(); refresh(); });
    $('clear-token').addEventListener('click', () => { saveToken(''); closeDetailIfOpen(); refresh(); });
    $('close-detail').addEventListener('click', closeDetail);
    $('session-dialog').addEventListener('cancel', (event) => { event.preventDefault(); closeDetail(); });
    $('session-dialog').addEventListener('click', (event) => { if (event.target === $('session-dialog')) closeDetail(); });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && $('session-dialog').open) { event.preventDefault(); closeDetail(); }
      const trigger = event.target.closest?.('[data-session-id]');
      if (trigger && trigger.tagName.toLowerCase() === 'g' && ['Enter', ' '].includes(event.key)) { event.preventDefault(); openDetail(trigger.dataset.sessionId, trigger); }
    });
    document.addEventListener('click', (event) => {
      const session = event.target.closest?.('[data-session-id]');
      if (session) { openDetail(session.dataset.sessionId, session); return; }
      const scenario = event.target.closest?.('[data-scenario-id]');
      if (scenario) {
        state.scenario = scenario.dataset.scenarioId;
        if (![...$('scenario-filter').options].some((o) => o.value === state.scenario)) { const o = make('option', '', scenario.closest('tr')?.querySelector('.scenario-name')?.textContent || state.scenario); o.value = state.scenario; $('scenario-filter').append(o); }
        $('scenario-filter').value = state.scenario; refresh();
      }
    });
    request('/scenarios').then((payload) => {
      state.scenarios = Array.isArray(payload.scenarios) ? payload.scenarios : [];
      state.scenarios.forEach((s) => { if ([...$('scenario-filter').options].some((o) => o.value === s.id)) return; const o = make('option', '', s.name); o.value = s.id; $('scenario-filter').append(o); });
    }).catch(() => { /* Historical scenarios remain available through the breakdown. */ });
    refresh();
  }
  init();
})();
