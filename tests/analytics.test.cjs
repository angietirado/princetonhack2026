const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'apps/analytics/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'apps/analytics/app.js'), 'utf8');
const constraints = { budget_cents: 7000, time_budget_minutes: 45, travel_mode: 'drive', max_stores: 2 };
const scenario = { id: 'houston-heights', name: 'Heights weekday shop', version: 'v1', stores: [{ id: 'corner', name: 'Corner Market' }], basket: [{ id: 'milk', name: 'Milk', quantity: 1, unit: '1 gallon' }], provenance: { prices: 'Simulated prices.', travel: 'Modeled travel.' } };
const evaluation = { total_cost_cents: 6400, total_minutes: 28, transport_cost_cents: 200, basket_cost_cents: 6200, feasible: true, constraints, assignments: { milk: 'corner' }, route: ['home', 'corner', 'home'], violations: [] };
const initial = { id: 'choice-1', stage: 'initial', evaluation, created_at: '2026-11-14T12:00:00Z' };
const revised = { id: 'choice-2', stage: 'revised', evaluation: { ...evaluation, total_cost_cents: 5500, total_minutes: 43 }, created_at: '2026-11-14T12:02:00Z' };
function summary(overrides = {}) {
  return { data_mode: 'participants', session_count: 2, completed_session_count: 1, initial_choice_count: 1, revised_choice_count: 1, matched_pair_count: 1, infeasible_choice_count: 0, mean_initial_cost_cents: 6400, median_initial_cost_cents: 6400, mean_initial_minutes: 28, mean_savings_cents: 900, mean_time_change_minutes: 15, scenario_breakdown: [{ scenario_id: scenario.id, scenario_name: scenario.name, sessions: 2, initial_choices: 1, revised_choices: 1 }], store_choices: [{ store_id: 'corner', store_name: 'Corner Market', choice_count: 2 }], tradeoffs: [{ session_id: 'session-1', scenario_id: scenario.id, stage: 'initial', cost_cents: 6400, minutes: 28, feasible: true, is_demo: false }, { session_id: 'session-1', scenario_id: scenario.id, stage: 'revised', cost_cents: 5500, minutes: 43, feasible: true, is_demo: false }], notices: ['Small experimental sample.'], ...overrides };
}
function session(overrides = {}) {
  return { id: 'session-1', scenario_id: scenario.id, scenario_name: scenario.name, is_demo: false, created_at: '2026-11-14T12:00:00Z', constraints, initial, latest_revised: revised, choice_count: 2, comparable: true, ...overrides };
}
const detail = { id: 'session-1', scenario, constraints, created_at: '2026-11-14T12:00:00Z', is_demo: false, choices: [initial, revised] };
const response = (payload, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => payload, blob: async () => payload });
const flush = async () => { for (let i = 0; i < 4; i++) await new Promise((resolve) => setImmediate(resolve)); };
function harness(handler, token) {
  const console = new VirtualConsole();
  const errors = [];
  console.on('jsdomError', (error) => errors.push(error));
  const dom = new JSDOM(html, { url: 'http://localhost:5184/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: console });
  const calls = [], downloads = [];
  const { window } = dom;
  window.URL.createObjectURL = () => 'blob:export';
  window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = function () { downloads.push({ href: this.href, download: this.download }); };
  if (token) window.sessionStorage.setItem('blockwise.analytics.access', token);
  window.fetch = async (url, options) => {
    const parsed = new URL(url, window.location.href);
    calls.push({ url: parsed, options });
    if (handler) {
      const custom = await handler(parsed, options);
      if (custom !== undefined) return custom;
    }
    if (parsed.pathname === '/api/v1/scenarios') return response({ scenarios: [scenario] });
    if (parsed.pathname === '/api/v1/analytics/summary') return response(summary());
    if (parsed.pathname === '/api/v1/analytics/sessions') return response({ sessions: [session()], total: 1, limit: 50, offset: 0 });
    if (parsed.pathname === '/api/v1/analytics/sessions/session-1') return response(detail);
    if (parsed.pathname === '/api/v1/analytics/export') return response(new window.Blob(['stage,cost\ninitial,6400'], { type: 'text/csv' }));
    return response({}, 404);
  };
  window.eval(script);
  return { dom, window, document: window.document, calls, downloads, errors, close: () => window.close() };
}
function clickMode(app, mode) { app.document.querySelector('input[name="data-mode"][value="' + mode + '"]').click(); }

test('analytics loads participant-only results and renders honest paired metrics and chart values', async () => {
  const app = harness();
  try {
    await flush();
    const analytics = app.calls.filter((call) => call.url.pathname.startsWith('/api/v1/analytics/'));
    assert.ok(analytics.every((call) => call.url.searchParams.get('data_mode') === 'participants'));
    assert.equal(app.document.getElementById('metric-sessions').textContent, '2');
    assert.equal(app.document.getElementById('metric-cost').textContent, '$64.00');
    assert.equal(app.document.getElementById('metric-savings').textContent, '$9.00');
    assert.equal(app.document.getElementById('metric-time').textContent, '+15 min');
    assert.match(app.document.getElementById('metric-savings-note').textContent, /1 matched pair/);
    assert.equal(app.document.querySelectorAll('#scatter-chart circle').length, 2);
    assert.equal(app.document.querySelectorAll('#tradeoffs-table tr').length, 2);
    assert.match(app.document.getElementById('pair-note').textContent, /do not prove/);
    assert.equal(app.document.getElementById('mode-notice').hidden, true);
    assert.equal(app.document.getElementById('results').getAttribute('aria-busy'), 'false');
    assert.equal(app.errors.length, 0);
  } finally { app.close(); }
});

test('demo and mixed data are explicit and demo challenge links stay in demo mode', async () => {
  const app = harness();
  try {
    await flush();
    clickMode(app, 'demo');
    await flush();
    assert.equal(app.calls.filter((call) => call.url.pathname.endsWith('/summary')).at(-1).url.searchParams.get('data_mode'), 'demo');
    assert.match(app.document.getElementById('mode-notice').textContent, /Demonstration data/);
    assert.equal(app.document.getElementById('mode-notice').hidden, false);
    assert.equal(new URL(app.document.getElementById('participant-link').href).searchParams.get('demo'), '1');
    clickMode(app, 'all'); await flush();
    assert.match(app.document.getElementById('mode-notice').textContent, /Mixed data/);
    assert.equal(new URL(app.document.getElementById('participant-link').href).search, '');
    clickMode(app, 'participants'); await flush();
    assert.equal(app.document.getElementById('mode-notice').hidden, true);
  } finally { app.close(); }
});

test('no observations renders an empty state and unknown means stay unknown, not zero', async () => {
  const app = harness((url) => {
    if (url.pathname.endsWith('/summary')) return response(summary({ session_count: 0, completed_session_count: 0, initial_choice_count: 0, revised_choice_count: 0, matched_pair_count: 0, mean_initial_cost_cents: null, median_initial_cost_cents: null, mean_savings_cents: null, mean_time_change_minutes: null, scenario_breakdown: [], store_choices: [], tradeoffs: [] }));
    if (url.pathname.endsWith('/sessions')) return response({ sessions: [], total: 0 });
  });
  try {
    await flush();
    assert.equal(app.document.getElementById('metric-sessions').textContent, '0');
    assert.equal(app.document.getElementById('metric-cost').textContent, '—');
    assert.equal(app.document.getElementById('metric-savings').textContent, '—');
    assert.equal(app.document.getElementById('empty-state').hidden, false);
    assert.match(app.document.getElementById('empty-description').textContent, /No participant sessions/);
    assert.equal(app.document.querySelector('#scatter-chart svg'), null);
    assert.equal(app.document.getElementById('next-page').disabled, true);
  } finally { app.close(); }
});

test('pagination requests the correct page and scenario changes reset the offset', async () => {
  const app = harness((url) => {
    if (url.pathname.endsWith('/sessions')) return response({ sessions: [session({ id: url.searchParams.get('offset') === '50' ? 'session-51' : 'session-1' })], total: 51, limit: 50, offset: Number(url.searchParams.get('offset')) });
  });
  try {
    await flush();
    assert.equal(app.document.getElementById('next-page').disabled, false);
    app.document.getElementById('next-page').click(); await flush();
    const second = app.calls.filter((call) => call.url.pathname.endsWith('/sessions')).at(-1);
    assert.equal(second.url.searchParams.get('offset'), '50');
    assert.equal(second.url.searchParams.get('limit'), '50');
    assert.match(app.document.getElementById('page-label').textContent, /51–51 of 51/);
    assert.equal(app.document.getElementById('next-page').disabled, true);
    const select = app.document.getElementById('scenario-filter'); select.value = scenario.id;
    select.dispatchEvent(new app.window.Event('change', { bubbles: true })); await flush();
    const reset = app.calls.filter((call) => call.url.pathname.endsWith('/sessions')).at(-1);
    assert.equal(reset.url.searchParams.get('offset'), '0');
    assert.equal(reset.url.searchParams.get('scenario_id'), scenario.id);
    assert.equal(app.document.getElementById('previous-page').disabled, true);
  } finally { app.close(); }
});

test('analytics authentication uses tab storage and headers, never query strings or persistent local storage', async () => {
  const app = harness((url, options) => {
    if (url.pathname.startsWith('/api/v1/analytics/') && options.headers.Authorization !== 'Bearer test-access-token') return response({}, 401);
  });
  try {
    await flush();
    assert.equal(app.document.getElementById('access-panel').hidden, false);
    assert.match(app.document.getElementById('error-message').textContent, /access token/);
    app.document.getElementById('access-token').value = 'test-access-token';
    app.document.getElementById('access-form').dispatchEvent(new app.window.Event('submit', { bubbles: true, cancelable: true })); await flush();
    assert.equal(app.window.sessionStorage.getItem('blockwise.analytics.access'), 'test-access-token');
    assert.equal(app.window.localStorage.length, 0);
    assert.equal(app.document.getElementById('metric-sessions').textContent, '2');
    assert.ok(app.calls.every((call) => !call.url.href.includes('test-access-token')));
    app.document.getElementById('clear-token').click(); await flush();
    assert.equal(app.window.sessionStorage.getItem('blockwise.analytics.access'), null);
    assert.equal(app.document.getElementById('metric-sessions').textContent, '—');
  } finally { app.close(); }
});

test('session inspection safely renders labels and only approved fields, then returns focus', async () => {
  const unsafe = '<img src=x onerror="window.injected=true">';
  const app = harness((url) => {
    if (url.pathname === '/api/v1/analytics/sessions/session-1') return response({ ...detail, scenario: { ...scenario, name: unsafe }, token: 'do-not-render-token', request_id: 'do-not-render-request-id' });
  });
  try {
    await flush();
    const button = app.document.querySelector('#session-table [data-session-id]');
    button.focus(); button.click(); await flush();
    assert.equal(app.document.getElementById('session-dialog').open, true);
    assert.equal(app.document.getElementById('detail-title').textContent, unsafe);
    assert.equal(app.document.getElementById('session-dialog').querySelector('img'), null);
    assert.equal(app.window.injected, undefined);
    assert.equal(app.document.querySelectorAll('.choice-card').length, 2);
    assert.match(app.document.getElementById('detail-content').textContent, /Home → Corner Market → Home/);
    assert.doesNotMatch(app.document.getElementById('detail-content').textContent, /do-not-render/);
    app.document.getElementById('close-detail').click();
    assert.equal(app.document.getElementById('session-dialog').open, false);
    assert.equal(app.document.activeElement, button);
  } finally { app.close(); }
});

test('export carries selected filters and authentication, and downloads a named CSV', async () => {
  const app = harness(undefined, 'tab-token');
  try {
    await flush(); clickMode(app, 'demo'); await flush();
    const select = app.document.getElementById('scenario-filter'); select.value = scenario.id; select.dispatchEvent(new app.window.Event('change')); await flush();
    app.document.getElementById('export-button').click(); await flush();
    const call = app.calls.find((entry) => entry.url.pathname.endsWith('/export'));
    assert.equal(call.url.searchParams.get('data_mode'), 'demo');
    assert.equal(call.url.searchParams.get('scenario_id'), scenario.id);
    assert.equal(call.options.headers.Authorization, 'Bearer tab-token');
    assert.equal(app.downloads.length, 1);
    assert.match(app.downloads[0].download, /^blockwise-demo-\d{4}-\d{2}-\d{2}\.csv$/);
    assert.equal(app.downloads[0].href, 'blob:export');
  } finally { app.close(); }
});

test('a failed filter load clears old results and provides a retry, rather than showing stale metrics', async () => {
  let fail = false;
  const app = harness((url) => { if (fail && url.pathname.endsWith('/summary')) return response({}, 503); });
  try {
    await flush(); assert.equal(app.document.getElementById('metric-sessions').textContent, '2');
    fail = true; clickMode(app, 'demo'); await flush();
    assert.equal(app.document.getElementById('metric-sessions').textContent, '—');
    assert.equal(app.document.getElementById('error-message').hidden, false);
    assert.equal(app.document.getElementById('export-button').disabled, true);
    assert.equal(app.document.getElementById('refresh-button').disabled, false);
    fail = false; app.document.getElementById('refresh-button').click(); await flush();
    assert.equal(app.document.getElementById('metric-sessions').textContent, '2');
    assert.equal(app.document.getElementById('error-message').hidden, true);
  } finally { app.close(); }
});

test('late responses from an old mode cannot overwrite a newer mode', async () => {
  let release;
  let delay = true;
  const pending = new Promise((resolve) => { release = resolve; });
  const app = harness(async (url) => {
    if (url.pathname.endsWith('/summary') && url.searchParams.get('data_mode') === 'participants' && delay) {
      await pending; return response(summary({ session_count: 99 }));
    }
    if (url.pathname.endsWith('/summary') && url.searchParams.get('data_mode') === 'demo') return response(summary({ session_count: 3 }));
  });
  try {
    await flush(); clickMode(app, 'demo'); await flush();
    assert.equal(app.document.getElementById('metric-sessions').textContent, '3');
    delay = false; release(); await flush();
    assert.equal(app.document.getElementById('metric-sessions').textContent, '3');
    assert.match(app.document.getElementById('mode-notice').textContent, /Demonstration data/);
  } finally { release(); app.close(); }
});

test('zero and negative savings remain truthful and infeasible choices are visible', async () => {
  const app = harness((url) => {
    if (url.pathname.endsWith('/summary')) return response(summary({ mean_initial_cost_cents: 0, median_initial_cost_cents: 0, mean_savings_cents: -250, mean_time_change_minutes: -3, infeasible_choice_count: 1, tradeoffs: [{ session_id: 'session-1', stage: 'initial', cost_cents: 0, minutes: 0, feasible: false }] }));
    if (url.pathname.endsWith('/sessions')) return response({ sessions: [session({ comparable: false })], total: 1 });
  });
  try {
    await flush();
    assert.equal(app.document.getElementById('metric-cost').textContent, '$0.00');
    assert.equal(app.document.getElementById('metric-savings').textContent, '-$2.50');
    assert.equal(app.document.getElementById('metric-time').textContent, '-3 min');
    assert.match(app.document.getElementById('summary-notices').textContent, /exceeded a stated limit/);
    assert.match(app.document.getElementById('session-table').textContent, /Constraints changed/);
    assert.equal(app.document.querySelectorAll('#scatter-chart path').length, 1);
    assert.match(app.document.getElementById('tradeoffs-table').textContent, /No/);
  } finally { app.close(); }
});

test('same-named stores remain distinct by scenario, with historical names and ID fallback', async () => {
  const storeChoices = [
    { scenario_id: scenario.id, store_id: 'corner', store_name: 'Corner Market', choice_count: 3 },
    { scenario_id: 'houston-midtown', store_id: 'corner', store_name: 'Corner Market', choice_count: 2 },
    { scenario_id: 'archived-setting', store_id: 'corner', store_name: 'Corner Market', choice_count: 1 }
  ];
  const app = harness((url) => {
    if (url.pathname.endsWith('/summary')) return response(summary({
      scenario_breakdown: [
        { scenario_id: scenario.id, scenario_name: scenario.name, sessions: 2, initial_choices: 2, revised_choices: 1 },
        { scenario_id: 'houston-midtown', scenario_name: 'Midtown evening shop', sessions: 1, initial_choices: 1, revised_choices: 1 }
      ],
      store_choices: url.searchParams.has('scenario_id') ? storeChoices.slice(0, 1) : storeChoices
    }));
  });
  try {
    await flush();
    const rows = [...app.document.querySelectorAll('.store-row')];
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((row) => row.querySelector('strong').textContent), ['Corner Market', 'Corner Market', 'Corner Market']);
    assert.deepEqual(rows.map((row) => row.querySelector('.store-scenario').textContent), [scenario.name, 'Midtown evening shop', 'archived-setting']);
    assert.deepEqual(rows.map((row) => row.querySelector('.store-row-head > span').textContent), ['3 plans', '2 plans', '1 plan']);
    const select = app.document.getElementById('scenario-filter');
    select.value = scenario.id;
    select.dispatchEvent(new app.window.Event('change'));
    await flush();
    assert.equal(app.document.querySelectorAll('.store-row').length, 1);
    assert.equal(app.document.querySelector('.store-scenario'), null);
    assert.match(app.document.getElementById('store-chart').textContent, /Corner Market/);
  } finally { app.close(); }
});


function auditCohort(overrides = {}) {
  return {
    id: 'cohort-a1234567', scenario_id: scenario.id, scenario_name: scenario.name, scenario_version: 'v1',
    scenario_hash: 'snapshot-a123456789', world_version: 'houston-2026', is_demo: false,
    initial_constraints: { ...constraints }, probe_constraints: { ...constraints }, probe_clipped_by_app_bounds: false,
    wallet_attached: false, wallet_source: null, financial_context: null,
    counts: { initial_choices: 8, selected_plan_fits: 4, other_plan_fits: 2, no_plan_fits: 2, needs_review: 0, newly_feasible_under_probe: 0, modeled_dominated: 2 },
    denominators: { classified_initial_choices: 8, probe_eligible_initial_choices: 8, baseline_no_feasible_initial_choices: 2, dominance_eligible_initial_choices: 4 },
    example_ids: { selected_plan_fits: ['session-1'], other_plan_fits: ['session-2'], no_plan_fits: ['session-3'], needs_review: [], newly_feasible_under_probe: [], modeled_dominated: ['session-1'] },
    ...overrides
  };
}
function auditPayload(url, overrides = {}) {
  const budget = Number(url.searchParams.get('budget_delta_cents')), time = Number(url.searchParams.get('time_delta_minutes'));
  const mode = url.searchParams.get('data_mode');
  const cohorts = overrides.cohorts || [auditCohort({
    is_demo: mode === 'demo',
    probe_constraints: { ...constraints, budget_cents: constraints.budget_cents + budget, time_budget_minutes: constraints.time_budget_minutes + time },
    counts: { ...auditCohort().counts, newly_feasible_under_probe: budget || time ? 1 : 0 }
  })];
  const analyzed = cohorts.reduce((sum, cohort) => sum + cohort.counts.initial_choices, 0);
  return {
    data_mode: mode, scenario_id: url.searchParams.get('scenario_id'), probe: { budget_delta_cents: budget, time_delta_minutes: time, combined: Boolean(budget && time) },
    total_sessions: analyzed + 2, selected_sessions: analyzed + 2, analyzed_sessions: analyzed,
    skipped_no_initial_sessions: 1, skipped_invalid_initial_sessions: 1, truncated: false, max_sessions: 200,
    cohorts, notices: [], ...overrides
  };
}
function setAuditInput(app, id, value) {
  const input = app.document.getElementById(id); input.value = value; input.dispatchEvent(new app.window.Event('input', { bubbles: true }));
}
function submitAudit(app) { app.document.getElementById('audit-form').dispatchEvent(new app.window.Event('submit', { bubbles: true, cancelable: true })); }
const auditCalls = (app) => app.calls.filter((call) => call.url.pathname === '/api/v1/analytics/audit');

test('audit is lazy and sends exact cents, active filters, and authorization only on explicit submission', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url)) : undefined, 'audit-secret');
  try {
    await flush(); assert.equal(auditCalls(app).length, 0);
    clickMode(app, 'demo'); await flush();
    const filter = app.document.getElementById('scenario-filter'); filter.value = scenario.id; filter.dispatchEvent(new app.window.Event('change')); await flush();
    setAuditInput(app, 'audit-budget-increase', '10.05'); setAuditInput(app, 'audit-time-increase', '15');
    assert.equal(auditCalls(app).length, 0);
    submitAudit(app); await flush();
    const call = auditCalls(app)[0];
    assert.equal(call.url.searchParams.get('budget_delta_cents'), '1005');
    assert.equal(call.url.searchParams.get('time_delta_minutes'), '15');
    assert.equal(call.url.searchParams.get('scenario_id'), scenario.id);
    assert.equal(call.url.searchParams.get('data_mode'), 'demo');
    assert.equal(call.options.headers.Authorization, 'Bearer audit-secret');
    assert.ok(!call.url.href.includes('audit-secret'));
    assert.equal(app.document.getElementById('audit-results').hidden, false);
    assert.match(app.document.getElementById('audit-cohort-context').textContent, /Demonstration choices/);
    assert.match(app.document.getElementById('audit-probe-results').textContent, /\+\$10\.05 and \+15 min, applied together/);
    assert.equal(app.errors.length, 0);
  } finally { app.close(); }
});

