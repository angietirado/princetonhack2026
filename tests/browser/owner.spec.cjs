const { test, expect } = require('@playwright/test');
const fs = require('node:fs/promises');
const dollars = cents => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100);
const FIXTURE = 'kind,id,customer_id,order_id,date,amount_cents,status\norder,browser_order_1,browser_customer_1,,2026-11-01,1000,completed\norder,browser_order_2,browser_customer_1,,2026-11-08,1500,completed\nrefund,browser_refund_1,,browser_order_1,2026-11-09,200,\n';
let originalDataset;

function observe(page) {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (/\.(css|js)(\?|$)/.test(response.url()) && response.status() >= 400) errors.push(`Asset ${response.status()}: ${response.url()}`);
  });
  return errors;
}
async function noOverflow(page) {
  const size = await page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth }));
  expect(size.page, JSON.stringify(size)).toBeLessThanOrEqual(size.viewport + 1);
}
async function ready(page) {
  await page.goto('/');
  await expect(page.locator('#dashboard-content')).toBeVisible();
  await expect(page.locator('#source-pill')).toContainText('Synthetic');
  await expect(page.locator('#metric-revenue')).toHaveText(/\$/);
}
async function capture(page, filename, fullPage = true) {
  await page.evaluate(() => window.scrollTo({ top: 0, left: 0, behavior: 'instant' }));
  await page.screenshot({ path: '/artifacts/' + filename, fullPage, animations: 'disabled' });
}
async function select(request, dataset_id) {
  const response = await request.post('/api/v1/owner/datasets/select', { data: { dataset_id } });
  expect(response.status(), await response.text()).toBe(200);
}

test.beforeAll(async ({ request }) => {
  await fs.mkdir('/artifacts', { recursive: true });
  const response = await request.get('/api/v1/owner/datasets');
  expect(response.status(), 'Run the credential-free .env.example stack before browser tests').toBe(200);
  originalDataset = (await response.json()).active_id;
  await select(request, 'demo');
});
test.afterAll(async ({ request }) => {
  if (originalDataset) await select(request, originalDataset);
});
test.beforeEach(async ({ request }) => { await select(request, 'demo'); });

test('desktop customer story reconciles, filters, explains and exports', async ({ page, request }) => {
  const errors = observe(page);
  const snapshot = await (await request.get('/api/v1/owner/dashboard')).json();
  await ready(page);
  await expect(page.locator('#metric-revenue')).toHaveText(dollars(snapshot.metrics.net_revenue_cents));
  await expect(page.locator('#metric-customers')).toHaveText(String(snapshot.metrics.active_customers));
  await expect(page.locator('#segments .segment-row')).toHaveCount(5);
  const unavailable = page.locator('#cohorts [title="Not yet observable"]');
  await expect(unavailable.first()).toHaveText('—');
  expect(await unavailable.count()).toBeGreaterThan(0);
  await noOverflow(page);
  await capture(page, 'owner-desktop-overview.png');
  await capture(page, 'owner-desktop-first-screen.png', false);

  const slipping = snapshot.customers.filter(customer => customer.segment === 'slipping');
  await page.locator('#segments [data-segment="slipping"]').click();
  await expect(page.locator('#customers-panel')).toBeVisible();
  await expect(page.locator('#segment-filter')).toHaveValue('slipping');
  await expect(page.locator('#customers tr')).toHaveCount(slipping.length);
  await page.locator('#customer-search').fill(slipping[0].id);
  await expect(page.locator('#customers tr')).toHaveCount(1);
  await capture(page, 'owner-desktop-customers.png');
  await page.locator('#customer-search').fill('not-an-observed-customer');
  await expect(page.locator('#customer-empty')).toBeVisible();
  await page.locator('#customer-search').fill('');

  await page.locator('#question').fill('How are sales in this period?');
  await page.locator('#ask-submit').click();
  await expect(page.locator('#ask-result')).toBeVisible();
  await expect(page.locator('#answer')).toContainText(dollars(snapshot.metrics.net_revenue_cents));
  await expect(page.locator('#answer-limitations')).toContainText('Deterministic offline');
  const downloadEvent = page.waitForEvent('download');
  await page.locator('#export-customers').click();
  const download = await downloadEvent;
  await download.saveAs('/artifacts/customer-export.csv');
  const exported = await fs.readFile('/artifacts/customer-export.csv', 'utf8');
  expect(exported).toContain(snapshot.period.start);
  expect(exported).toContain('synthetic');
  expect(exported).toContain('True');
  expect(errors).toEqual([]);
});

