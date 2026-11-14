(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { data: null, token: '', view: 'overview', sequence: 0, controller: null, askSequence: 0, askController: null, draft: null, comparisonSequence: 0, comparisonController: null, pilotSequence: 0, pilotController: null, pilotResult: null };
  const customerState = { id: null, opener: null, sequence: 0, controller: null, busy: false, bound: null, nextOffset: null, total: null, events: [] };
  const savedState = { canSave: null, items: [], nextOffset: null, total: 0, listSequence: 0, listController: null, listBusy: false, saveSequence: 0, saveController: null, saveBusy: false, detailSequence: 0, detailController: null, current: null, opener: null, noteSequence: 0, noteController: null, noteBusy: false };
  const colors = { champions: '#315b43', regulars: '#78966b', promising: '#b7c89e', slipping: '#bb8059', new: '#ddd9bd' };
  const labels = { champions: 'Champions', regulars: 'Regulars', promising: 'Other buyers', slipping: 'Quiet repeat buyers', new: 'Newly observed' };
  const money = (value) => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value / 100) : '—';
  const integer = (value) => Number.isFinite(value) ? new Intl.NumberFormat('en-US').format(value) : '—';
  const percent = (value) => Number.isFinite(value) ? `${Math.round(value * 100)}%` : '—';
  const shortDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) : value || '—';
  const titleCase = (value) => String(value).replace(/_/g, ' ').replace(/^./, (letter) => letter.toUpperCase());
  const element = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  const setText = (id, text) => { $(id).textContent = text; };
  const status = (text, error = false) => { setText('status', text); $('status').classList.toggle('error', error); };
  const authHeaders = () => state.token ? { Authorization: `Bearer ${state.token}` } : {};
  const periodQuery = () => { const query = new URLSearchParams(); if ($('start-date').value) query.set('start', $('start-date').value); if ($('end-date').value) query.set('end', $('end-date').value); return query.toString(); };
  const options = (extra = {}) => ({ cache: 'no-store', redirect: 'error', credentials: 'same-origin', ...extra, headers: { ...authHeaders(), ...(extra.headers || {}) } });
  const resetAnswer = () => { state.askSequence++; state.askController?.abort(); $('ask-result').hidden = true; setText('answer', ''); $('evidence').replaceChildren(); $('answer-limitations').replaceChildren(); $('answer-limits-details').open = false; setText('answer-source', ''); setText('answer-meta', ''); $('answer-activity').replaceChildren(); $('answer-activity-details').hidden = true; $('answer-activity-details').open = false; $('ask-result').classList.remove('incomplete'); setText('ask-status', ''); $('ask-submit').disabled = false; };
  const clearData = (preserveSnapshot = false) => { resetAnalysis(); if (!preserveSnapshot) { state.data = null; $('dashboard-content').hidden = true; $('period-status').hidden = true; resetSavedRequests(true); } resetAnswer(); $('draft-card').hidden = true; state.draft = null; $('draft-hypothesis').value = ''; $('draft-measure').value = ''; setText('draft-description', ''); };
  const unlockRequired = () => { state.token = ''; $('lock-button').hidden = true; clearData(); $('access-panel').hidden = false; status('This workspace requires an owner access token.'); $('access-token').focus(); };
  const fetchJson = async (url, extra = {}) => { const response = await fetch(url, options(extra)); if (response.status === 401) { const error = new Error('Owner access token required.'); error.auth = true; throw error; } if (!response.ok) { let detail = ''; try { const body = await response.json(); if (typeof body.detail === 'string') detail = body.detail; } catch {} const error = new Error(detail || `Request failed (${response.status}). Please try again.`); error.status = response.status; throw error; } return response.json(); };

  function switchView(view) {
    const views = { overview: ['Overview', 'YOUR CUSTOMER STORY', 'Good business starts\nwith familiar faces.', 'See who returns, what matters, and where to put your energy.'], customers: ['Customers', 'KNOW YOUR PEOPLE', 'From first visits\nto familiar faces.', 'Explore the purchase patterns behind your customer relationships.'], experiments: ['Experiments', 'A THOUGHTFUL NEXT STEP', 'Turn a little insight\ninto a little progress.', 'Choose one idea, define success, and learn before doing more.'] };
    if (!views[view]) return;
    if (state.view !== view) resetAnalysis();
    state.view = view;
    const [label, eyebrow, title, lede] = views[view];
    setText('view-label', label); setText('page-eyebrow', eyebrow); setText('page-lede', lede);
    $('page-title').replaceChildren(); title.split('\n').forEach((line, i) => { if (i) $('page-title').append(document.createElement('br')); $('page-title').append(document.createTextNode(line)); });
    document.querySelectorAll('[data-view]').forEach((button) => { const active = button.dataset.view === view; button.classList.toggle('active', active); if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current'); });
    ['overview', 'customers', 'experiments'].forEach((name) => { $(`${name}-panel`).hidden = name !== view; });
    if (view === 'experiments' && state.data) loadSavedPilots();
  }

  function renderChart(rows) {
    const host = $('revenue-chart'); host.replaceChildren();
    const points = rows.filter((row) => Number.isFinite(row.net_revenue_cents));
    if (!points.length) { host.append(element('div', 'empty-state', 'No observed revenue in this period.')); return; }
    const ns = 'http://www.w3.org/2000/svg';
    const svg = (tag, attrs = {}) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); return node; };
    const width = 650, height = 200, left = 46, right = 10, top = 12, bottom = 13;
    const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    const min = Math.min(0, ...points.map((p) => p.net_revenue_cents));
    const max = Math.max(100, ...points.map((p) => p.net_revenue_cents));
    const x = (i) => left + (points.length === 1 ? (width - left - right) / 2 : i * (width - left - right) / (points.length - 1));
    const y = (value) => top + (max - value) / (max - min) * (height - top - bottom);
    for (let i = 0; i <= 4; i++) { const value = min + (max - min) * i / 4; const lineY = y(value); chart.append(svg('line', { x1: left, x2: width - right, y1: lineY, y2: lineY, stroke: '#e8ebdf', 'stroke-dasharray': '3 5', 'stroke-width': '.8' })); const label = svg('text', { x: left - 9, y: lineY + 3, 'text-anchor': 'end', fill: '#596950', 'font-size': '9', 'font-family': 'system-ui' }); label.textContent = `$${Math.round(value / 100)}`; chart.append(label); }
    const coords = points.map((point, i) => `${x(i)},${y(point.net_revenue_cents)}`);
    const defs = svg('defs'); const gradient = svg('linearGradient', { id: 'revenue-fill', x1: 0, y1: 0, x2: 0, y2: 1 }); gradient.append(svg('stop', { offset: '0%', 'stop-color': '#b0c59a', 'stop-opacity': '.35' }), svg('stop', { offset: '100%', 'stop-color': '#c6d6b6', 'stop-opacity': '.02' })); defs.append(gradient); chart.append(defs);
    if (points.length > 1) chart.append(svg('path', { d: `M ${x(0)},${y(0)} L ${coords.join(' L ')} L ${x(points.length - 1)},${y(0)} Z`, fill: 'url(#revenue-fill)' }));
    chart.append(svg('path', { d: `M ${coords.join(' L ')}`, fill: 'none', stroke: '#426c4e', 'stroke-width': '2.4', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'vector-effect': 'non-scaling-stroke' }));
    points.forEach((point, i) => { const dot = svg('circle', { cx: x(i), cy: y(point.net_revenue_cents), r: points.length <= 8 ? 3 : 2, fill: '#426c4e' }); const title = svg('title'); title.textContent = `${point.date}: ${money(point.net_revenue_cents)} net revenue, ${integer(point.orders)} orders`; dot.append(title); chart.append(dot); });
    host.append(chart); host.setAttribute('aria-label', `Daily net revenue from ${points[0].date} through ${points.at(-1).date}. Total ${money(points.reduce((sum, p) => sum + p.net_revenue_cents, 0))}.`);
    setText('chart-start', shortDate(points[0].date)); setText('chart-end', shortDate(points.at(-1).date));
  }

  function renderSegments(segments) {
    $('segments').replaceChildren(); $('segment-stack').replaceChildren();
    const previous = $('segment-filter').value;
    $('segment-filter').replaceChildren(element('option', '', 'All customer groups')); $('segment-filter').firstChild.value = 'all';
    const total = segments.reduce((sum, segment) => sum + segment.customers, 0);
    segments.forEach((segment) => {
      const color = colors[segment.id] || '#a7b598';
      const bar = element('span'); bar.style.background = color; bar.style.flex = String(segment.customers); if (segment.customers > 0) $('segment-stack').append(bar);
      const button = element('button', 'segment-row'); button.type = 'button'; button.title = segment.description; button.dataset.segment = segment.id;
      const dot = element('span', 'segment-dot'); dot.style.background = color;
      button.append(dot, element('span', 'segment-label', segment.label), element('span', 'segment-value', integer(segment.customers)), element('span', 'segment-share', total ? percent(segment.customers / total) : '—'));
      button.addEventListener('click', () => { $('segment-filter').value = segment.id; $('customer-search').value = ''; renderCustomers(); switchView('customers'); });
      $('segments').append(button);
      const option = element('option', '', segment.label); option.value = segment.id; $('segment-filter').append(option);
    });
    if ([...$('segment-filter').options].some((option) => option.value === previous)) $('segment-filter').value = previous;
  }

  function clearCustomerEvidence() {
    $('customer-evidence-content').hidden = true; $('customer-load-more').hidden = true; $('customer-evidence-refresh').hidden = true;
    ['customer-history-metrics', 'customer-period-metrics', 'customer-events', 'customer-evidence-limitations'].forEach((id) => $(id).replaceChildren());
    ['customer-evidence-group', 'customer-evidence-cutoff', 'customer-evidence-rule', 'customer-evidence-scope', 'customer-history-dates', 'customer-period-dates', 'customer-event-count', 'customer-evidence-source'].forEach((id) => setText(id, ''));
    customerState.events = []; customerState.nextOffset = null; customerState.total = null;
  }
  function closeCustomerDrawer(restoreFocus = true) {
    const opener = customerState.opener, wasOpen = !$('customer-drawer').hidden;
    customerState.sequence++; customerState.controller?.abort(); customerState.busy = false; customerState.id = null; customerState.bound = null; customerState.opener = null;
    clearCustomerEvidence(); $('customer-drawer').hidden = true; $('customer-drawer-backdrop').hidden = true; $('customer-drawer').setAttribute('aria-busy', 'false'); setText('customer-evidence-title', 'Customer evidence'); setText('customer-evidence-status', '');
    if ($('data-drawer').hidden) { $('main').removeAttribute('inert'); document.querySelector('.sidebar').removeAttribute('inert'); }
    if (restoreFocus && wasOpen) { if (opener?.isConnected) opener.focus(); else $('customer-search').focus(); }
  }
  function openCustomerDrawer(id, opener) {
    if (!state.data) return;
    closeCustomerDrawer(false); customerState.id = id; customerState.opener = opener;
    customerState.bound = { dataSequence: state.sequence, revision: state.data.source.revision, dataset_id: state.data.source.dataset_id, period: { ...state.data.period } };
    $('customer-drawer').hidden = false; $('customer-drawer-backdrop').hidden = false; $('main').setAttribute('inert', ''); document.querySelector('.sidebar').setAttribute('inert', '');
    setText('customer-evidence-title', state.data.customers.find((customer) => customer.id === id)?.label || id); $('close-customer-drawer').focus(); loadCustomerPage(0);
  }
  function customerMetrics(target, metrics) {
    $(target).replaceChildren(); [['Completed purchases', integer(metrics.orders)], ['Gross purchases', money(metrics.gross_revenue_cents)], ['Refunds deducted', `−${money(metrics.refund_cents)}`], ['Net revenue', money(metrics.net_revenue_cents)]].forEach(([label, value]) => { $(target).append(element('dt', '', label), element('dd', '', value)); });
  }
  function renderCustomerSummary(result) {
    setText('customer-evidence-title', result.customer.label || result.customer.id); setText('customer-evidence-group', result.segment.label); setText('customer-evidence-cutoff', `Observed through ${result.segment.as_of} · UTC`); setText('customer-evidence-rule', result.segment.description);
    setText('customer-evidence-scope', `This group uses all ${integer(result.observed_history.orders)} observed completed purchases through ${result.segment.as_of}. The table's ${integer(result.period_metrics.orders)} purchases and net revenue cover only ${result.period.start} to ${result.period.end}. A customer can qualify as a regular even with fewer purchases in the selected period.`);
    setText('customer-history-dates', `First seen ${result.observed_history.first_seen} · Last seen ${result.observed_history.last_seen}`); setText('customer-period-dates', `${result.period.start} to ${result.period.end} · UTC`);
    customerMetrics('customer-history-metrics', result.observed_history); customerMetrics('customer-period-metrics', result.period_metrics);
    setText('customer-evidence-source', `${result.source.label} · ${result.source.synthetic ? 'Synthetic records' : 'Owner-provided records'}`); $('customer-evidence-limitations').replaceChildren(); (result.source.limitations || []).forEach((note) => $('customer-evidence-limitations').append(element('li', '', note)));
  }
  function customerSourceError() { const error = new Error('The dataset or observation window changed. Close this panel and refresh the workspace before reviewing this customer.'); error.staleSource = true; return error; }
  function validateCustomerPage(result, offset) {
    const bound = customerState.bound, pagination = result.pagination;
    if (!state.data || !bound || !result.source?.revision || result.source.revision !== bound.revision || result.source.dataset_id !== bound.dataset_id || result.source.revision !== state.data.source.revision || result.source.dataset_id !== state.data.source.dataset_id || !samePeriod(result.period, bound.period) || !samePeriod(result.period, state.data.period) || result.customer?.id !== customerState.id) throw customerSourceError();
    if (!result.segment || !result.observed_history || !result.period_metrics || !Array.isArray(result.events) || !pagination || pagination.offset !== offset || pagination.limit !== 50 || !Number.isSafeInteger(pagination.total) || pagination.total < 0 || result.events.length > 50) throw new Error('The customer evidence page is incomplete. Close this panel and try again.');
    if ((customerState.total !== null && customerState.total !== pagination.total) || offset !== customerState.events.length) throw customerSourceError();
    const ending = offset + result.events.length;
    if (ending > pagination.total || (pagination.next_offset === null ? ending !== pagination.total : pagination.next_offset !== ending || ending >= pagination.total || !result.events.length)) throw new Error('The customer evidence pagination is inconsistent. Refresh the workspace and try again.');
    const seen = new Set(customerState.events.map((event) => `${event.kind}:${event.id}`)); let previousDate = customerState.events.at(-1)?.date;
    for (const event of result.events) {
      if (!['purchase', 'refund'].includes(event.kind) || typeof event.id !== 'string' || typeof event.order_id !== 'string' || !Number.isSafeInteger(event.amount_cents) || event.amount_cents < 0 || !/^\d{4}-\d{2}-\d{2}$/.test(event.date) || event.date > result.segment.as_of || typeof event.within_period !== 'boolean') throw new Error('The customer timeline contains an invalid record. Refresh the workspace and try again.');
      if (event.within_period !== (event.date >= result.period.start && event.date <= result.period.end) || seen.has(`${event.kind}:${event.id}`) || (previousDate && event.date > previousDate)) throw new Error('The customer timeline changed while paging. Refresh the workspace and try again.');
      seen.add(`${event.kind}:${event.id}`); previousDate = event.date;
    }
  }
  function appendCustomerEvents(events) {
    events.forEach((event) => {
      const row = element('li', `customer-event ${event.kind}`); row.dataset.eventId = `${event.kind}:${event.id}`;
      const icon = element('span', 'customer-event-icon', event.kind === 'refund' ? '↩' : '+'); icon.setAttribute('aria-hidden', 'true');
      const detail = element('div'); detail.append(element('div', 'customer-event-title', event.kind === 'refund' ? 'Refund recorded' : 'Completed purchase'), element('div', 'customer-event-meta', `${event.date} · ${event.kind === 'refund' ? `Refund ${event.id} · Original order ${event.order_id}` : `Order ${event.order_id}`}`), element('span', `customer-event-period${event.within_period ? '' : ' outside'}`, event.within_period ? 'In selected period' : 'Outside selected period'));
      row.append(icon, detail, element('div', 'customer-event-amount', `${event.kind === 'refund' ? '−' : '+'}${money(event.amount_cents)}`)); $('customer-events').append(row);
    });
  }
  async function loadCustomerPage(offset) {
    if (customerState.busy || !customerState.id || !customerState.bound) return;
    if (document.activeElement === $('customer-load-more')) $('customer-event-count').focus();
    const sequence = customerState.sequence, bound = customerState.bound; customerState.busy = true; customerState.controller = new AbortController(); $('customer-drawer').setAttribute('aria-busy', 'true'); $('customer-load-more').disabled = true; $('customer-evidence-status').classList.remove('error'); setText('customer-evidence-status', offset ? 'Loading earlier records…' : 'Reading the purchases behind this customer group…');
    try {
      const query = new URLSearchParams({ ...bound.period, offset: String(offset), limit: '50', expected_revision: bound.revision });
      const result = await fetchJson(`/api/v1/owner/customers/${encodeURIComponent(customerState.id)}?${query}`, { signal: customerState.controller.signal });
      if (sequence !== customerState.sequence || bound.dataSequence !== state.sequence || $('customer-drawer').hidden) return;
      validateCustomerPage(result, offset); if (!offset) renderCustomerSummary(result); appendCustomerEvents(result.events);
      customerState.events.push(...result.events); customerState.nextOffset = result.pagination.next_offset; customerState.total = result.pagination.total;
      setText('customer-event-count', `${integer(customerState.events.length)} of ${integer(customerState.total)} observed records shown`); $('customer-load-more').hidden = customerState.nextOffset === null; $('customer-events-empty').hidden = customerState.total > 0; $('customer-evidence-content').hidden = false; setText('customer-evidence-status', '');
    } catch (error) {
      if (sequence !== customerState.sequence || bound.dataSequence !== state.sequence || error.name === 'AbortError') return;
      if (error.auth) { closeCustomerDrawer(false); unlockRequired(); }
      else if (error.status === 409 || error.staleSource) { const needsFocus = $('customer-evidence-content').contains(document.activeElement); clearCustomerEvidence(); setText('customer-evidence-status', 'The dataset changed. Refresh the workspace before reviewing this customer; no mismatched evidence is shown.'); $('customer-evidence-refresh').hidden = false; $('customer-evidence-status').classList.add('error'); if (needsFocus) $('customer-evidence-refresh').focus(); }
      else { setText('customer-evidence-status', error.message || 'Could not load customer evidence. Close this panel and try again.'); $('customer-evidence-status').classList.add('error'); }
    } finally { if (sequence === customerState.sequence) { customerState.busy = false; $('customer-drawer').setAttribute('aria-busy', 'false'); $('customer-load-more').disabled = false; } }
  }
  $('close-customer-drawer').addEventListener('click', () => closeCustomerDrawer()); $('customer-drawer-backdrop').addEventListener('click', () => closeCustomerDrawer());
  $('customer-load-more').addEventListener('click', () => { if (customerState.nextOffset !== null) loadCustomerPage(customerState.nextOffset); });
  $('customer-evidence-refresh').addEventListener('click', () => { closeCustomerDrawer(false); load(); });
  $('customer-drawer').addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeCustomerDrawer(); }
    if (event.key === 'Tab') { const focusable = [...$('customer-drawer').querySelectorAll('button, summary, [tabindex]')].filter((node) => !node.disabled && node.tabIndex >= 0 && !node.closest('[hidden]')); const first = focusable[0], last = focusable.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } }
  });

  function renderCustomers() {
    $('customers').replaceChildren();
    if (!state.data) return;
    const search = $('customer-search').value.trim().toLowerCase(), segment = $('segment-filter').value;
    const rows = state.data.customers.filter((customer) => (segment === 'all' || customer.segment === segment) && `${customer.id} ${customer.label}`.toLowerCase().includes(search));
    rows.forEach((customer) => {
      const row = element('tr'); const identity = element('td'); const customerButton = element('button', 'customer-link', customer.label || customer.id); customerButton.type = 'button'; customerButton.dataset.customerId = customer.id; customerButton.setAttribute('aria-haspopup', 'dialog'); customerButton.setAttribute('aria-controls', 'customer-drawer'); customerButton.setAttribute('aria-label', `View purchase history for ${customer.label || customer.id}`); customerButton.addEventListener('click', () => openCustomerDrawer(customer.id, customerButton)); identity.append(customerButton); row.append(identity);
      const group = element('td'); const badge = element('span', 'segment-badge'); const dot = element('span', 'segment-dot'); dot.style.background = colors[customer.segment] || '#a7b598'; badge.append(dot, document.createTextNode(state.data.segments.find((s) => s.id === customer.segment)?.label || labels[customer.segment] || customer.segment)); group.append(badge); row.append(group);
      row.append(element('td', '', integer(customer.orders)), element('td', '', money(customer.revenue_cents)), element('td', '', shortDate(customer.last_seen)), element('td', '', integer(customer.days_since_last)));
      $('customers').append(row);
    });
    setText('customer-count', `${integer(rows.length)} of ${integer(state.data.customers.length)} customers`); $('customer-empty').hidden = rows.length > 0;
  }

  function renderCohorts(cohorts) {
    $('cohorts').replaceChildren();
    if (!cohorts.length) { const row = element('tr'); const cell = element('td', 'empty-state', 'No customer cohorts in the observed history.'); cell.colSpan = 6; row.append(cell); $('cohorts').append(row); return; }
    cohorts.forEach((cohort) => {
      const row = element('tr'); let month = cohort.month; if (/^\d{4}-\d{2}$/.test(month)) month = new Date(`${month}-15T12:00:00Z`).toLocaleDateString('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
      row.append(element('td', '', month), element('td', '', integer(cohort.customers)));
      for (let i = 0; i < 4; i++) { const value = cohort.retention[i]; const cell = element('td'); const pill = element('span', 'retention-cell', percent(value)); if (Number.isFinite(value)) { const intensity = Math.min(1, Math.max(0, value)); pill.style.background = `rgba(100, 139, 72, ${0.07 + intensity * 0.3})`; pill.style.color = '#426039'; pill.title = `${percent(value)} observed active in cohort month ${i}`; } else { pill.style.background = '#f4f5ee'; pill.style.color = '#596950'; pill.title = 'Not yet observable'; } cell.append(pill); row.append(cell); }
      $('cohorts').append(row);
    });
  }

  function chooseDraft(action) {
    if (!state.data || state.data.period.end > state.data.source.coverage_end) { setText('manual-pilot-note', 'Choose an end date within observed coverage before drafting a pilot.'); return; }
    resetPilot();
    state.draft = action; $('draft-card').hidden = false;
    $('pilot-segment').value = action.segment;
    $('pilot-audience').value = String(Math.max(1, Math.min(20, action.customers)));
    $('pilot-offer').value = '5.00'; $('pilot-budget').value = '50.00'; $('pilot-days').value = '14';
    setText('draft-title', action.title); $('draft-name').value = action.title; setText('draft-description', action.reason);
    $('draft-hypothesis').value = action.suggestion || '';
    $('draft-measure').value = 'Share of assigned customers with a completed return visit within the same evaluation window; compare the offer group with a randomly assigned holdout.';
    setText('draft-status', 'Draft prepared in this tab. Review the idea and choose your own success criteria.');
    $('draft-hypothesis').focus();
  }

  function renderActions(actions) {
    $('actions').replaceChildren(); setText('action-count', integer(actions.length));
    const unobservedEnd = state.data.period.end > state.data.source.coverage_end, hasCustomers = state.data.customers.length > 0;
    $('manual-pilot').disabled = unobservedEnd || !hasCustomers;
    setText('manual-pilot-note', unobservedEnd ? 'Choose an end date within observed coverage before drafting a pilot. Later customer inactivity is unknown.' : !hasCustomers ? 'Load a period with observed customers before choosing an audience.' : 'Start from a suggestion, or define a pilot for any observed customer group.');
    setText('spotlight-title', actions[0]?.title || (unobservedEnd ? 'Stay within what you observed.' : 'Make room for your own idea.'));
    setText('spotlight-reason', actions[0]?.reason || (unobservedEnd ? 'Later dates contain no supplied evidence. Choose a covered period before reviewing a customer action.' : 'No automatic suggestion applies here. You can still define a small pilot using the customer groups you observed.'));
    if (!actions.length) $('actions').append(element('p', 'empty-state', unobservedEnd ? 'Suggestions are withheld because the selected end is outside observed coverage.' : hasCustomers ? 'No automatic suggestion applies to these groups. Draft your own pilot to choose an observed audience and assumptions.' : 'There are no observed customers available for a pilot.'));
    actions.forEach((action, index) => { const card = element('article', 'action-card'); card.append(element('span', 'action-number', String(index + 1).padStart(2, '0')), element('span', 'eyebrow', `${integer(action.customers)} CUSTOMERS · ${labels[action.segment] || action.segment || 'OBSERVED PATTERN'}`), element('h3', '', action.title), element('p', '', action.reason), element('p', 'action-metric', `Audience: ${action.metric}`)); const button = element('button', '', 'Draft an experiment'); button.type = 'button'; button.append(element('span', '', '→')); button.addEventListener('click', () => chooseDraft(action)); card.append(button); $('actions').append(card); });
  }

  function renderDefinitions(definitions, limitations) {
    $('definitions').replaceChildren(); const list = element('dl'); Object.entries(definitions || {}).forEach(([key, value]) => { list.append(element('dt', '', titleCase(key)), element('dd', '', typeof value === 'string' ? value : JSON.stringify(value))); }); $('definitions').append(list);
    $('limitations').replaceChildren(); (limitations || []).forEach((item) => $('limitations').append(element('li', '', item)));
  }

  function render(data) {
    if (!data || !data.metrics || !data.source || !data.period || !Array.isArray(data.customers) || !Array.isArray(data.segments) || !Array.isArray(data.trend) || !Array.isArray(data.cohorts) || !Array.isArray(data.actions)) throw new Error('The server returned an incomplete customer snapshot. Please refresh.');
    state.data = data;
    setText('business-name', data.business.name); setText('source-mini', data.source.synthetic ? 'Fictional demo data' : 'Owner-provided data'); setText('source-pill', data.source.synthetic ? 'Synthetic demo' : 'Observed data');
    $('start-date').value = data.period.start; $('end-date').value = data.period.end;
    const metrics = data.metrics;
    setText('metric-revenue', money(metrics.net_revenue_cents)); setText('revenue-detail', `${money(metrics.refund_cents)} in recorded refunds`);
    const noLookback = data.period.start <= data.source.coverage_start;
    const cutoff = data.source.segment_as_of || (data.period.end < data.source.coverage_end ? data.period.end : data.source.coverage_end);
    const unobservedEnd = data.period.end > data.source.coverage_end, uncoveredStart = data.period.start < data.source.coverage_start;
    setText('metric-returning', percent(metrics.returning_share)); setText('returning-detail', noLookback ? 'No earlier history supplied; not a first-time rate' : `${integer(metrics.returning_customers)} had a purchase before this period`);
    setText('metric-customers', integer(metrics.active_customers)); setText('customers-detail', 'Identified buyers in this period');
    setText('metric-average', money(metrics.gross_average_order_cents)); setText('orders-detail', 'Completed purchases before refunds');
    setText('repeat-share', percent(metrics.within_period_repeat_share)); setText('repeat-count', `${integer(metrics.within_period_repeat_customers)} of ${integer(metrics.active_customers)} active customers had at least two completed purchases.`);
    setText('customer-cutoff', `Groups and days since purchase are observed as of ${cutoff}. Orders and net revenue use ${data.period.start} to ${data.period.end}.`);
    setText('repeat-caveat', 'No supplied purchase history precedes this window. The returning-customer figure cannot establish whether buyers are new to the business; this repeat measure counts purchases inside the selected period.'); $('repeat-caveat').hidden = !noLookback;
    $('coverage-warning').hidden = !unobservedEnd && !uncoveredStart;
    setText('coverage-warning', unobservedEnd ? `Selected dates extend beyond observed history ending ${data.source.coverage_end}. Empty later dates are unknown, not confirmed zero sales. Customer groups stop at ${cutoff}; suggestions and pilot drafting are withheld until you choose a covered end date.` : uncoveredStart ? `The selected start precedes supplied history beginning ${data.source.coverage_start}. Empty earlier dates are unknown, not confirmed zero sales.` : '');
    renderChart(data.trend); renderSegments(data.segments); renderCustomers(); renderCohorts(data.cohorts); renderActions(data.actions); renderDefinitions(data.definitions, data.source.limitations);
    setText('source-label', data.source.label); setText('coverage-label', `Observed ${data.source.coverage_start} – ${data.source.coverage_end} · UTC`);
    setText('date-caption', `Customer groups as of ${cutoff} · UTC`);
    $('dashboard-content').hidden = false; $('access-panel').hidden = true; $('lock-button').hidden = !state.token;
    updatePeriodStatus(); status('');
  }

  function updatePeriodStatus(loading = false) {
    const changed = state.data && ($('start-date').value !== state.data.period.start || $('end-date').value !== state.data.period.end);
    $('period-status').hidden = !changed;
    if (changed) setText('period-status', `${loading ? 'Applying requested dates.' : 'Date edits are not applied yet.'} The workspace and questions still use ${state.data.period.start} to ${state.data.period.end} (UTC).${loading ? '' : ' Apply the date range to update them.'}`);
  }
  function datesEdited() {
    state.controller?.abort(); state.controller = null; state.sequence++;
    resetAnalysis(); resetAnswer(); $('refresh').disabled = false; updatePeriodStatus();
    status(state.data ? '' : 'Dates changed. Apply the date range to load customer data.');
  }
  async function load() {
    const start = $('start-date').value, end = $('end-date').value;
    if (start && end && start > end) { status('Choose an end date on or after the start date.', true); return; }
    state.controller?.abort(); const controller = new AbortController(); state.controller = controller; const sequence = ++state.sequence;
    clearData(true); $('access-panel').hidden = true; $('refresh').disabled = true; updatePeriodStatus(true); status('Reading your customer story…');
    try { const query = periodQuery(); const data = await fetchJson(`/api/v1/owner/dashboard${query ? `?${query}` : ''}`, { signal: controller.signal }); if (sequence !== state.sequence) return; render(data); state.sequence++; resetAnalysis(); resetAnswer(); if (state.view === 'experiments') loadSavedPilots(); }
    catch (error) { if (sequence !== state.sequence || error.name === 'AbortError') return; clearData(); if (error.auth) unlockRequired(); else status(error.message || 'Could not load customer data. Please try again.', true); }
    finally { if (state.controller === controller) { state.controller = null; $('refresh').disabled = false; } }
  }

  function evidenceText(value) {
    if (typeof value === 'string') return value;
    if (value === null || value === undefined) return '';
    if (Array.isArray(value)) return value.map(evidenceText).join('\n');
    if (typeof value === 'object') return Object.entries(value).map(([key, item]) => `${titleCase(key)}: ${key.endsWith('_cents') && Number.isFinite(item) ? money(item) : evidenceText(item)}`).join(' · ');
    return String(value);
  }

  const evidenceMetricLabels = { net_revenue_cents: 'Net revenue', gross_revenue_cents: 'Gross purchases', refund_cents: 'Refunds recorded', order_count: 'Completed purchases', orders: 'Completed purchases', active_customers: 'Active customers', customers: 'Observed customers', returning_customers: 'Customers observed before this period', within_period_repeat_customers: 'Customers who bought twice in this period', within_period_repeat_share: 'Bought twice in this period', returning_share: 'Previously observed customer share', gross_average_order_cents: 'Average purchase before refunds', average_order_cents: 'Net revenue per completed purchase' };
  const evidenceGroupLabels = { first_observed: 'First observed this period', returned: 'Returned after a quiet period', continued: 'Bought in both periods', went_quiet: 'Bought earlier, no purchase now', refund_only: 'Refunds without period purchases' };
  const evidencePercent = (value) => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 }).format(value) : 'Unavailable';
  const evidenceDates = (period) => period?.start && period?.end ? `${period.start} to ${period.end} · UTC` : 'Selected observation window';
  function evidenceMetricValue(key, value) { if (value === null || value === undefined) return 'Unavailable'; if (key.endsWith('_cents')) return money(value); if (key.endsWith('_share') || key.endsWith('_fraction')) return evidencePercent(value); return Number.isFinite(value) ? integer(value) : String(value); }
  function evidenceStats(host, rows) { const grid = element('div', 'evidence-stats'); rows.forEach(([label, value, note]) => { const stat = element('div', 'evidence-stat'); stat.append(element('span', '', label), element('strong', '', value)); if (note) stat.append(element('small', '', note)); grid.append(stat); }); host.append(grid); }
  function evidenceTable(host, headings, rows, caption) { const scroller = element('div', 'table-scroll'); const table = element('table', 'evidence-table'); if (caption) table.append(element('caption', '', caption)); const head = element('thead'), header = element('tr'); headings.forEach((heading) => { const cell = element('th', '', heading); cell.scope = 'col'; header.append(cell); }); head.append(header); table.append(head); const body = element('tbody'); rows.forEach((values) => { const row = element('tr'); values.forEach((value) => row.append(element('td', '', value))); body.append(row); }); table.append(body); scroller.append(table); host.append(scroller); }
  function evidenceComparison(host, value) {
    host.append(element('h3', '', 'Observed revenue change'));
    if (value.available === false || !value.baseline?.metrics || !value.current?.metrics) { host.append(element('p', 'evidence-note warning', value.limitations?.[0] || 'A preceding-period comparison is unavailable for this observation window.')); return; }
    const percentageAvailable = value.percentage_available !== false && value.fully_covered === true && Number.isFinite(value.change_fraction);
    const changeNote = percentageAvailable ? `${value.change_fraction > 0 ? '+' : ''}${evidencePercent(value.change_fraction)} of earlier net revenue` : 'Percentage change unavailable';
    evidenceStats(host, [['Earlier net revenue', money(value.baseline.metrics.net_revenue_cents), evidenceDates(value.baseline.period)], ['Current net revenue', money(value.current.metrics.net_revenue_cents), evidenceDates(value.current.period)], ['Observed difference', signedMoney(value.delta_cents), changeNote]]);
    if (Array.isArray(value.groups)) evidenceTable(host, ['Customer pattern', 'Customers', 'Earlier', 'Current', 'Change'], value.groups.map((group) => [evidenceGroupLabels[group.id] || group.label || 'Other observed pattern', integer(group.customers), money(group.baseline_cents), money(group.current_cents), signedMoney(group.delta_cents)]), 'How customer groups contribute to the difference');
    const coverageNote = value.fully_covered ? `${integer(value.days_per_period)} days in each window, within supplied history. These contributions explain the arithmetic, not what caused behavior to change.` : 'At least one window extends beyond supplied history. Empty dates are unknown; percentages and rate comparisons are unavailable.';
    host.append(element('p', `evidence-note${value.fully_covered ? '' : ' warning'}`, coverageNote));
    if (value.fully_covered && !percentageAvailable) host.append(element('p', 'evidence-note warning', 'Percentage change is unavailable because earlier net revenue is zero or negative.'));
  }
  function evidenceSummary(host, value) {
    const metrics = value.metrics || value; host.append(element('h3', '', 'Observed business totals')); if (value.period) host.append(element('p', 'evidence-period', evidenceDates(value.period)));
    const keys = ['net_revenue_cents', 'order_count', 'active_customers', 'gross_revenue_cents', 'refund_cents', 'gross_average_order_cents', 'within_period_repeat_customers', 'within_period_repeat_share', 'returning_customers'];
    const rows = keys.filter((key) => Object.hasOwn(metrics, key)).map((key) => [evidenceMetricLabels[key], evidenceMetricValue(key, metrics[key])]);
    if (Object.hasOwn(metrics, 'customers')) rows.push(['Observed customers', integer(metrics.customers)]);
    evidenceStats(host, rows); if (value.source?.period_within_coverage === false) host.append(element('p', 'evidence-note warning', 'Some selected dates are outside supplied coverage. Empty dates remain unknown.'));
  }
  function evidenceSegments(host, rows, value) {
    host.append(element('h3', '', rows.length === 1 ? `${rows[0].label || labels[rows[0].segment || rows[0].id] || 'Observed'} customer group` : 'Observed customer groups'));
    evidenceTable(host, ['Customer group', 'Customers', 'Selected-period net revenue'], rows.map((row) => [row.label || labels[row.segment || row.id] || 'Observed group', integer(row.customers), money(row.revenue_cents)]));
    const cutoff = value.source?.segment_as_of || state.data?.source.segment_as_of;
    host.append(element('p', 'evidence-note', `${cutoff ? `Groups use purchase history through ${cutoff}. ` : ''}Revenue uses only the selected period. These are purchase-pattern rules, not churn predictions.`));
    if (rows.length === 1 && rows[0].description) host.append(element('p', 'evidence-note', rows[0].description));
  }
  function evidenceCohorts(host, rows) {
    host.append(element('h3', '', 'Do observed customers come back?'));
    const shown = rows.slice(-4); evidenceTable(host, ['First observed', 'Customers', 'Month 0', 'Month 1', 'Month 2', 'Month 3'], shown.map((cohort) => [cohort.month, integer(cohort.customers), ...Array.from({ length: 4 }, (_, index) => cohort.retention?.[index] === null || cohort.retention?.[index] === undefined ? '—' : evidencePercent(cohort.retention[index]))]));
    host.append(element('p', 'evidence-note', `${rows.length > 4 ? `Showing the latest 4 of ${integer(rows.length)} cohorts. ` : ''}Each percentage is the share with a completed purchase in that cohort month. “—” means not yet observable, not zero; month 0 is the starting group.`));
  }
  function evidencePlan(host, value) {
    host.append(element('h3', '', 'An illustrative customer pilot')); host.append(element('p', 'evidence-period', `${labels[value.segment] || 'Selected customer group'} · ${integer(value.window_days)} evaluation days`));
    evidenceStats(host, [['Offer group', integer(value.treatment_customers), 'Proposed customers'], ['Holdout group', integer(value.holdout_customers), 'Proposed customers'], ['Maximum offer cost', money(value.maximum_offer_cost_cents), `${money(value.offer_cents)} per offer`]]);
    host.append(element('p', `evidence-note${value.within_budget === false || value.feasible_pilot === false ? ' warning' : ''}`, value.within_budget === false ? `This exceeds the entered ${money(value.budget_cents)} offer budget. Revise the assumptions before considering a pilot.` : value.feasible_pilot === false ? 'The observed audience is too small for this illustrative split.' : `${value.budget_cents === null ? 'No offer budget was supplied.' : `Fits the entered ${money(value.budget_cents)} offer budget.`} This does not establish effectiveness or statistical power.`));
    host.append(element('p', 'evidence-note', 'Calculation only: Regulars does not assign or contact customers. Maximum offer cost excludes delivery, labor, product, and setup costs.'));
  }
  function renderEvidence(item) {
    const card = element('article', 'evidence-item evidence-card'), primary = element('div', 'evidence-primary'); card.append(primary);
    const tool = item && typeof item === 'object' ? item.tool : null;
    const value = tool && item.result && typeof item.result === 'object' ? item.result : item;
    if (typeof value === 'string') primary.append(element('p', '', value));
    else if (value && typeof value === 'object') {
      if (tool === 'owner_revenue_change' || (Object.hasOwn(value, 'available') && (value.baseline || value.reason)) || (value.baseline && value.current && Array.isArray(value.groups))) evidenceComparison(primary, value);
      else if (Object.hasOwn(value, 'metric') && Object.hasOwn(value, 'value')) evidenceStats(primary, [[evidenceMetricLabels[value.metric] || titleCase(String(value.metric).replace(/_cents$/, '')), evidenceMetricValue(String(value.metric), value.value)]]);
      else if (tool === 'plan_owner_experiment' || Object.hasOwn(value, 'maximum_offer_cost_cents')) evidencePlan(primary, value);
      else if (Array.isArray(value.segments)) evidenceSegments(primary, value.segments, value);
      else if (Array.isArray(value.cohorts)) evidenceCohorts(primary, value.cohorts);
      else if (value.month && Array.isArray(value.retention)) evidenceCohorts(primary, [value]);
      else if ((labels[value.id] || labels[value.segment]) && Object.hasOwn(value, 'customers') && Object.hasOwn(value, 'revenue_cents')) evidenceSegments(primary, [value], value);
      else if (value.title && value.reason) { primary.append(element('h3', '', value.title), element('p', '', value.reason)); if (value.customers !== undefined) primary.append(element('p', 'evidence-note', `${integer(value.customers)} observed customers in this audience.`)); if (value.suggestion) primary.append(element('p', 'evidence-note', value.suggestion)); }
      else if (value.metrics || ['net_revenue_cents', 'gross_revenue_cents', 'order_count', 'customers'].some((key) => Object.hasOwn(value, key))) evidenceSummary(primary, value);
      else { primary.append(element('h3', '', 'Recorded calculation'), element('p', 'evidence-note', 'Open calculation details to inspect the full recorded result.')); }
    } else primary.append(element('p', '', String(value ?? 'No evidence returned.')));
    if (item && typeof item === 'object') { const details = element('details', 'evidence-details'); details.append(element('summary', '', 'Calculation details'), element('pre', '', JSON.stringify(item, null, 2))); card.append(details); }
    return card;
  }

  async function ask(event) {
    event.preventDefault(); const question = $('question').value.trim(); if (!question || !state.data) return;
    resetAnswer(); const sequence = state.askSequence; const dataSequence = state.sequence; state.askController = new AbortController(); $('ask-submit').disabled = true; setText('ask-status', 'Investigating the observed customer data…');
    try { const result = await fetchJson('/api/v1/owner/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, start: state.data.period.start, end: state.data.period.end }), signal: state.askController.signal });
      if (sequence !== state.askSequence || dataSequence !== state.sequence) return;
      if (!result.source?.revision || result.source.revision !== state.data.source.revision || !result.source.dataset_id || result.source.dataset_id !== state.data.source.dataset_id || !samePeriod(result.period, state.data.period)) throw new Error('The dataset changed during this investigation. Refresh the dashboard and ask again.');
      if (typeof result.answer !== 'string') throw new Error('The investigation returned no answer. Please try again.');
      setText('answer', result.answer); $('evidence').replaceChildren();
      const evidence = Array.isArray(result.evidence) ? result.evidence : result.evidence ? [result.evidence] : [];
      evidence.forEach((item) => $('evidence').append(renderEvidence(item)));
      const limitations = [...new Set((result.limitations || []).filter((item) => typeof item === 'string'))];
      $('answer-limitations').replaceChildren(); limitations.forEach((item) => $('answer-limitations').append(element('li', '', item)));
      setText('answer-source', `${result.source.label || 'Observed customer records'} · ${result.source.synthetic ? 'Synthetic data' : 'Owner-provided data'} · ${evidenceDates(result.period)}`);
      setText('answer-limits-summary', `Sources and limits${limitations.length ? ` (${limitations.length})` : ''}`); $('answer-limits-details').open = false;
      const isModel = ['gemini', 'nemotron'].includes(result.provider), completed = result.status === 'answered';
      const providerLabel = { deterministic: 'Offline calculation', gemini: 'Gemini', nemotron: 'NVIDIA Nemotron' }[result.provider] || 'Unknown provider';
      const resultLabel = { answered: 'Answered', limited: 'Resource limit reached', unavailable: 'Unavailable', clarification: 'Clarification needed' }[result.status] || 'Incomplete response';
      setText('answer-label', completed ? (isModel ? 'MODEL INTERPRETATION · CHECK THE EVIDENCE' : 'OBSERVED DATA, EXPLAINED') : `INCOMPLETE INVESTIGATION · ${resultLabel.toUpperCase()}`);
      setText('answer-meta', `${providerLabel} · ${resultLabel}${Number.isInteger(result.model_calls) ? ` · ${result.model_calls} model calls` : ''}${Number.isInteger(result.tool_calls) ? ` · ${result.tool_calls} tool calls` : ''}${!completed && evidence.length ? ' · Partial evidence only' : ''}`);
      $('ask-result').classList.toggle('incomplete', !completed); $('answer-activity').replaceChildren();
      const activity = Array.isArray(result.activity) ? result.activity : []; activity.forEach((item) => $('answer-activity').append(element('li', '', evidenceText(item)))); $('answer-activity-details').hidden = activity.length === 0;
      $('ask-result').hidden = false; setText('ask-status', '');
    } catch (error) { if (sequence !== state.askSequence || dataSequence !== state.sequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('ask-status', error.message || 'Could not complete this investigation.'); }
    finally { if (sequence === state.askSequence) $('ask-submit').disabled = false; }
  }

  async function exportCustomers() {
    if (!state.data) return; const sequence = state.sequence; $('export-customers').disabled = true;
    try { const query = new URLSearchParams(state.data.period); const response = await fetch(`/api/v1/owner/export.csv?${query}`, options()); if (sequence !== state.sequence) return; if (response.status === 401) { unlockRequired(); return; } if (!response.ok) throw new Error(`Export failed (${response.status}).`); if (!state.data.source.revision || response.headers?.get('X-Source-Revision') !== state.data.source.revision || !state.data.source.dataset_id || response.headers?.get('X-Dataset-ID') !== state.data.source.dataset_id) throw new Error('The dataset changed before export. Refresh the dashboard and export again.'); const blob = await response.blob(); if (sequence !== state.sequence) return; const url = URL.createObjectURL(blob); const link = element('a'); link.href = url; link.download = `regulars-customers-${state.data.period.start}-${state.data.period.end}.csv`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); status('Customer export downloaded for the selected period.'); }
    catch (error) { if (sequence === state.sequence) status(error.message || 'Export failed. Please try again.', true); }
    finally { $('export-customers').disabled = false; }
  }

  function resetComparison() { state.comparisonSequence++; state.comparisonController?.abort(); $('comparison-result').hidden = true; $('comparison-totals').replaceChildren(); $('comparison-groups').replaceChildren(); $('comparison-contributors').replaceChildren(); $('comparison-limitations').replaceChildren(); $('comparison-contributor-details').open = false; setText('comparison-status', ''); $('compare-button').disabled = false; }
  function resetPilot() { state.pilotResult = null; resetSaveOperation(); state.pilotSequence++; state.pilotController?.abort(); $('pilot-result').hidden = true; $('pilot-numbers').replaceChildren(); $('pilot-limitations').replaceChildren(); setText('pilot-status', ''); $('pilot-submit').disabled = false; }
  function resetAnalysis() { resetComparison(); resetPilot(); closeCustomerDrawer(false); resetSavedRequests(); }
  const samePeriod = (a, b) => a?.start === b?.start && a?.end === b?.end;
  const signedMoney = (value) => `${value > 0 ? '+' : ''}${money(value)}`;
  function renderComparison(result) {
    const expected = state.data;
    if (!result.source?.revision || result.source.revision !== expected.source.revision || !result.source.dataset_id || result.source.dataset_id !== expected.source.dataset_id || !samePeriod(result.current?.period, expected.period)) throw new Error('The dataset changed during this comparison. Refresh the dashboard and try again.');
    if (!result.baseline?.metrics || !Array.isArray(result.groups) || !Array.isArray(result.contributors) || !Number.isSafeInteger(result.delta_cents)) throw new Error('The comparison response is incomplete. Please try again.');
    $('comparison-totals').replaceChildren();
    const totals = [
      ['EARLIER OBSERVED REVENUE', money(result.baseline.metrics.net_revenue_cents), `${result.baseline.period.start} to ${result.baseline.period.end}`],
      ['CURRENT OBSERVED REVENUE', money(result.current.metrics.net_revenue_cents), `${result.current.period.start} to ${result.current.period.end}`],
      ['OBSERVED CHANGE', signedMoney(result.delta_cents), Number.isFinite(result.change_fraction) ? `${result.change_fraction > 0 ? '+' : ''}${percent(result.change_fraction)} · ${result.days_per_period} days per period` : 'Percentage unavailable · incomplete coverage or nonpositive baseline'],
    ];
    totals.forEach(([label, value, detail]) => { const card = element('div', 'comparison-total'); card.append(element('span', '', label), element('strong', '', value), element('small', '', detail)); $('comparison-totals').append(card); });
    setText('comparison-coverage', result.fully_covered ? 'Both windows fall within the supplied coverage. The difference describes observed records, not why behavior changed.' : 'Incomplete coverage: at least one window extends beyond supplied history. Empty dates are unknown, and percentage comparisons are unavailable.'); $('comparison-coverage').classList.toggle('warning', !result.fully_covered);
    $('comparison-groups').replaceChildren(); result.groups.forEach((group) => { const row = element('tr'); row.append(element('td', '', group.label), element('td', '', integer(group.customers)), element('td', '', money(group.baseline_cents)), element('td', '', money(group.current_cents)), element('td', group.delta_cents < 0 ? 'negative' : 'positive', signedMoney(group.delta_cents))); $('comparison-groups').append(row); });
    $('comparison-contributors').replaceChildren(); result.contributors.forEach((customer) => { const row = element('tr'); row.append(element('td', '', customer.customer_id), element('td', '', money(customer.baseline_cents)), element('td', '', money(customer.current_cents)), element('td', customer.delta_cents < 0 ? 'negative' : 'positive', signedMoney(customer.delta_cents))); $('comparison-contributors').append(row); });
    setText('comparison-omitted', `${integer(result.omitted_contributors)} additional contributors omitted from this detail table; their combined change is ${signedMoney(result.omitted_delta_cents)}. All customer contributions reconcile to ${signedMoney(result.reconciled_delta_cents)}.`);
    $('comparison-limitations').replaceChildren(); (result.limitations || []).forEach((item) => $('comparison-limitations').append(element('li', '', item))); $('comparison-result').hidden = false;
  }
  async function explainChange() { if (!state.data) return; resetComparison(); const sequence = state.comparisonSequence, dataSequence = state.sequence; state.comparisonController = new AbortController(); $('compare-button').disabled = true; setText('comparison-status', 'Comparing observed purchases and refunds across two equal windows…');
    try { const query = new URLSearchParams(state.data.period); const result = await fetchJson(`/api/v1/owner/compare?${query}`, { signal: state.comparisonController.signal }); if (sequence !== state.comparisonSequence || dataSequence !== state.sequence) return; renderComparison(result); setText('comparison-status', ''); }
    catch (error) { if (sequence !== state.comparisonSequence || dataSequence !== state.sequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('comparison-status', error.message || 'The period comparison could not be completed.'); }
    finally { if (sequence === state.comparisonSequence) $('compare-button').disabled = false; }
  }
  function decimalCents(value, label, maximum) { if (!/^\d+(?:\.\d{1,2})?$/.test(value.trim())) throw new Error(`${label} must be dollars with at most two decimal places.`); const [whole, fraction = ''] = value.trim().split('.'); const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0')); if (!Number.isSafeInteger(cents) || cents < 0 || cents > maximum) throw new Error(`${label} must be between $0.00 and ${money(maximum)}.`); return cents; }
  function wholeNumber(value, label, minimum, maximum) { if (!/^\d+$/.test(value)) throw new Error(`${label} must be a whole number.`); const number = Number(value); if (!Number.isSafeInteger(number) || number < minimum || number > maximum) throw new Error(`${label} must be between ${minimum} and ${maximum}.`); return number; }
  function renderPilot(result, reviewedInputs) {
    if (!result.source_revision || result.source_revision !== state.data.source.revision || !samePeriod(result.period, state.data.period)) throw new Error('The dataset changed while calculating. Refresh the dashboard and calculate again.');
    if (!Number.isSafeInteger(result.maximum_offer_cost_cents) || !Number.isSafeInteger(result.treatment_customers) || !Number.isSafeInteger(result.holdout_customers)) throw new Error('The pilot calculation response is incomplete.');
    if ((result.dataset_id && result.dataset_id !== state.data.source.dataset_id) || (result.source && (result.source.dataset_id !== state.data.source.dataset_id || result.source.revision !== state.data.source.revision)) || (result.inputs && !samePilotInputs(result.inputs, reviewedInputs))) throw new Error('The reviewed calculation does not match these inputs or this dataset. Calculate again.');
    const small = result.treatment_customers + result.holdout_customers < 4, overBudget = result.within_budget === false;
    const verdict = overBudget ? `Exceeds the offer budget by ${money(result.maximum_offer_cost_cents - result.budget_cents)}. Revise the audience cap, offer, or explicit budget.` : small ? 'Too few observed customers for this illustrative pilot. At least four are needed for the split.' : 'Fits the entered offer budget. Review eligibility, consent, and all other costs before deciding whether to proceed.';
    setText('pilot-verdict', verdict + (overBudget && small ? ' The observed audience is also smaller than four customers.' : '')); $('pilot-verdict').classList.toggle('warning', overBudget || small);
    $('pilot-numbers').replaceChildren(); [[integer(result.treatment_customers), 'Proposed offer group'], [integer(result.holdout_customers), 'Proposed holdout group'], [money(result.maximum_offer_cost_cents), 'Maximum offer cost'], [`${integer(result.window_days)} days`, 'Equal evaluation window']].forEach(([value, label]) => { const card = element('div', 'pilot-number'); card.append(element('strong', '', value), element('span', '', label)); $('pilot-numbers').append(card); });
    state.pilotResult = { ...result, dataset_id: state.data.source.dataset_id, ...(result.inputs ? { inputs: { ...result.inputs } } : {}) }; updateSaveAvailability();
    setText('pilot-method', result.method); setText('pilot-measure', result.success_metric); $('pilot-limitations').replaceChildren(); (result.limitations || []).forEach((item) => $('pilot-limitations').append(element('li', '', item))); $('pilot-result').hidden = false;
  }
  async function calculatePilot(event) { event.preventDefault(); if (!state.data || !state.draft) return; resetPilot(); const sequence = state.pilotSequence, dataSequence = state.sequence;
    try { const body = { segment: $('pilot-segment').value, max_customers: wholeNumber($('pilot-audience').value, 'Audience cap', 1, 1000), offer_cents: decimalCents($('pilot-offer').value, 'Offer', 5000), window_days: wholeNumber($('pilot-days').value, 'Evaluation days', 7, 90), budget_cents: decimalCents($('pilot-budget').value, 'Budget', 5000000), start: state.data.period.start, end: state.data.period.end }; state.pilotController = new AbortController(); $('pilot-submit').disabled = true; setText('pilot-status', 'Calculating the capped audience and maximum offer cost…'); const result = await fetchJson('/api/v1/owner/experiments/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: state.pilotController.signal }); if (sequence !== state.pilotSequence || dataSequence !== state.sequence) return; renderPilot(result, body); setText('pilot-status', ''); }
    catch (error) { if (sequence !== state.pilotSequence || dataSequence !== state.sequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('pilot-status', error.message || 'The pilot could not be calculated.'); }
    finally { if (sequence === state.pilotSequence) $('pilot-submit').disabled = false; }
  }
  $('compare-button').addEventListener('click', explainChange); $('pilot-form').addEventListener('submit', calculatePilot);
  ['pilot-segment', 'pilot-audience', 'pilot-offer', 'pilot-budget', 'pilot-days'].forEach((id) => { $(id).addEventListener('input', resetPilot); $(id).addEventListener('change', resetPilot); });
  ['start-date', 'end-date'].forEach((id) => { $(id).addEventListener('input', datesEdited); $(id).addEventListener('change', datesEdited); });

  const pilotInputKeys = ['segment', 'max_customers', 'offer_cents', 'window_days', 'budget_cents', 'start', 'end'];
  function samePilotInputs(a, b) { return !!a && !!b && pilotInputKeys.every((key) => a[key] === b[key]); }
  function currentPilotInputs() { return { segment: $('pilot-segment').value, max_customers: wholeNumber($('pilot-audience').value, 'Audience cap', 1, 1000), offer_cents: decimalCents($('pilot-offer').value, 'Offer', 5000), window_days: wholeNumber($('pilot-days').value, 'Evaluation days', 7, 90), budget_cents: decimalCents($('pilot-budget').value, 'Budget', 5000000), start: state.data?.period.start, end: state.data?.period.end }; }
  function hasReviewedPilot() { const plan = state.pilotResult; return !!(state.data && plan?.inputs && /^[a-f0-9]{64}$/.test(plan.reviewed_plan_hash || '') && plan.dataset_id === state.data.source.dataset_id && plan.source?.dataset_id === state.data.source.dataset_id && plan.source?.revision === state.data.source.revision && plan.source_revision === state.data.source.revision && samePeriod(plan.period, state.data.period)); }
  function updateSaveAvailability() {
    const ready = hasReviewedPilot(); $('save-pilot').disabled = savedState.canSave !== true || !ready || savedState.saveBusy;
    setText('save-pilot-help', savedState.canSave === false ? 'Saving is unavailable in this workspace. You can still copy a draft to keep it yourself.' : !ready ? 'Calculate the pilot before saving a reviewed draft. Recalculate after changing assumptions, dates, source, or view.' : 'Save the displayed calculation and your wording as a draft. Budget and audience warnings remain part of the saved record; nothing is launched.');
  }
  function resetSaveOperation() { savedState.saveSequence++; savedState.saveController?.abort(); savedState.saveBusy = false; setText('save-pilot-status', ''); updateSaveAvailability(); }
  function closeSavedPilot(restoreFocus = true) {
    const opener = savedState.opener, wasOpen = !$('saved-pilot-detail').hidden;
    savedState.detailSequence++; savedState.detailController?.abort(); savedState.noteSequence++; savedState.noteController?.abort(); savedState.noteBusy = false; savedState.current = null; savedState.opener = null;
    $('saved-pilot-detail').hidden = true; $('saved-pilot-content').hidden = true; setText('saved-pilot-title', ''); setText('saved-pilot-status', ''); setText('pilot-note-status', '');
    ['saved-pilot-hypothesis', 'saved-pilot-measure', 'saved-pilot-source', 'saved-pilot-binding', 'saved-pilot-created'].forEach((id) => setText(id, ''));
    ['saved-pilot-inputs', 'saved-pilot-calculation', 'saved-pilot-source-details', 'saved-pilot-limits', 'saved-pilot-notes'].forEach((id) => $(id).replaceChildren());
    $('pilot-note-form').reset(); ['pilot-note-date', 'pilot-note-text', 'pilot-note-submit'].forEach((id) => { $(id).disabled = false; });
    if (restoreFocus && wasOpen) (opener?.isConnected ? opener : $('refresh-saved-pilots')).focus();
  }
  function resetSavedRequests(clearList = false) {
    savedState.listSequence++; savedState.listController?.abort(); savedState.listBusy = false; $('refresh-saved-pilots').disabled = false; $('saved-pilots-more').disabled = false; closeSavedPilot(false); setText('saved-pilots-status', '');
    if (clearList) { savedState.canSave = null; savedState.items = []; savedState.nextOffset = null; savedState.total = 0; $('saved-pilots').replaceChildren(); $('saved-pilots-empty').hidden = true; $('saved-pilots-more').hidden = true; setText('saved-pilots-count', ''); updateSaveAvailability(); }
  }
  function savedBinding(record) {
    if (!state.data || record.source.dataset_id !== state.data.source.dataset_id) return { older: true, text: 'Different source from the current workspace. This draft retains its original business, records, and calculation.' };
    if (record.source.revision !== state.data.source.revision) return { older: true, text: 'An older or different revision of this source. The saved evidence has not been recalculated against the current records.' };
    if (!samePeriod(record.period, state.data.period)) return { older: true, text: 'A different observation period from the current workspace. This draft keeps the dates and evidence originally reviewed.' };
    return { older: false, text: 'Matches the current source revision and applied observation period. Saving does not launch a campaign.' };
  }
  function savedSourceLine(record) { return `${record.business.name} · ${record.source.label || 'Customer records'} · ${record.source.synthetic ? 'Synthetic/test records' : 'Owner-provided records'} · ${record.period.start} to ${record.period.end} (UTC)`; }
  function renderSavedList() {
    $('saved-pilots').replaceChildren();
    savedState.items.forEach((record) => {
      const row = element('li', 'saved-pilot-row'), text = element('div'); text.append(element('span', 'small-label', 'SAVED DRAFT · OWNER OBSERVATIONS'), element('h3', '', record.title), element('p', '', savedSourceLine(record)), element('p', '', `Saved ${record.created_at} · ${integer(record.note_count)} owner observation${record.note_count === 1 ? '' : 's'}`));
      const binding = savedBinding(record); if (binding.older) text.append(element('p', 'saved-warning', binding.text));
      if (record.calculation?.within_budget === false || record.calculation?.feasible_pilot === false) text.append(element('p', 'saved-warning', record.calculation.within_budget === false ? 'Saved with an over-budget warning. Review before considering any pilot.' : 'Saved with an insufficient-audience warning. This draft is not a feasible illustrative split.'));
      const open = element('button', 'secondary-button', 'Open saved draft'); open.type = 'button'; open.dataset.savedPilotId = record.id; open.setAttribute('aria-controls', 'saved-pilot-detail'); open.setAttribute('aria-label', `Open saved draft: ${record.title}`); open.addEventListener('click', () => openSavedPilot(record.id, open)); row.append(text, open); $('saved-pilots').append(row);
    });
    $('saved-pilots-empty').hidden = savedState.items.length > 0; $('saved-pilots-more').hidden = savedState.nextOffset === null; setText('saved-pilots-count', savedState.total ? `${integer(savedState.items.length)} of ${integer(savedState.total)} saved drafts · all sources` : '');
  }
  async function loadSavedPilots(offset = 0) {
    if (!state.data || state.view !== 'experiments') return;
    if (document.activeElement === $('saved-pilots-more')) $('saved-pilots-count').focus();
    savedState.listController?.abort(); const sequence = ++savedState.listSequence; savedState.listController = new AbortController(); const signal = savedState.listController.signal; savedState.listBusy = true; $('refresh-saved-pilots').disabled = true; $('saved-pilots-more').disabled = true; setText('saved-pilots-status', 'Reading saved drafts…');
    try {
      if (!offset || savedState.canSave === null) { const capability = await fetchJson('/api/v1/owner/status', { signal }); if (sequence !== savedState.listSequence) return; savedState.canSave = capability.can_save_pilots === true; updateSaveAvailability(); }
      if (!savedState.canSave) { savedState.items = []; savedState.nextOffset = null; savedState.total = 0; $('saved-pilots').replaceChildren(); $('saved-pilots-empty').hidden = true; $('saved-pilots-more').hidden = true; setText('saved-pilots-count', ''); setText('saved-pilots-status', 'Saved drafts are unavailable because this workspace has no pilot storage. Unsaved drafts can still be calculated and copied.'); return; }
      const result = await fetchJson(`/api/v1/owner/pilots?limit=20&offset=${offset}`, { signal }); if (sequence !== savedState.listSequence) return;
      const page = result.pagination; if (!Array.isArray(result.pilots) || !page || page.offset !== offset || page.limit !== 20 || !Number.isSafeInteger(page.total) || page.total < offset + result.pilots.length || result.pilots.some((record) => !record.id || !record.source || !record.business || !record.period) || (page.next_offset !== null && page.next_offset !== offset + result.pilots.length)) throw new Error('The saved draft list is incomplete. Refresh it to try again.');
      if (offset && (offset !== savedState.items.length || page.total !== savedState.total || result.pilots.some((record) => savedState.items.some((old) => old.id === record.id)))) throw new Error('The saved draft list changed while paging. Refresh it before continuing.');
      savedState.items = offset ? [...savedState.items, ...result.pilots] : result.pilots; savedState.total = page.total; savedState.nextOffset = page.next_offset; renderSavedList(); setText('saved-pilots-status', '');
    } catch (error) { if (sequence !== savedState.listSequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('saved-pilots-status', error.message || 'Saved drafts could not be loaded. Refresh to retry.'); }
    finally { if (sequence === savedState.listSequence) { savedState.listBusy = false; $('refresh-saved-pilots').disabled = false; $('saved-pilots-more').disabled = false; } }
  }
  function validateSavedRecord(record, expectedId) {
    if (!record || !/^[a-f0-9]{64}$/.test(record.id || '') || (expectedId && record.id !== expectedId) || record.status !== 'saved_draft' || !record.business?.name || !record.source?.dataset_id || !record.source?.revision || !record.period || !record.inputs || !record.calculation || !Array.isArray(record.notes) || !['title', 'hypothesis', 'measure'].every((key) => typeof record[key] === 'string')) throw new Error('This saved draft is incomplete. Refresh the list and open it again.');
    if (!samePeriod(record.period, record.calculation.period) || record.source.revision !== record.calculation.source_revision || record.inputs.start !== record.period.start || record.inputs.end !== record.period.end) throw new Error('The saved calculation does not match its source record. Refresh the list before continuing.');
    record.notes.forEach((note) => { if (!note.id || note.pilot_id !== record.id || note.verification !== 'owner_reported' || typeof note.text !== 'string' || typeof note.observed_on !== 'string') throw new Error('A saved observation is incomplete. Open the draft again before adding a note.'); });
  }
  function renderSavedNotes() {
    const record = savedState.current; $('saved-pilot-notes').replaceChildren();
    record.notes.forEach((note) => { const row = element('li'); row.dataset.noteId = note.id; row.append(element('strong', '', `${note.observed_on} · Owner-reported observation`), element('p', '', note.text), element('small', '', `Recorded ${note.created_at} · Not independently verified`)); $('saved-pilot-notes').append(row); }); $('saved-notes-empty').hidden = record.notes.length > 0;
  }
  function renderSavedDetail(record) {
    validateSavedRecord(record); savedState.current = record; setText('saved-pilot-title', record.title); const binding = savedBinding(record); setText('saved-pilot-binding', binding.text); $('saved-pilot-binding').classList.toggle('older', binding.older); setText('saved-pilot-source', savedSourceLine(record)); setText('saved-pilot-created', `Saved ${record.created_at} · Draft record · Regulars has not assigned or contacted customers`); setText('saved-pilot-hypothesis', record.hypothesis); setText('saved-pilot-measure', record.measure);
    $('saved-pilot-inputs').replaceChildren(); const inputRows = [['Customer group', labels[record.inputs.segment] || record.inputs.segment], ['Audience cap', integer(record.inputs.max_customers)], ['Offer per customer', money(record.inputs.offer_cents)], ['Offer budget', record.inputs.budget_cents === null ? 'Not specified' : money(record.inputs.budget_cents)], ['Evaluation window', `${integer(record.inputs.window_days)} days`]]; inputRows.forEach(([label, value]) => $('saved-pilot-inputs').append(element('dt', '', label), element('dd', '', value)));
    const calculation = element('div', 'evidence-primary'); evidencePlan(calculation, record.calculation); $('saved-pilot-calculation').replaceChildren(calculation); $('saved-pilot-source-details').replaceChildren(); [['Dataset', record.source.dataset_id], ['Source revision', record.source.revision], ['Observed coverage', `${record.source.coverage_start} to ${record.source.coverage_end}`], ['Saved review', record.reviewed_plan_hash]].forEach(([label, value]) => $('saved-pilot-source-details').append(element('dt', '', label), element('dd', '', value)));
    $('saved-pilot-limits').replaceChildren(); [...new Set([...(record.source.limitations || []), ...(record.calculation.limitations || [])])].forEach((note) => $('saved-pilot-limits').append(element('li', '', note))); renderSavedNotes(); $('saved-pilot-content').hidden = false; $('saved-pilot-detail').hidden = false; setText('saved-pilot-status', ''); $('pilot-note-submit').disabled = savedState.canSave !== true;
  }
  async function openSavedPilot(id, opener) {
    closeSavedPilot(false); savedState.opener = opener; const sequence = savedState.detailSequence; savedState.detailController = new AbortController(); $('saved-pilot-detail').hidden = false; setText('saved-pilot-title', 'Opening saved draft…'); setText('saved-pilot-status', 'Reading the original source and calculation.'); $('saved-pilot-title').focus();
    try { const record = await fetchJson(`/api/v1/owner/pilots/${encodeURIComponent(id)}`, { signal: savedState.detailController.signal }); if (sequence !== savedState.detailSequence) return; validateSavedRecord(record, id); renderSavedDetail(record); }
    catch (error) { if (sequence !== savedState.detailSequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else { setText('saved-pilot-title', 'Saved draft unavailable'); setText('saved-pilot-status', error.message || 'Could not open this saved draft. Refresh the list and try again.'); } }
  }
  function boundedDraftText(id, label, maximum) { const value = $(id).value.trim(); if (!value || value.length > maximum) { $(id).focus(); throw new Error(`${label} must contain 1 to ${maximum} characters.`); } return value; }
  async function saveReviewedPilot() {
    if (savedState.saveBusy || savedState.canSave !== true) return;
    if (!hasReviewedPilot()) { updateSaveAvailability(); setText('save-pilot-status', 'Calculate and review the current pilot before saving.'); return; }
    const sequence = savedState.saveSequence, dataSequence = state.sequence, pilot = state.pilotResult;
    try {
      if (!samePilotInputs(pilot.inputs, currentPilotInputs())) { resetPilot(); setText('save-pilot-status', 'The assumptions changed. Calculate again before saving this draft.'); return; }
      const body = { expected_dataset_id: pilot.dataset_id, expected_revision: pilot.source_revision, reviewed_plan_hash: pilot.reviewed_plan_hash, title: boundedDraftText('draft-name', 'Draft title', 120), hypothesis: boundedDraftText('draft-hypothesis', 'What you will try', 2000), measure: boundedDraftText('draft-measure', 'How you will measure it', 2000), inputs: { ...pilot.inputs } };
      savedState.saveBusy = true; savedState.saveController = new AbortController(); updateSaveAvailability(); setText('save-pilot-status', 'Saving the reviewed draft and its original evidence…');
      const record = await fetchJson('/api/v1/owner/pilots', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: savedState.saveController.signal });
      if (sequence !== savedState.saveSequence || dataSequence !== state.sequence) return;
      validateSavedRecord(record); if (record.source.dataset_id !== body.expected_dataset_id || record.source.revision !== body.expected_revision || record.reviewed_plan_hash !== body.reviewed_plan_hash || !samePilotInputs(record.inputs, body.inputs) || ['title', 'hypothesis', 'measure'].some((key) => record[key] !== body[key])) throw new Error('The saved response did not match the reviewed draft. Refresh saved drafts to inspect the stored record.');
      closeSavedPilot(false); savedState.opener = $('save-pilot'); renderSavedDetail(record); $('saved-pilot-title').focus(); setText('save-pilot-status', 'Saved as a draft with its original evidence. Regulars has not assigned or contacted customers. Saving does not launch a campaign.'); loadSavedPilots();
    } catch (error) { if (sequence !== savedState.saveSequence || dataSequence !== state.sequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('save-pilot-status', `${error.message || 'Save could not be confirmed.'}${savedState.saveBusy ? ' Refresh saved drafts before retrying if the connection was interrupted; identical reviewed content returns the same record.' : ''}`); }
    finally { if (sequence === savedState.saveSequence) { savedState.saveBusy = false; updateSaveAvailability(); } }
  }
  async function addPilotNote(event) {
    event.preventDefault(); if (!savedState.current || savedState.noteBusy || savedState.canSave !== true) return;
    const record = savedState.current, detailSequence = savedState.detailSequence, sequence = ++savedState.noteSequence;
    try {
      const observed_on = $('pilot-note-date').value, text = boundedDraftText('pilot-note-text', 'Observation', 2000); if (!/^\d{4}-\d{2}-\d{2}$/.test(observed_on) || !Number.isFinite(Date.parse(`${observed_on}T00:00:00Z`)) || new Date(`${observed_on}T00:00:00Z`).toISOString().slice(0, 10) !== observed_on) throw new Error('Choose a valid date for your observation.');
      savedState.noteBusy = true; savedState.noteController = new AbortController(); ['pilot-note-date', 'pilot-note-text', 'pilot-note-submit'].forEach((id) => { $(id).disabled = true; }); setText('pilot-note-status', 'Saving your dated observation…');
      const note = await fetchJson(`/api/v1/owner/pilots/${encodeURIComponent(record.id)}/notes`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ observed_on, text }), signal: savedState.noteController.signal });
      if (sequence !== savedState.noteSequence || detailSequence !== savedState.detailSequence) return;
      if (!note.id || note.pilot_id !== record.id || note.verification !== 'owner_reported' || note.observed_on !== observed_on || note.text !== text) throw new Error('The saved observation did not match your note. Open the draft again to verify it.');
      if (!record.notes.some((existing) => existing.id === note.id)) record.notes.push(note); renderSavedNotes(); $('pilot-note-text').value = ''; const listed = savedState.items.find((item) => item.id === record.id); if (listed) listed.note_count = record.notes.length; renderSavedList(); setText('pilot-note-status', 'Owner observation saved.'); $('pilot-note-text').disabled = false; $('pilot-note-text').focus();
    } catch (error) { if (sequence !== savedState.noteSequence || detailSequence !== savedState.detailSequence || error.name === 'AbortError') return; if (error.auth) unlockRequired(); else setText('pilot-note-status', `${error.message || 'The observation could not be saved.'}${savedState.noteBusy ? ' Open the saved draft again before retrying if the connection was interrupted.' : ''}`); }
    finally { if (sequence === savedState.noteSequence) { savedState.noteBusy = false; ['pilot-note-date', 'pilot-note-text', 'pilot-note-submit'].forEach((id) => { $(id).disabled = false; }); } }
  }
  $('save-pilot').addEventListener('click', saveReviewedPilot); $('refresh-saved-pilots').addEventListener('click', () => loadSavedPilots()); $('saved-pilots-more').addEventListener('click', () => { if (savedState.nextOffset !== null) loadSavedPilots(savedState.nextOffset); }); $('close-saved-pilot').addEventListener('click', () => closeSavedPilot()); $('pilot-note-form').addEventListener('submit', addPilotNote);
  ['draft-name', 'draft-hypothesis', 'draft-measure'].forEach((id) => $(id).addEventListener('input', resetSaveOperation));

  const importState = { sequence: 0, preview: null, reviewed: null, busy: false, canImport: false };
  const importStatus = (message) => setText('data-source-status', message);
  function invalidatePreview() { importState.sequence++; importState.preview = null; importState.reviewed = null; $('import-preview').hidden = true; $('import-summary').replaceChildren(); $('import-warnings').replaceChildren(); }
  function setImportBusy(busy) { importState.busy = busy; $('data-drawer').setAttribute('aria-busy', String(busy)); ['import-business', 'import-file', 'import-csv', 'import-synthetic', 'preview-import', 'commit-import', 'select-dataset', 'dataset-select'].forEach((id) => { $(id).disabled = busy || !importState.canImport; }); }
  function closeDataDrawer() { if (importState.busy) return; $('data-drawer').hidden = true; $('data-drawer-backdrop').hidden = true; $('main').removeAttribute('inert'); document.querySelector('.sidebar').removeAttribute('inert'); $('data-source-button').focus(); }
  async function refreshDatasets() { const result = await fetchJson('/api/v1/owner/datasets'); $('dataset-select').replaceChildren(); result.datasets.forEach((dataset) => { const option = element('option', '', `${dataset.business_name} · ${dataset.synthetic ? 'Synthetic demo' : 'Imported data'}`); option.value = dataset.id; $('dataset-select').append(option); }); $('dataset-select').value = result.active_id; }
  async function openDataDrawer() {
    resetAnalysis(); resetAnswer();
    $('data-drawer').hidden = false; $('data-drawer-backdrop').hidden = false; $('main').setAttribute('inert', ''); document.querySelector('.sidebar').setAttribute('inert', ''); $('close-data-drawer').focus(); importStatus('Checking available datasets…');
    try { const capability = await fetchJson('/api/v1/owner/status'); importState.canImport = capability.can_import === true; setImportBusy(false); await refreshDatasets(); if (!$('data-drawer').hidden) importStatus(importState.canImport ? '' : 'This workspace is read-only. Dataset import and switching are unavailable.'); }
    catch (error) { if (error.auth) { closeDataDrawer(); unlockRequired(); } else importStatus(error.message || 'Could not load datasets.'); }
  }
  function csvBody() { const csv = $('import-csv').value, business_name = $('import-business').value.trim(); if (!business_name) throw new Error('Enter a business name before previewing.'); if (!csv.trim()) throw new Error('Choose a CSV file or paste its contents.'); if (new Blob([csv]).size > 750000) throw new Error('The CSV exceeds the 750 KB limit.'); return { csv, business_name, synthetic: $('import-synthetic').checked }; }
  function renderImportPreview(preview) { if (!/^[a-f0-9]{64}$/.test(preview.preview_hash || '')) throw new Error('The import preview is incomplete. Please try again.'); $('import-summary').replaceChildren(); const rows = { Business: preview.business_name || importState.reviewed.business_name, 'Order rows': integer(preview.orders), 'Completed purchases': integer(preview.completed_orders), 'Excluded orders (pending / cancelled)': `${integer(preview.excluded_orders)} (${integer(preview.pending_orders)} / ${integer(preview.cancelled_orders)})`, 'Refund records': integer(preview.refunds), 'Identified buyers': integer(preview.customers), 'Gross purchases': money(preview.gross_revenue_cents), 'Refunds deducted': `−${money(preview.refund_cents)}`, 'Net revenue': money(preview.net_revenue_cents), 'First record': preview.coverage_start, 'Last record': preview.coverage_end, 'Source label': preview.synthetic ? 'Synthetic or test data' : 'Owner-provided data' }; Object.entries(rows).forEach(([label, value]) => { $('import-summary').append(element('dt', '', label), element('dd', '', value)); }); $('import-warnings').replaceChildren(); (preview.warnings || []).forEach((warning) => $('import-warnings').append(element('li', '', warning))); $('import-preview').hidden = false; }
  async function previewImport(event) { event.preventDefault(); if (importState.busy || !importState.canImport) return; invalidatePreview(); const sequence = importState.sequence; try { const body = csvBody(); setImportBusy(true); importStatus('Validating the complete CSV…'); const preview = await fetchJson('/api/v1/owner/import/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); if (sequence !== importState.sequence) return; importState.preview = preview; importState.reviewed = body; renderImportPreview(preview); importStatus('Preview ready. Review the counts, dates, and limitations before importing.'); }
    catch (error) { if (sequence !== importState.sequence) return; if (error.auth) { setImportBusy(false); closeDataDrawer(); unlockRequired(); } else importStatus(error.message || 'The CSV could not be validated.'); } finally { setImportBusy(false); } }
  async function commitImport() { if (importState.busy || !importState.canImport || !importState.preview || !importState.reviewed) return; try { const current = csvBody(); if (JSON.stringify(current) !== JSON.stringify(importState.reviewed)) { invalidatePreview(); throw new Error('The input changed. Preview it again before importing.'); } setImportBusy(true); importStatus('Saving the reviewed dataset…'); await fetchJson('/api/v1/owner/import/commit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...importState.reviewed, preview_hash: importState.preview.preview_hash }) }); invalidatePreview(); $('import-csv').value = ''; $('import-file').value = ''; $('start-date').value = ''; $('end-date').value = ''; $('customer-search').value = ''; clearData(); await refreshDatasets(); await load(); importStatus('Imported successfully. The workspace now shows your separate reviewed dataset.'); }
    catch (error) { if (error.auth) { setImportBusy(false); closeDataDrawer(); unlockRequired(); } else importStatus(`${error.message || 'Import could not be completed.'} Check the dataset list before retrying if the connection was interrupted.`); } finally { setImportBusy(false); } }
  async function selectDataset() { if (importState.busy || !importState.canImport) return; try { setImportBusy(true); importStatus('Switching datasets…'); await fetchJson('/api/v1/owner/datasets/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ dataset_id: $('dataset-select').value }) }); invalidatePreview(); $('start-date').value = ''; $('end-date').value = ''; $('customer-search').value = ''; clearData(); await load(); importStatus('Dataset switched. Dates now match its observed history.'); }
    catch (error) { if (error.auth) { setImportBusy(false); closeDataDrawer(); unlockRequired(); } else importStatus(error.message || 'Dataset could not be switched.'); } finally { setImportBusy(false); } }
  $('data-source-button').addEventListener('click', openDataDrawer); $('close-data-drawer').addEventListener('click', closeDataDrawer); $('data-drawer-backdrop').addEventListener('click', closeDataDrawer);
  $('import-form').addEventListener('submit', previewImport); $('commit-import').addEventListener('click', commitImport); $('select-dataset').addEventListener('click', selectDataset);
  ['import-business', 'import-csv'].forEach((id) => $(id).addEventListener('input', () => { invalidatePreview(); importStatus('Input changed. Preview again before saving.'); }));
  $('import-synthetic').addEventListener('change', () => { invalidatePreview(); importStatus('Source label changed. Preview again before saving.'); });
  $('import-file').addEventListener('change', async () => { invalidatePreview(); const sequence = importState.sequence; const file = $('import-file').files[0]; if (!file) return; if (file.size > 750000) { importStatus('The file exceeds the 750 KB limit. Choose a smaller sales export.'); return; } try { const text = typeof file.text === 'function' ? await file.text() : await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error('File could not be read.')); reader.readAsText(file, 'UTF-8'); }); if (sequence !== importState.sequence) return; $('import-csv').value = text; importStatus('File loaded into the preview form. Review the business name, then preview the import.'); } catch { importStatus('This file could not be read as text. Try a UTF-8 CSV export.'); } });
  $('download-template').addEventListener('click', async () => { try { const response = await fetch('/api/v1/owner/import/template.csv', options()); if (response.status === 401) { closeDataDrawer(); unlockRequired(); return; } if (!response.ok) throw new Error('Template download failed.'); const url = URL.createObjectURL(await response.blob()); const link = element('a'); link.href = url; link.download = 'regulars-import-template.csv'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); importStatus('CSV template downloaded. Replace the examples with your own pseudonymous records.'); } catch (error) { importStatus(error.message || 'Template download failed.'); } });
  $('data-drawer').addEventListener('keydown', (event) => { if (event.key === 'Escape') { event.preventDefault(); closeDataDrawer(); } if (event.key === 'Tab') { const focusable = [...$('data-drawer').querySelectorAll('button, input, select, textarea, [tabindex]')].filter((node) => !node.disabled && node.tabIndex >= 0 && !node.closest('[hidden]')); const first = focusable[0], last = focusable.at(-1); if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } } });

  document.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
  document.querySelectorAll('[data-question]').forEach((button) => button.addEventListener('click', () => { $('question').value = button.dataset.question; $('question').focus(); }));
  $('view-experiments').addEventListener('click', () => switchView('experiments'));
  $('manual-pilot').addEventListener('click', () => {
    if (!state.data || state.data.period.end > state.data.source.coverage_end) return;
    const group = state.data.segments.find((segment) => segment.id === 'promising' && segment.customers > 0) || state.data.segments.find((segment) => segment.customers > 0);
    if (!group) return;
    chooseDraft({ id: 'manual', title: 'A pilot you define', reason: 'Choose an observed customer group, a clear hypothesis, and an equal evaluation window. Review eligibility and consent before any assignment.', segment: group.id, customers: group.customers, metric: `${group.customers} identified customers`, suggestion: 'Write one specific change you want to test and why it might help this group.' });
  });
  $('refresh').addEventListener('click', load);
  $('date-form').addEventListener('submit', (event) => { event.preventDefault(); load(); });
  $('customer-search').addEventListener('input', renderCustomers); $('segment-filter').addEventListener('change', renderCustomers);
  $('access-form').addEventListener('submit', (event) => { event.preventDefault(); state.token = $('access-token').value.trim(); $('access-token').value = ''; load(); });
  $('lock-button').addEventListener('click', () => { state.controller?.abort(); state.sequence++; state.token = ''; clearData(); $('lock-button').hidden = true; $('access-panel').hidden = false; status('Workspace locked. Customer data has been cleared from view.'); });
  $('ask-form').addEventListener('submit', ask); $('export-customers').addEventListener('click', exportCustomers);
  function pilotCopyText() {
    const pilot = state.pilotResult;
    if (!pilot) return 'Pilot not calculated. No current audience split, offer-cost estimate, or feasibility check is attached. Calculate again after changing assumptions, dates, dataset, or view.';
    const segmentLabel = state.data.segments.find((segment) => segment.id === pilot.segment)?.label || labels[pilot.segment] || pilot.segment;
    const feasibility = !pilot.within_budget ? 'Exceeds the entered offer budget. Revise assumptions before considering a pilot.' : !pilot.feasible_pilot ? 'Not a feasible illustrative split; fewer than four observed customers may be available.' : 'Fits the entered offer budget and minimum illustrative audience. This does not establish statistical power, effectiveness, or complete operating cost.';
    return [
      'Calculated pilot assumptions:',
      `Customer group: ${segmentLabel} (${pilot.segment})`,
      `Proposed audience: ${integer(pilot.treatment_customers + pilot.holdout_customers)} customers; offer group ${integer(pilot.treatment_customers)}, holdout group ${integer(pilot.holdout_customers)}`,
      `Offer per customer: ${money(pilot.offer_cents)} USD`,
      `Offer budget: ${pilot.budget_cents === null ? 'Not specified' : `${money(pilot.budget_cents)} USD`}`,
      `Maximum offer cost: ${money(pilot.maximum_offer_cost_cents)} USD`,
      `Evaluation window: ${integer(pilot.window_days)} days for each group`,
      `Observed source period: ${pilot.period.start} through ${pilot.period.end} (UTC)`,
      `Source dataset: ${pilot.dataset_id}`,
      `Source revision: ${pilot.source_revision}`,
      `Feasibility: ${feasibility}`,
      `Method: ${pilot.method}`,
      `Success metric: ${pilot.success_metric}`,
      ...(pilot.limitations || []).map((limitation) => `Limitation: ${limitation}`),
    ].join('\n');
  }
  $('copy-draft').addEventListener('click', async () => {
    if (!state.draft) return;
    const text = `${$('draft-name').value || state.draft.title}\n\nObservation: ${state.draft.reason}\n\nExperiment: ${$('draft-hypothesis').value}\n\nMeasurement: ${$('draft-measure').value}\n\n${pilotCopyText()}\n\nDraft only. No customer has been assigned or contacted, and no campaign has been launched.`;
    try { await navigator.clipboard.writeText(text); setText('draft-status', 'Draft copied with its current calculation state. Nothing has been sent to customers.'); }
    catch { setText('draft-status', 'Copy is unavailable in this browser. Select the draft text above to copy it manually.'); }
  });
  load();
})();