test('audit displays only one exact cohort with classified, baseline, and dominance denominators', async () => {
  const other = auditCohort({
    id: 'cohort-b9876543', scenario_hash: 'snapshot-b9876543', is_demo: true,
    initial_constraints: { ...constraints, budget_cents: 8000 },
    wallet_attached: true, wallet_source: 'fixture',
    financial_context: { profile_name: 'Tight week', source: 'fixture', status: 'ready', spendable_now_cents: 3000 },
    counts: { initial_choices: 1, selected_plan_fits: 0, other_plan_fits: 0, no_plan_fits: 1, needs_review: 0, newly_feasible_under_probe: 0, modeled_dominated: 0 },
    denominators: { classified_initial_choices: 1, probe_eligible_initial_choices: 1, baseline_no_feasible_initial_choices: 1, dominance_eligible_initial_choices: 0 }
  });
  const app = harness((url) => {
    if (!url.pathname.endsWith('/audit')) return;
    const first = auditCohort({ scenario_version: 'a'.repeat(64), world_version: 'b'.repeat(64), counts: { ...auditCohort().counts, newly_feasible_under_probe: 1 } });
    return response(auditPayload(url, { cohorts: [first, other] }));
  });
  try {
    await flush(); clickMode(app, 'all'); await flush();
    setAuditInput(app, 'audit-budget-increase', '10'); submitAudit(app); await flush();
    const select = app.document.getElementById('audit-cohort');
    assert.equal(select.options.length, 2);
    assert.match(select.options[0].textContent, /Heights weekday shop.*\$70\.00.*45 min.*No wallet.*cohort-a/);
    assert.match(select.options[1].textContent, /\$80\.00.*Tight week.*Demo.*cohort-b/);
    assert.deepEqual([...app.document.querySelectorAll('#audit-classifications dd')].map((node) => node.textContent), ['4 / 8', '2 / 8', '2 / 8', '0 / 8']);
    assert.deepEqual([...app.document.querySelectorAll('#audit-probe-results strong')].map((node) => node.textContent), ['1 / 2', '2 / 4']);
    assert.match(app.document.getElementById('audit-method-context').textContent, /not an observed response/);
    const context = app.document.getElementById('audit-cohort-context');
    assert.match(context.textContent, /scenario aaaaaaaa · world bbbbbbbb · snapshot/);
    assert.ok(!context.textContent.includes('a'.repeat(64)));
    assert.ok(!context.textContent.includes('b'.repeat(64)));
    assert.ok(context.title.includes('a'.repeat(64)));
    assert.ok(context.title.includes('b'.repeat(64)));
    select.value = other.id; select.dispatchEvent(new app.window.Event('change'));
    assert.deepEqual([...app.document.querySelectorAll('#audit-classifications dd')].map((node) => node.textContent), ['0 / 1', '0 / 1', '1 / 1', '0 / 1']);
    assert.match(app.document.getElementById('audit-cohort-context').textContent, /snapshot snapshot.*Tight week.*Synthetic fixture.*\$30\.00/);
    assert.match(app.document.getElementById('audit-probe-results').textContent, /no eligible denominator/);
    assert.equal(auditCalls(app).length, 1, 'cohort selection uses the loaded report');
  } finally { app.close(); }
});

