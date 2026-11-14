const { test, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const participant = process.env.PARTICIPANT_URL || 'http://localhost:5183';
const analytics = process.env.ANALYTICS_URL || 'http://localhost:5184';
const backend = process.env.BACKEND_URL || 'http://localhost:8775';

function observe(page) {
  const problems = [];
  page.on('pageerror', error => problems.push('JavaScript: ' + error.message));
  page.on('console', message => {
    if (message.type() === 'error' && /Content Security Policy|Refused to (execute|apply|connect)/i.test(message.text())) problems.push(message.text());
  });
  page.on('response', response => {
    if (/\.(css|js)(\?|$)/.test(response.url()) && response.status() >= 400) problems.push('Asset ' + response.status() + ': ' + response.url());
  });
  return problems;
}
async function noPageOverflow(page) {
  const sizes = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  expect(sizes.page, JSON.stringify(sizes)).toBeLessThanOrEqual(sizes.viewport + 1);
}
async function startDemo(page, { budget = '70', time = '90', stops = '2', wallet } = {}) {
  await page.goto(participant + '/?demo=1');
  await expect(page.locator('#setup')).toBeVisible();
  await expect(page.locator('#demo-notice')).toBeVisible();
  await page.locator('#setup-budget').fill(budget);
  await page.locator('#setup-time').fill(time);
  await page.locator('#setup-stops').selectOption(stops);
  if (wallet) await page.getByRole('combobox', { name: /^Financial context/ }).selectOption(wallet);
  await page.locator('#consent').check();
  const created = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/sessions' && response.request().method() === 'POST');
  await page.getByRole('button', { name: /Start my challenge/ }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const session = await response.json();
  expect(session.is_demo).toBe(true);
  await expect(page.getByRole('heading', { name: 'Your basket. Your call.' })).toBeVisible();
  return session;
}
async function chooseWholeStore(page, name = 'Corner Market') {
  await page.getByRole('button', { name: new RegExp('Buy the whole basket at ' + name) }).click();
  await expect(page.locator('#save-choice')).toBeEnabled();
  await expect(page.locator('#total-cost')).not.toHaveText('—');
}
async function saveChoice(page) {
  const response = page.waitForResponse(response => /\/choices$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST');
  await page.locator('#save-choice').click();
  const result = await response;
  expect(result.status()).toBe(201);
  return result.json();
}
async function readyAnalytics(page, mode = 'participants') {
  await page.goto(analytics);
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('radio', { name: 'Participants', exact: true })).toBeChecked();
  if (mode !== 'participants') {
    await page.getByRole('radio', { name: mode === 'demo' ? 'Demo data' : 'All sessions', exact: true }).check();
    await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  }
}
async function capture(page, path) {
  // Cancel any smooth scrolling before a full-page capture, so offscreen fixed
  // skip links are not stitched into the middle of the resulting image.
  await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
  const dialogOpen = await page.locator('dialog[open]').count();
  await page.screenshot({ path, fullPage: !dialogOpen, animations: 'disabled' });
}

test.beforeAll(async () => { await fs.mkdir('/artifacts', { recursive: true }); });

test('desktop participant choices persist and appear only in demo analytics', async ({ page, context, request }) => {
  test.setTimeout(60000);
  const errors = observe(page);
  const before = await (await request.get(backend + '/api/v1/analytics/summary?data_mode=participants')).json();
  await page.goto(participant + '/?demo=1');
  await expect(page.locator('#scenario-cards button')).toHaveCount(3);
  await noPageOverflow(page);
  await capture(page, '/artifacts/participant-desktop.png');
  const session = await startDemo(page);
  await expect(page.locator('#recommendations-section')).toBeHidden();
  await chooseWholeStore(page);
  await capture(page, '/artifacts/participant-basket-desktop.png');
  const initial = await saveChoice(page);
  expect(initial.stage).toBe('initial');
  await expect(page.locator('#recommendation-cards article')).not.toHaveCount(0);
  await expect(page.locator('#recommendations-note')).toContainText('feasible plans checked');
  await capture(page, '/artifacts/participant-options-desktop.png');
  await page.setViewportSize({ width: 375, height: 812 });
  await noPageOverflow(page);
  await capture(page, '/artifacts/participant-options-mobile.png');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: /Try this plan/ }).first().click();
  await expect(page.locator('#save-choice')).toBeEnabled();
  const revised = await saveChoice(page);
  expect(revised.stage).toBe('revised');
  expect(revised.evaluation.feasible).toBe(true);
  await expect(page.locator('#saved-result')).toBeVisible();
  await page.reload();
  await expect(page.locator('#session-notice')).toContainText('restored');
  await expect(page.locator('#scenario-eyebrow')).toContainText('DEMO');
  await expect(page.locator('#total-cost')).toHaveText(new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(revised.evaluation.total_cost_cents / 100));
  const detail = await (await request.get(backend + '/api/v1/analytics/sessions/' + session.id)).json();
  expect(detail.is_demo).toBe(true);
  expect(detail.choices.map(choice => choice.stage)).toEqual(['initial', 'revised']);
  const after = await (await request.get(backend + '/api/v1/analytics/summary?data_mode=participants')).json();
  expect(after.session_count).toBe(before.session_count);
  const desk = await context.newPage();
  const deskErrors = observe(desk);
  await readyAnalytics(desk);
  await expect(desk.locator('#data-context')).toContainText('Demo sessions are excluded');
  await desk.getByRole('radio', { name: 'Demo data', exact: true }).check();
  await expect(desk.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await desk.locator('#scenario-filter').selectOption(session.scenario.id);
  await expect(desk.locator('#results')).toHaveAttribute('aria-busy', 'false');
  const open = desk.locator('#session-table .session-id-link[data-session-id="' + session.id + '"]');
  await expect(open).toBeVisible();
  await open.click();
  await expect(desk.locator('#session-dialog')).toBeVisible();
  await expect(desk.locator('#detail-content .choice-card')).toHaveCount(2);
  await expect(desk.locator('#detail-content')).toContainText('Demonstration data');
  await desk.keyboard.press('Escape');
  await expect(desk.locator('#session-dialog')).not.toBeVisible();
  await expect(open).toBeFocused();
  await noPageOverflow(desk);
  await capture(desk, '/artifacts/analytics-desktop.png');
  const downloaded = desk.waitForEvent('download');
  await desk.getByRole('button', { name: /Export CSV/ }).click();
  const download = await downloaded;
  await download.saveAs('/artifacts/demo-export.csv');
  const csv = await fs.readFile('/artifacts/demo-export.csv', 'utf8');
  expect(csv).toContain(session.id);
  expect(csv).not.toContain(session.token);
  expect(errors).toEqual([]);
  expect(deskErrors).toEqual([]);
});

test('mobile challenge explains impossible budgets and keeps the original choice', async ({ page }) => {
  const errors = observe(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(participant + '/?demo=1');
  await expect(page.locator('#setup')).toBeVisible();
  await noPageOverflow(page);
  await expect(page.locator('.setup-panel-copy h2')).toHaveText('Make room for what matters.');
  await expect(page.locator('#setup-stops option:checked')).toHaveText('2 stores');
  await capture(page, '/artifacts/participant-mobile.png');
  const session = await startDemo(page, { budget: '1', time: '1', stops: '1' });
  await chooseWholeStore(page);
  await expect(page.locator('#plan-state')).toHaveText('Outside your limits');
  await expect(page.locator('#evaluation-error')).toContainText('exceeds');
  const initial = await saveChoice(page);
  expect(initial.evaluation.feasible).toBe(false);
  await expect(page.locator('#recommendations-status')).toContainText('No complete shopping plan');
  await expect(page.locator('#recommendation-cards article')).toHaveCount(0);
  await expect(page.locator('#active-budget')).toHaveValue('1.00');
  await expect(page.locator('#active-time')).toHaveValue('1');
  await noPageOverflow(page);
  await capture(page, '/artifacts/participant-infeasible-mobile.png');
  expect(session.is_demo).toBe(true);
  expect(errors).toEqual([]);
});

test('a third store is blocked with an actionable message', async ({ page }) => {
  const errors = observe(page);
  const session = await startDemo(page);
  await chooseWholeStore(page);
  await page.getByRole('combobox', { name: 'Store for Milk', exact: true }).selectOption(session.scenario.stores[1].id);
  await page.getByRole('combobox', { name: 'Store for Eggs', exact: true }).selectOption(session.scenario.stores[2].id);
  await expect(page.locator('#evaluation-error')).toContainText('no more than two stores');
  await expect(page.locator('#save-choice')).toBeDisabled();
  await expect(page.locator('#total-cost')).toHaveText('—');
  await chooseWholeStore(page, 'Value Foods');
  await expect(page.locator('#evaluation-error')).toBeHidden();
  expect(errors).toEqual([]);
});

test('one-store constraint flags a two-store choice without silently relaxing it', async ({ page }) => {
  const session = await startDemo(page, { stops: '1' });
  await chooseWholeStore(page);
  await page.getByRole('combobox', { name: 'Store for Milk', exact: true }).selectOption(session.scenario.stores[1].id);
  await expect(page.locator('#evaluation-error')).toContainText('maximum number of stores');
  await expect(page.locator('#active-stops')).toHaveValue('1');
  const initial = await saveChoice(page);
  expect(initial.evaluation.feasible).toBe(false);
  expect(initial.evaluation.constraints.max_stores).toBe(1);
  await expect(page.locator('#recommendation-cards article')).not.toHaveCount(0);
});

test('scenario loading failure has a keyboard-operable retry', async ({ page }) => {
  const errors = observe(page);
  let fail = true;
  await page.route('**/api/v1/scenarios', async route => {
    if (fail) { fail = false; await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'The scenario service is temporarily unavailable.' }) }); }
    else await route.continue();
  });
  await page.goto(participant + '/?demo=1');
  await expect(page.locator('#global-error')).toContainText('temporarily unavailable');
  await expect(page.getByRole('button', { name: 'Try again', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Try again', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#scenario-cards button')).toHaveCount(3);
  await expect(page.locator('#global-error')).toBeHidden();
  expect(errors).toEqual([]);
});