test('mobile keeps content inside viewport and drawer keyboard controls work', async ({ page }) => {
  const errors = observe(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await ready(page);
  await noOverflow(page);
  await capture(page, 'owner-mobile-overview.png');
  await capture(page, 'owner-mobile-first-screen.png', false);
  await page.locator('[data-view="customers"]').click();
  await page.locator('#segment-filter').selectOption('new');
  await expect(page.locator('#customers tr')).not.toHaveCount(0);
  await noOverflow(page);
  await capture(page, 'owner-mobile-customers.png', false);
  await page.locator('#data-source-button').click();
  await expect(page.locator('#data-drawer')).toBeVisible();
  await expect(page.locator('#dataset-select')).toBeEnabled();
  await expect(page.locator('#close-data-drawer')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('#preview-import')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('#close-data-drawer')).toBeFocused();
  const drawer = await page.locator('#data-drawer').evaluate(element => ({ client: element.clientWidth, content: element.scrollWidth }));
  expect(drawer.content, JSON.stringify(drawer)).toBeLessThanOrEqual(drawer.client + 1);
  await capture(page, 'owner-mobile-data-drawer.png', false);
  await page.keyboard.press('Escape');
  await expect(page.locator('#data-drawer')).toBeHidden();
  await expect(page.locator('#data-source-button')).toBeFocused();
  await page.setViewportSize({ width: 320, height: 740 });
  await noOverflow(page);
  expect(errors).toEqual([]);
});

test('reviewed synthetic CSV import persists separately and demo is restored', async ({ page, request }) => {
  const errors = observe(page);
  const before = (await (await request.get('/api/v1/owner/datasets')).json()).active_id;
  try {
    await ready(page);
    await page.locator('#data-source-button').click();
    await expect(page.locator('#dataset-select')).toBeEnabled();
    await page.locator('#import-business').fill('Browser QA synthetic cafe');
    await page.locator('#import-synthetic').check();
    await page.locator('#import-csv').fill(FIXTURE);
    const previewEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/import/preview');
    await page.locator('#preview-import').click();
    const previewResponse = await previewEvent;
    expect(previewResponse.status()).toBe(200);
    const preview = await previewResponse.json();
    expect(preview.synthetic).toBe(true);
    await expect(page.locator('#import-preview')).toBeVisible();
    expect((await (await request.get('/api/v1/owner/datasets')).json()).active_id).toBe(before);
    await page.locator('#import-preview').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '/artifacts/owner-desktop-import-preview.png', animations: 'disabled' });
    const commitEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/import/commit');
    await page.locator('#commit-import').click();
    const committed = await commitEvent;
    expect(committed.status()).toBe(200);
    const key = (await committed.json()).dataset_id;
    expect(key).toBe(preview.preview_hash);
    await expect(page.locator('#data-source-status')).toContainText('Imported successfully');
    await page.locator('#close-data-drawer').click();
    await expect(page.locator('#business-name')).toHaveText('Browser QA synthetic cafe');
    await expect(page.locator('#metric-revenue')).toHaveText('$23.00');
    await expect(page.locator('#source-pill')).toContainText('Synthetic');
    await page.reload();
    await expect(page.locator('#metric-revenue')).toHaveText('$23.00');
    const persisted = await (await request.get('/api/v1/owner/dashboard')).json();
    expect(persisted.source.synthetic).toBe(true);
    expect(persisted.source.dataset_id).toBe(key);
    await capture(page, 'owner-desktop-imported-synthetic.png');
    expect(persisted.actions).toHaveLength(0);
    expect(persisted.metrics.within_period_repeat_customers).toBe(1);
    expect(persisted.metrics.within_period_repeat_share).toBe(1);
    await page.locator('[data-view="customers"]').click();
    await expect(page.locator('#repeat-share')).toBeVisible();
    await expect(page.locator('#repeat-share')).toHaveText('100%');
    await expect(page.locator('#repeat-count')).toContainText('1 of 1 active customers');
    await expect(page.locator('#repeat-caveat')).toBeVisible();
    await expect(page.locator('#repeat-caveat')).toContainText('No supplied purchase history');
    await expect(page.locator('#customers tr')).toHaveCount(1);
    await capture(page, 'owner-desktop-imported-repeat.png');
    await page.locator('[data-view="experiments"]').click();
    await expect(page.locator('#actions button')).toHaveCount(0);
    await expect(page.locator('#actions')).toContainText('No automatic suggestion applies');
    await expect(page.locator('#manual-pilot')).toBeEnabled();
    await page.locator('#manual-pilot').click();
    await expect(page.locator('#draft-card')).toBeVisible();
    await expect(page.locator('#draft-title')).toHaveText('A pilot you define');
    await page.locator('#pilot-segment').selectOption('promising');
    await page.locator('#pilot-audience').fill('20');
    await page.locator('#pilot-offer').fill('1.00');
    await page.locator('#pilot-budget').fill('10.00');
    await page.locator('#pilot-days').fill('14');
    const smallPilotEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/experiments/plan');
    await page.locator('#pilot-submit').click();
    const smallPilotResponse = await smallPilotEvent;
    expect(smallPilotResponse.status()).toBe(200);
    const smallPilot = await smallPilotResponse.json();
    expect(smallPilot.source.dataset_id).toBe(key);
    expect(smallPilot.segment).toBe('promising');
    expect(smallPilot.observed_segment_customers).toBe(1);
    expect(smallPilot.proposed_audience_cap).toBe(1);
    expect(smallPilot.treatment_customers + smallPilot.holdout_customers).toBe(1);
    expect(smallPilot.feasible_pilot).toBe(false);
    await expect(page.locator('#pilot-verdict')).toContainText('Too few observed customers');
    await expect(page.locator('#pilot-verdict')).toContainText('At least four');
    await capture(page, 'owner-desktop-small-pilot.png');
    await page.locator('#data-source-button').click();
    await expect(page.locator('#dataset-select')).toBeEnabled();
    await page.locator('#dataset-select').selectOption('demo');
    await page.locator('#select-dataset').click();
    await expect(page.locator('#data-source-status')).toContainText('Dataset switched');
    await page.locator('#close-data-drawer').click();
    await expect(page.locator('#business-name')).toHaveText('Harbor & Pine');
    expect(errors).toEqual([]);
  } finally {
    await select(request, before);
  }
});
for (const device of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'mobile', width: 375, height: 812 }]) {
  test(`${device.name} comparison and pilot preserve evidence and explicit budget`, async ({ page, request }) => {
    const errors = observe(page);
    await page.setViewportSize({ width: device.width, height: device.height });
    await ready(page);
    const expected = await (await request.get('/api/v1/owner/compare')).json();
    const compareEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/compare');
    await page.locator('#compare-button').click();
    const comparison = await compareEvent;
    expect(comparison.status()).toBe(200);
    const result = await comparison.json();
    expect(result.source.dataset_id).toBe('demo');
    expect(result.delta_cents).toBe(expected.delta_cents);
    expect(result.groups.reduce((sum, row) => sum + row.delta_cents, 0)).toBe(result.delta_cents);
    await expect(page.locator('#comparison-result')).toBeVisible();
    await expect(page.locator('#comparison-totals')).toContainText(dollars(result.current.metrics.net_revenue_cents));
    await expect(page.locator('#comparison-totals')).toContainText(dollars(result.baseline.metrics.net_revenue_cents));
    await expect(page.locator('#comparison-groups tr')).toHaveCount(result.groups.length);
    await page.locator('#comparison-contributor-details summary').click();
    await expect(page.locator('#comparison-contributors tr')).toHaveCount(result.contributors.length);
    await noOverflow(page);
    await page.locator('.comparison-card').screenshot({ path: `/artifacts/owner-${device.name}-comparison.png`, animations: 'disabled' });

    await page.locator('[data-view="experiments"]').click();
    await expect(page.locator('#experiments-panel')).toBeVisible();
    await page.locator('#actions article').filter({ hasText: 'Reconnect with quiet regulars' }).getByRole('button').click();
    await expect(page.locator('#draft-card')).toBeVisible();
    await page.locator('#pilot-segment').selectOption('slipping');
    await page.locator('#pilot-audience').fill('20');
    await page.locator('#pilot-offer').fill('5.00');
    await page.locator('#pilot-budget').fill('10.00');
    await page.locator('#pilot-days').fill('14');
    const pilotEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/experiments/plan');
    await page.locator('#pilot-submit').click();
    const planned = await pilotEvent;
    expect(planned.status()).toBe(200);
    const pilot = await planned.json();
    const submitted = planned.request().postDataJSON();
    expect(submitted.offer_cents).toBe(500);
    expect(submitted.budget_cents).toBe(1000);
    expect(pilot.proposed_audience_cap).toBe(20);
    expect(pilot.treatment_customers).toBe(10);
    expect(pilot.holdout_customers).toBe(10);
    expect(pilot.maximum_offer_cost_cents).toBe(5000);
    expect(pilot.feasible_pilot).toBe(false);
    expect(pilot.within_budget).toBe(false);
    await expect(page.locator('#pilot-verdict')).toContainText('Exceeds the offer budget by $40.00');
    await expect(page.locator('#pilot-audience')).toHaveValue('20');
    await expect(page.locator('#pilot-budget')).toHaveValue('10.00');
    await expect(page.locator('#pilot-limitations')).toContainText('No customer is assigned or contacted');
    await noOverflow(page);
    await capture(page, `owner-${device.name}-pilot-over-budget.png`);

    await page.locator('#pilot-budget').fill('50.00');
    await expect(page.locator('#pilot-result')).toBeHidden();
    await page.locator('#pilot-submit').click();
    await expect(page.locator('#pilot-verdict')).toContainText('Fits the entered offer budget');
    await expect(page.locator('#pilot-audience')).toHaveValue('20');
    await expect(page.locator('#pilot-numbers')).toContainText('$50.00');
    await capture(page, `owner-${device.name}-pilot-within-budget.png`);
    expect(errors).toEqual([]);
  });
}
test('future end date preserves observed recency and withholds pilots until coverage is restored', async ({ page, request }) => {
  const errors = observe(page);
  await page.setViewportSize({ width: 375, height: 812 });
  const snapshot = await (await request.get('/api/v1/owner/dashboard')).json();
  const observedSlipping = snapshot.segments.find(segment => segment.id === 'slipping').customers;
  const future = new Date(snapshot.source.coverage_end + 'T12:00:00Z');
  future.setUTCDate(future.getUTCDate() + 31);
  const futureEnd = future.toISOString().slice(0, 10);
  await ready(page);
  await expect(page.locator('#segments [data-segment="slipping"] .segment-value')).toHaveText(String(observedSlipping));
  await page.locator('[data-view="experiments"]').click();
  await expect(page.locator('#manual-pilot')).toBeEnabled();
  await page.locator('#manual-pilot').click();
  await expect(page.locator('#draft-card')).toBeVisible();

  await page.locator('#end-date').fill(futureEnd);
  const futureEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/dashboard');
  await page.locator('#date-form button[type="submit"]').click();
  const futureResponse = await futureEvent;
  expect(futureResponse.status()).toBe(200);
  const futureSnapshot = await futureResponse.json();
  expect(futureSnapshot.source.segment_as_of).toBe(snapshot.source.coverage_end);
  expect(futureSnapshot.segments.find(segment => segment.id === 'slipping').customers).toBe(observedSlipping);
  expect(futureSnapshot.customers.map(row => [row.id, row.segment, row.days_since_last]).sort()).toEqual(snapshot.customers.map(row => [row.id, row.segment, row.days_since_last]).sort());
  expect(futureSnapshot.actions).toHaveLength(0);
  await expect(page.locator('#coverage-warning')).toBeVisible();
  await expect(page.locator('#coverage-warning')).toContainText('Empty later dates are unknown');
  await expect(page.locator('#date-caption')).toContainText(snapshot.source.coverage_end);
  await expect(page.locator('#actions')).toContainText('Suggestions are withheld');
  await expect(page.locator('#actions button')).toHaveCount(0);
  await expect(page.locator('#manual-pilot')).toBeDisabled();
  await expect(page.locator('#draft-card')).toBeHidden();
  await expect(page.locator('#manual-pilot-note')).toContainText('Later customer inactivity is unknown');
  await noOverflow(page);
  await capture(page, 'owner-mobile-future-coverage.png');
  await page.locator('[data-view="overview"]').click();
  await expect(page.locator('#segments [data-segment="slipping"] .segment-value')).toHaveText(String(observedSlipping));

  await page.locator('#end-date').fill(snapshot.source.coverage_end);
  const coveredEvent = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/owner/dashboard');
  await page.locator('#date-form button[type="submit"]').click();
  expect((await coveredEvent).status()).toBe(200);
  await expect(page.locator('#coverage-warning')).toBeHidden();
  await page.locator('[data-view="experiments"]').click();
  await expect(page.locator('#manual-pilot')).toBeEnabled();
  await expect(page.locator('#actions button')).not.toHaveCount(0);
  await page.locator('#manual-pilot').click();
  await expect(page.locator('#draft-card')).toBeVisible();
  expect(errors).toEqual([]);
});