test('audit coverage discloses latest-200 truncation and both skipped-record counts', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url, {
    total_sessions: 650, selected_sessions: 200, skipped_no_initial_sessions: 190, skipped_invalid_initial_sessions: 2, truncated: true
  })) : undefined);
  try {
    await flush(); submitAudit(app); await flush();
    const coverage = app.document.getElementById('audit-coverage').textContent;
    assert.match(coverage, /Truncated to the latest 200/);
    assert.match(coverage, /Selected 200 of 650 matching sessions; analyzed 8 valid initial choices/);
    assert.match(coverage, /190 without an initial choice; 2 with an invalid initial record/);
    assert.match(coverage, /not a random sample.*earliest initial choice/);
  } finally { app.close(); }
});

test('audit handles an empty analyzed sample without inventing zero rates', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url, { cohorts: [] })) : undefined);
  try {
    await flush(); submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-results').hidden, false);
    assert.equal(app.document.getElementById('audit-cohort-view').hidden, true);
    assert.match(app.document.getElementById('audit-status').textContent, /No valid initial choices/);
    assert.match(app.document.getElementById('audit-coverage').textContent, /Selected 2 of 2.*analyzed 0/);
    assert.equal(app.document.querySelectorAll('#audit-probe-results strong').length, 0);
  } finally { app.close(); }
});