test('expired browser session is cleared and a new demo can start', async ({ page }) => {
  const original = await startDemo(page);
  await page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('blockwise.participant.v1'));
    saved.token = 'invalid-test-token';
    localStorage.setItem('blockwise.participant.v1', JSON.stringify(saved));
  });
  await page.reload();
  await expect(page.locator('#global-error')).toContainText('previous session is no longer available');
  await expect(page.locator('#setup')).toBeVisible();
  const newSession = await startDemo(page);
  expect(newSession.id).not.toBe(original.id);
  expect(newSession.is_demo).toBe(true);
});

test('mobile analytics is readable, keyboard accessible, and separates demo results', async ({ page }) => {
  const errors = observe(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await readyAnalytics(page, 'demo');
  await expect(page.locator('#mode-notice')).toContainText('Demonstration data');
  await expect(page.locator('#participant-link')).toHaveAttribute('href', /\?demo=1$/);
  await noPageOverflow(page);
  await capture(page, '/artifacts/analytics-mobile.png');
  await page.locator('#session-table button[data-session-id]').first().click();
  await expect(page.locator('#session-dialog')).toBeVisible();
  await expect(page.locator('#close-detail')).toBeFocused();
  await noPageOverflow(page);
  await capture(page, '/artifacts/analytics-detail-mobile.png');
  await page.keyboard.press('Escape');
  await expect(page.locator('#session-dialog')).not.toBeVisible();
  await page.getByRole('radio', { name: 'Participants', exact: true }).check();
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#mode-notice')).toBeHidden();
  await expect(page.locator('#data-context')).toContainText('Demo sessions are excluded');
  expect(errors).toEqual([]);
});


test('chat proposes limits without applying or saving until explicit actions', async ({ page, request }) => {
  test.setTimeout(60000);
  const errors = observe(page);
  let choicePosts = 0;
  page.on('request', req => {
    if (req.method() === 'POST' && /\/choices$/.test(new URL(req.url()).pathname)) choicePosts++;
  });
  const session = await startDemo(page, { stops: '2' });
  await expect(page.locator('#chat-section')).toBeHidden();
  await chooseWholeStore(page);
  await saveChoice(page);
  await expect(page.locator('#chat-section')).toBeVisible();
  await expect(page.locator('#chat-send')).toBeEnabled();
  const chatResponse = page.waitForResponse(res => /\/chat$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
  await page.getByRole('textbox', { name: 'Ask the shopping guide', exact: true }).fill('one store only');
  await page.getByRole('button', { name: /Ask the guide/ }).click();
  const proposal = await (await chatResponse).json();
  expect(proposal.status).toBe('ok');
  expect(proposal.constraints.max_stores).toBe(1);
  await expect(page.locator('#chat-proposal')).toBeVisible();
  await expect(page.locator('#chat-proposal-summary')).toContainText('1 store maximum');
  await expect(page.locator('#active-stops')).toHaveValue('2');
  const getChoices = async () => (await (await request.get(backend + '/api/v1/analytics/sessions/' + session.id)).json()).choices;
  expect((await getChoices()).length).toBe(1);
  expect(choicePosts).toBe(1);
  await page.locator('#chat-section').screenshot({ path: '/artifacts/participant-chat-desktop.png', animations: 'disabled' });
  await page.setViewportSize({ width: 375, height: 812 });
  await noPageOverflow(page);
  const selectSizing = await page.locator('#item-0').evaluate(select => {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = getComputedStyle(select).font;
    return { width: select.getBoundingClientRect().width, text: context.measureText(select.selectedOptions[0].textContent).width };
  });
  expect(selectSizing.width, JSON.stringify(selectSizing)).toBeGreaterThan(selectSizing.text + 30);
  await page.locator('#chat-section').screenshot({ path: '/artifacts/participant-chat-mobile.png', animations: 'disabled' });
  await page.getByRole('button', { name: 'Apply these limits', exact: true }).click();
  await expect(page.locator('#active-stops')).toHaveValue('1');
  await expect(page.locator('#save-choice')).toBeEnabled();
  await expect(page.locator('#session-notice')).toContainText('saved choices have not changed');
  expect((await getChoices()).length).toBe(1);
  expect(choicePosts).toBe(1);
  const revised = await saveChoice(page);
  expect(revised.stage).toBe('revised');
  expect(revised.evaluation.constraints.max_stores).toBe(1);
  const choices = await getChoices();
  expect(choices.length).toBe(2);
  expect(choices[0].evaluation.constraints.max_stores).toBe(2);
  expect(choices[1].evaluation.constraints.max_stores).toBe(1);
  expect(choicePosts).toBe(2);
  await page.reload();
  await expect(page.locator('#chat-history .chat-message')).toHaveCount(2);
  await expect(page.locator('#chat-history')).toContainText('one store only');
  await expect(page.locator('#active-stops')).toHaveValue('1');
  await expect(page.locator('#chat-proposal-summary')).toContainText('Restored guide limits');
  await noPageOverflow(page);
  expect((await getChoices()).length).toBe(2);
  expect(errors).toEqual([]);
});

test('tight fictional wallet limits recommendations without overwriting the chosen budget', async ({ page, request }) => {
  const errors = observe(page);
  await page.setViewportSize({ width: 375, height: 812 });
  const session = await startDemo(page, { wallet: 'tight' });
  expect(session.scenario.financial_context.profile_id).toBe('tight');
  expect(session.scenario.financial_context.snapshot.source).toBe('fixture');
  await expect(page.locator('#active-budget')).toHaveValue('70.00');
  await expect(page.locator('#wallet-summary')).toBeVisible();
  await expect(page.locator('#active-wallet-checking')).toContainText('140');
  await expect(page.locator('#active-wallet-bills')).toContainText('110');
  await expect(page.locator('#active-wallet-capacity')).toContainText('30');
  await chooseWholeStore(page);
  await expect(page.locator('#plan-state')).toHaveText('Outside your limits');
  const initial = await saveChoice(page);
  expect(initial.evaluation.feasible).toBe(false);
  expect(initial.evaluation.wallet.summary.spendable_now_cents).toBe(3000);
  expect(initial.evaluation.wallet.after_choice.local_spending_cents).toBe(initial.evaluation.total_cost_cents);
  await expect(page.locator('#recommendation-cards article')).toHaveCount(0);
  const response = page.waitForResponse(res => /\/chat$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
  await page.getByRole('textbox', { name: 'Ask the shopping guide', exact: true }).fill('budget $500');
  await page.getByRole('button', { name: /Ask the guide/ }).click();
  const chat = await (await response).json();
  expect(chat.plans).toEqual([]);
  await expect(page.locator('#active-budget')).toHaveValue('70.00');
  await chooseWholeStore(page, 'Grove Grocer');
  const revision = await saveChoice(page);
  expect(revision.evaluation.wallet.after_choice.local_spending_cents).toBe(revision.evaluation.total_cost_cents);
  expect(revision.evaluation.wallet.summary.spendable_now_cents).toBe(3000);
  await page.reload();
  await expect(page.locator('#active-wallet-capacity')).toContainText('30');
  await expect(page.locator('#wallet-after-choice')).toBeVisible();
  const frozen = await (await request.get(backend + '/api/v1/sessions/' + session.id + '/wallet', { headers: { Authorization: 'Bearer ' + session.token } })).json();
  expect(frozen.summary.spendable_now_cents).toBe(3000);
  await noPageOverflow(page);
  await capture(page, '/artifacts/participant-wallet-mobile.png');
  expect(errors).toEqual([]);
});

test('unavailable optional wallets do not block a budget-only challenge', async ({ page }) => {
  const errors = observe(page);
  await page.route('**/api/v1/wallets', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Wallet options are temporarily unavailable.' }) }));
  const session = await startDemo(page);
  expect(session.scenario.financial_context).toBeUndefined();
  await expect(page.locator('#wallet-summary')).toBeHidden();
  await chooseWholeStore(page);
  const initial = await saveChoice(page);
  expect(initial.evaluation.feasible).toBe(true);
  await expect(page.locator('#recommendation-cards article')).not.toHaveCount(0);
  expect(errors).toEqual([]);
});
test('investigation protects limits and applies a verified proposal only to the draft', async ({ page, request }) => {
  const errors = observe(page);
  const session = await startDemo(page, { budget: '1', time: '1', stops: '1' });
  await chooseWholeStore(page);
  await saveChoice(page);
  await page.locator('#investigation-panel > summary').click();
  await page.locator('#investigate-protect-budget').check();
  const investigate = async () => {
    const pending = page.waitForResponse(res => /\/investigate$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Check possible changes', exact: true }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    return response.json();
  };
  const protectedResult = await investigate();
  expect(protectedResult.alternatives).toEqual([]);
  expect(protectedResult.protected_constraints).toContain('budget_cents');
  await expect(page.locator('[data-investigation-apply]')).toHaveCount(0);
  await page.locator('#investigate-protect-budget').uncheck();
  const report = await investigate();
  expect(report.alternatives.length).toBeGreaterThan(0);
  expect(report.mode).toBe('deterministic');
  await expect(page.locator('#active-budget')).toHaveValue('1.00');
  await expect(page.locator('#active-time')).toHaveValue('1');
  const getChoices = async () => (await (await request.get(backend + '/api/v1/sessions/' + session.id, { headers: { Authorization: 'Bearer ' + session.token } })).json()).choices;
  expect((await getChoices()).length).toBe(1);
  await page.setViewportSize({ width: 375, height: 812 });
  await noPageOverflow(page);
  await page.locator('#investigation-panel').screenshot({ path: '/artifacts/participant-investigation-mobile.png', animations: 'disabled' });
  const apply = page.locator('[data-investigation-apply]').first();
  const identity = await apply.getAttribute('data-investigation-apply');
  const proposal = report.alternatives.find(item => item.id === identity);
  expect(proposal).toBeTruthy();
  await apply.click();
  await expect(page.locator('#active-budget')).toHaveValue((proposal.constraints.budget_cents / 100).toFixed(2));
  await expect(page.locator('#active-time')).toHaveValue(String(proposal.constraints.time_budget_minutes));
  expect((await getChoices()).length).toBe(1);
  const revised = await saveChoice(page);
  expect(revised.evaluation.feasible).toBe(true);
  expect(revised.evaluation.assignments).toEqual(proposal.plan.assignments);
  expect((await getChoices()).length).toBe(2);
  expect(errors).toEqual([]);
});

test('changing a draft invalidates previously calculated investigation proposals', async ({ page }) => {
  await startDemo(page, { budget: '1', time: '1', stops: '1' });
  await chooseWholeStore(page);
  await saveChoice(page);
  await page.locator('#investigation-panel > summary').click();
  const pending = page.waitForResponse(res => /\/investigate$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
  await page.getByRole('button', { name: 'Check possible changes', exact: true }).click();
  expect((await (await pending).json()).alternatives.length).toBeGreaterThan(0);
  await expect(page.locator('[data-investigation-apply]').first()).toBeEnabled();
  await page.locator('.limits-card > summary').click();
  await page.locator('#active-time').fill('2');
  await page.getByRole('button', { name: 'Apply limits', exact: true }).click();
  await expect(page.locator('[data-investigation-apply]').first()).toBeDisabled();
  await expect(page.locator('#investigation-status')).toContainText('Check again');
});
test('analytics audit keeps exact cohorts separate and resets when filters change', async ({ page }) => {
  const errors = observe(page);
  await readyAnalytics(page, 'demo');
  await page.locator('#audit-budget-increase').fill('2');
  await page.locator('#audit-time-increase').fill('5');
  const pending = page.waitForResponse(res => new URL(res.url()).pathname === '/api/v1/analytics/audit');
  await page.locator('#audit-button').click();
  const response = await pending;
  expect(response.status()).toBe(200);
  const url = new URL(response.url());
  expect(url.searchParams.get('data_mode')).toBe('demo');
  expect(url.searchParams.get('budget_delta_cents')).toBe('200');
  expect(url.searchParams.get('time_delta_minutes')).toBe('5');
  const report = await response.json();
  expect(report.cohorts.length).toBeGreaterThan(0);
  for (const cohort of report.cohorts) {
    const counts = cohort.counts;
    expect(counts.selected_plan_fits + counts.other_plan_fits + counts.no_plan_fits + counts.needs_review).toBe(counts.initial_choices);
    expect(cohort.is_demo).toBe(true);
  }
  await expect(page.locator('#audit-results')).toBeVisible();
  await expect(page.locator('#audit-cohort option')).toHaveCount(report.cohorts.length);
  const walletCohort = report.cohorts.find(cohort => cohort.financial_context?.profile_name === 'Tight week');
  expect(walletCohort).toBeTruthy();
  await page.locator('#audit-cohort').selectOption(walletCohort.id);
  await expect(page.locator('#audit-cohort-context')).toContainText('Tight week');
  await page.setViewportSize({ width: 375, height: 812 });
  await noPageOverflow(page);
  // Capture a real viewport at mobile width; a tall element stitch can incorrectly
  // draw offscreen fixed skip links into the middle of the artifact.
  await page.setViewportSize({ width: 375, height: 1900 });
  await page.evaluate(() => window.scrollTo({ top: scrollY + document.getElementById('audit-panel').getBoundingClientRect().top - 12, behavior: 'instant' }));
  await page.screenshot({ path: '/artifacts/analytics-audit-mobile.png', animations: 'disabled' });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.locator('.audit-example-disclosure > summary').click();
  const example = page.locator('#audit-examples [data-session-id]').first();
  await expect(example).toBeVisible();
  await example.click();
  await expect(page.locator('#session-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(example).toBeFocused();
  await page.getByRole('radio', { name: 'Participants', exact: true }).check();
  await expect(page.locator('#results')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#audit-results')).toBeHidden();
  expect(errors).toEqual([]);
});
test('a money-or-time question changes the highlighted option without changing the saved choice', async ({ page, request }) => {
  const session = await startDemo(page, { budget: '45', time: '15', stops: '1', wallet: 'steady' });
  await chooseWholeStore(page);
  await saveChoice(page);
  await page.locator('#investigation-panel > summary').click();
  await page.locator('#investigate-protect-stops').check();
  await page.locator('#investigate-protect-travel').check();
  const pending = page.waitForResponse(res => /\/investigate$/.test(new URL(res.url()).pathname) && res.request().method() === 'POST');
  await page.locator('#investigate-button').click();
  const report = await (await pending).json();
  expect(report.question.id).toBe('money_or_time');
  await page.getByRole('button', { name: 'More time', exact: true }).click();
  await expect(page.locator('#active-budget')).toHaveValue('45.00');
  await expect(page.locator('#active-time')).toHaveValue('15');
  const selected = page.locator('#investigation-alternatives [data-investigation-apply]').first();
  const chosenId = await selected.getAttribute('data-investigation-apply');
  const timeProposal = report.alternatives.find(item => item.id === chosenId);
  expect(timeProposal.kind).toBe('time');
  expect(timeProposal.constraints.budget_cents).toBe(4500);
  await expect(page.locator('#investigation-more')).toBeVisible();
  await page.setViewportSize({ width: 375, height: 1900 });
  await noPageOverflow(page);
  await page.evaluate(() => window.scrollTo({ top: scrollY + document.getElementById('investigation-panel').getBoundingClientRect().top - 12, behavior: 'instant' }));
  await page.screenshot({ path: '/artifacts/participant-tradeoff-mobile.png', animations: 'disabled' });
  await page.setViewportSize({ width: 375, height: 812 });
  await selected.click();
  await expect(page.locator('#active-time')).toHaveValue(String(timeProposal.constraints.time_budget_minutes));
  const revised = await saveChoice(page);
  expect(revised.evaluation.feasible).toBe(true);
  expect(revised.evaluation.wallet.summary.spendable_now_cents).toBe(22000);
  const records = await (await request.get(backend + '/api/v1/analytics/sessions?data_mode=demo&limit=100')).json();
  const record = records.sessions.find(item => item.id === session.id);
  expect(record.comparable).toBe(false);
});