test('uncertain wallet availability stays unknown and labels remain inert text', async () => {
  const malicious = '<img src=x onerror=alert(1)>';
  const cohort = auditCohort({
    scenario_name: malicious, wallet_attached: true, wallet_source: 'fixture',
    financial_context: { profile_name: malicious, source: 'fixture', status: 'needs_review', spendable_now_cents: null },
    counts: { initial_choices: 1, selected_plan_fits: 0, other_plan_fits: 0, no_plan_fits: 0, needs_review: 1, newly_feasible_under_probe: 0, modeled_dominated: 0 },
    denominators: { classified_initial_choices: 1, probe_eligible_initial_choices: 0, baseline_no_feasible_initial_choices: 0, dominance_eligible_initial_choices: 0 },
    example_ids: { needs_review: ['session-1'] }
  });
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url, { cohorts: [cohort] })) : undefined);
  try {
    await flush(); submitAudit(app); await flush();
    const context = app.document.getElementById('audit-cohort-context').textContent;
    assert.ok(context.includes(malicious));
    assert.match(context, /availability needs review/);
    assert.doesNotMatch(context, /shopping cash: \$0/);
    assert.equal(app.document.querySelector('#audit-panel img'), null);
    assert.deepEqual([...app.document.querySelectorAll('#audit-probe-results strong')].map((node) => node.textContent), ['Not applicable', 'Not applicable']);
    assert.match(app.document.getElementById('audit-method-context').textContent, /1 needs-review cases are excluded/);
  } finally { app.close(); }
});

test('audit examples reuse authenticated session detail and return keyboard focus', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url)) : undefined, 'detail-secret');
  try {
    await flush(); submitAudit(app); await flush();
    app.document.querySelector('.audit-example-disclosure').open = true;
    const button = app.document.querySelector('#audit-examples [data-session-id="session-1"]');
    button.focus(); button.click(); await flush();
    assert.equal(app.document.getElementById('session-dialog').open, true);
    const call = app.calls.find((item) => item.url.pathname.endsWith('/sessions/session-1'));
    assert.equal(call.options.headers.Authorization, 'Bearer detail-secret');
    app.document.getElementById('close-detail').click();
    assert.equal(app.document.activeElement, button);
    assert.equal(app.errors.length, 0);
  } finally { app.close(); }
});

test('audit input rejects out-of-range and fractional-minute probes without requests', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url)) : undefined);
  try {
    await flush();
    for (const [id, value] of [['audit-budget-increase', '-1'], ['audit-budget-increase', '100.01'], ['audit-budget-increase', '0.001'], ['audit-budget-increase', ''], ['audit-time-increase', '121'], ['audit-time-increase', '0.5']]) {
      setAuditInput(app, 'audit-budget-increase', '0'); setAuditInput(app, 'audit-time-increase', '0'); setAuditInput(app, id, value);
      submitAudit(app); await flush();
      assert.equal(auditCalls(app).length, 0); assert.equal(app.document.getElementById('audit-error').hidden, false);
    }
    setAuditInput(app, 'audit-budget-increase', '100'); setAuditInput(app, 'audit-time-increase', '120');
    submitAudit(app); await flush();
    assert.equal(auditCalls(app)[0].url.searchParams.get('budget_delta_cents'), '10000');
    assert.equal(auditCalls(app)[0].url.searchParams.get('time_delta_minutes'), '120');
  } finally { app.close(); }
});

test('audit results reset on probe, mode, scenario, token, and refresh changes without automatic audit', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url)) : undefined);
  try {
    await flush();
    const changes = [
      () => setAuditInput(app, 'audit-budget-increase', '1'),
      () => clickMode(app, 'demo'),
      () => { const select = app.document.getElementById('scenario-filter'); select.value = scenario.id; select.dispatchEvent(new app.window.Event('change')); },
      () => { app.document.getElementById('access-token').value = 'new-token'; app.document.getElementById('access-form').dispatchEvent(new app.window.Event('submit', { cancelable: true })); },
      () => app.document.getElementById('refresh-button').click(),
      () => app.document.getElementById('clear-token').click()
    ];
    for (const change of changes) {
      submitAudit(app); await flush(); assert.equal(app.document.getElementById('audit-results').hidden, false);
      const before = auditCalls(app).length; change(); await flush();
      assert.equal(app.document.getElementById('audit-results').hidden, true);
      assert.equal(app.document.getElementById('audit-cohort').options.length, 0);
      assert.equal(auditCalls(app).length, before);
    }
  } finally { app.close(); }
});

test('late audit responses cannot restore stale results after controls or filters change', async () => {
  let release, pendingUrl;
  const app = harness((url) => {
    if (url.pathname.endsWith('/audit')) { pendingUrl = url; return new Promise((resolve) => { release = resolve; }); }
  });
  try {
    await flush(); submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-panel').getAttribute('aria-busy'), 'true');
    assert.equal(app.document.getElementById('audit-button').disabled, true);
    setAuditInput(app, 'audit-time-increase', '15');
    assert.equal(auditCalls(app)[0].options.signal.aborted, true);
    release(response(auditPayload(pendingUrl))); await flush();
    assert.equal(app.document.getElementById('audit-results').hidden, true);
    assert.match(app.document.getElementById('audit-status').textContent, /Probe changed/);
    submitAudit(app); await flush(); const oldUrl = pendingUrl;
    clickMode(app, 'demo'); await flush(); release(response(auditPayload(oldUrl))); await flush();
    assert.equal(app.document.getElementById('audit-results').hidden, true);
    assert.equal(app.document.getElementById('audit-panel').getAttribute('aria-busy'), 'false');
    assert.equal(app.document.getElementById('audit-button').disabled, false);
  } finally { app.close(); }
});

test('audit authentication errors are local, retryable, and never replace dashboard results', async () => {
  let fail = true;
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(fail ? {} : auditPayload(url), fail ? 401 : 200) : undefined);
  try {
    await flush(); submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-error').hidden, false);
    assert.match(app.document.getElementById('audit-error').textContent, /access token/);
    assert.equal(app.document.getElementById('access-panel').hidden, false);
    assert.equal(app.document.getElementById('metric-cost').textContent, '$64.00');
    fail = false; submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-error').hidden, true);
    assert.equal(app.document.getElementById('audit-results').hidden, false);
  } finally { app.close(); }
});

test('audit rejects inconsistent counts instead of presenting fabricated denominators', async () => {
  const app = harness((url) => {
    if (!url.pathname.endsWith('/audit')) return;
    const payload = auditPayload(url); delete payload.cohorts[0].denominators.baseline_no_feasible_initial_choices;
    return response(payload);
  });
  try {
    await flush(); submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-results').hidden, true);
    assert.match(app.document.getElementById('audit-error').textContent, /incomplete or inconsistent counts/);
    assert.equal(app.document.getElementById('audit-button').disabled, false);
  } finally { app.close(); }
});

test('audit exposes effective probe limits when application bounds clip an increase', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url, {
    cohorts: [auditCohort({ probe_clipped_by_app_bounds: true, probe_constraints: { ...constraints, budget_cents: 8000, time_budget_minutes: 60 } })]
  })) : undefined);
  try {
    await flush(); setAuditInput(app, 'audit-budget-increase', '100'); setAuditInput(app, 'audit-time-increase', '120');
    submitAudit(app); await flush();
    assert.match(app.document.getElementById('audit-probe-results').textContent, /Effective limits: \$80\.00 · 60 min/);
    assert.match(app.document.getElementById('audit-probe-results').textContent, /app maximum capped this increase/);
    assert.match(app.document.getElementById('audit-probe-results').textContent, /Wallet cash stays fixed/);
  } finally { app.close(); }
});

test('audit keeps fractional initial time limits precise while probe increments are whole minutes', async () => {
  const app = harness((url) => url.pathname.endsWith('/audit') ? response(auditPayload(url, {
    cohorts: [auditCohort({ initial_constraints: { ...constraints, time_budget_minutes: 45.25 }, probe_constraints: { ...constraints, time_budget_minutes: 60.25 } })]
  })) : undefined);
  try {
    await flush(); setAuditInput(app, 'audit-time-increase', '15'); submitAudit(app); await flush();
    assert.equal(app.document.getElementById('audit-results').hidden, false);
    assert.match(app.document.getElementById('audit-cohort-context').textContent, /45\.25 min/);
    assert.match(app.document.getElementById('audit-probe-results').textContent, /60\.25 min/);
  } finally { app.close(); }
});