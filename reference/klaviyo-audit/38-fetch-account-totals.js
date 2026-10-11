// Read-only account totals: the numbers the audit memo is built on, pulled from
// Klaviyo's aggregate endpoints rather than by paging through profiles.
//
// Usage:
//   ACCOUNT_SLUG=client-2026-10-02 node 38-fetch-account-totals.js
//   (needs output/{ACCOUNT_SLUG}/account-audit-api.json from step 24)
//
// Writes (re-written after every section, so a partial run still keeps what it got):
//   output/{ACCOUNT_SLUG}/account-totals.json       aggregates, campaigns, reports, counts
//   output/{ACCOUNT_SLUG}/placed-orders-24mo.json   one compact row per order (profile id, no PII)
//
// Sections, cheapest and most important first:
//   1. account + every metric
//   2. monthly metric aggregates: revenue (24 months, total and by attributed channel / flow),
//      refunds, site funnel, email + SMS engagement and deliverability, subscriber growth, forms
//   3. every email and SMS campaign with its messages (subject, preview, sender, send times)
//   4. Placed Order events for 24 months (repeat purchase, time to second order)
//   5. reporting API: form performance (365 days), weekly flow performance (365 days)
//   6. coupons, catalog items
//   7. exact profile counts for every list and segment (slow: Klaviyo allows 15 a minute)

const fs = require('fs');
const path = require('path');
const { pickMetric, describeMetricChoice } = require('./metric-selection');
const { extractOrderItemNames } = require('./order-items');
const { aggregateWindowUtc } = require('./aggregate-boundaries');

const API_BASE = process.env.KLAVIYO_API_BASE || 'https://a.klaviyo.com/api'; // override only for local mock tests
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
const AUDIT_PATH = path.join(OUT_DIR, 'account-audit-api.json');
const OUT_FILE = path.join(OUT_DIR, 'account-totals.json');
const ORDERS_FILE = path.join(OUT_DIR, 'placed-orders-24mo.json');
const MAX_EVENT_PAGES = Number(process.env.MAX_EVENT_PAGES || 1000);
const MAX_CAMPAIGN_PAGES = Number(process.env.MAX_CAMPAIGN_PAGES || 100);

const API_KEY = (process.env.KLAVIYO_PRIVATE_API_KEY || '').trim();

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function request(pathOrUrl, { method = 'GET', params = {}, body = null, label = pathOrUrl, revision = REVISION, pace = 150 } = {}) {
  const url = new URL(pathOrUrl.startsWith('http') ? pathOrUrl : `${API_BASE}${pathOrUrl}`);
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== '') url.searchParams.set(key, value);
  }
  let last = null;
  for (let attempt = 1; attempt <= 7; attempt += 1) {
    await sleep(pace);
    let response;
    try {
      response = await fetch(url, {
        method,
        headers: {
          Authorization: `Klaviyo-API-Key ${API_KEY}`,
          accept: 'application/vnd.api+json',
          'content-type': 'application/vnd.api+json',
          revision,
        },
        body: body ? JSON.stringify(body) : undefined,
      });
    } catch (error) {
      last = { label, status: 'network', message: error.message };
      await sleep(1500 * attempt);
      continue;
    }
    const text = await response.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch (_) { json = { rawText: text.slice(0, 800) }; }
    if (response.ok) return json;
    last = { label, status: response.status, error: json?.errors || json };
    if (![408, 429, 500, 502, 503, 504].includes(response.status)) break;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(retryAfter ? retryAfter * 1000 + 250 : 2000 * attempt);
  }
  const err = new Error(`${label} failed (${last?.status})`);
  err.details = last;
  throw err;
}

async function safe(label, fn) {
  try {
    return { ok: true, value: await fn() };
  } catch (error) {
    const failure = { ok: false, label, error: error.details || { message: error.message } };
    out.errors.push(failure);
    console.log(`    ! ${label}: ${error.message}`);
    return failure;
  }
}

async function getAll(pathname, params, label, maxPages, options = {}) {
  const rows = [];
  const included = [];
  let next = pathname;
  let pages = 0;
  let first = true;
  while (next && pages < maxPages) {
    const page = await request(next, { params: first ? params : {}, label: `${label} p${pages + 1}`, ...options });
    first = false;
    pages += 1;
    if (Array.isArray(page?.data)) rows.push(...page.data);
    if (Array.isArray(page?.included)) included.push(...page.included);
    next = page?.links?.next || null;
  }
  return { rows, included, pages, truncated: Boolean(next) };
}

// ---- dates -----------------------------------------------------------------

function monthStart(offsetMonths) {
  const now = new Date();
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offsetMonths, 1));
  return d.toISOString().slice(0, 10);
}

// This is an actual instant. Keep Z so aggregate conversion cannot treat it as local wall time.
const NOW_UTC = new Date().toISOString();
const WINDOWS = {
  // 12 monthly buckets: 11 full months + the current month to date (within the 1-year cap)
  current: { start: `${monthStart(-11)}T00:00:00`, end: NOW_UTC },
  // the 12 full months before that, for year-over-year
  prior: { start: `${monthStart(-23)}T00:00:00`, end: `${monthStart(-11)}T00:00:00` },
};

// ---- metric lookup ----------------------------------------------------------

// Shared selection expands SMS/Text Message aliases, prefers canonical Shopify
// events, and records competing candidates instead of silently taking the first.
const AGGREGATES = [
  // revenue and orders
  { key: 'placed_order', names: ['Placed Order'], prefer: 'ecommerce', measurements: ['count', 'sum_value', 'unique'], windows: ['current', 'prior'] },
  { key: 'placed_order_by_channel', names: ['Placed Order'], prefer: 'ecommerce', measurements: ['count', 'sum_value'], by: ['$attributed_channel'], windows: ['current', 'prior'] },
  { key: 'placed_order_by_flow', names: ['Placed Order'], prefer: 'ecommerce', measurements: ['count', 'sum_value'], by: ['$attributed_flow'], windows: ['current'] },
  { key: 'refunded_order', names: ['Refunded Order'], prefer: 'ecommerce', measurements: ['count', 'sum_value'], windows: ['current', 'prior'] },
  { key: 'cancelled_order', names: ['Cancelled Order'], prefer: 'ecommerce', measurements: ['count', 'sum_value'], windows: ['current'] },
  // site funnel
  { key: 'active_on_site', names: ['Active on Site'], measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'viewed_product', names: ['Viewed Product'], measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'added_to_cart', names: ['Added to Cart', 'Add to Cart'], prefer: 'ecommerce', measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'started_checkout', names: ['Started Checkout', 'Checkout Started'], prefer: 'ecommerce', measurements: ['count', 'unique', 'sum_value'], windows: ['current'] },
  // email engagement and deliverability
  { key: 'received_email', names: ['Received Email'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current', 'prior'] },
  { key: 'received_email_by_inbox', names: ['Received Email'], prefer: 'klaviyo', measurements: ['count'], by: ['Inbox Provider'], windows: ['current'] },
  { key: 'opened_email', names: ['Opened Email'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current', 'prior'] },
  { key: 'opened_email_by_inbox', names: ['Opened Email'], prefer: 'klaviyo', measurements: ['unique'], by: ['Inbox Provider'], windows: ['current'] },
  { key: 'clicked_email', names: ['Clicked Email'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current', 'prior'] },
  { key: 'bounced_email', names: ['Bounced Email'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'bounced_email_by_type', names: ['Bounced Email'], prefer: 'klaviyo', measurements: ['count'], by: ['Bounce Type'], windows: ['current'] },
  { key: 'dropped_email', names: ['Dropped Email'], prefer: 'klaviyo', measurements: ['count'], windows: ['current'] },
  { key: 'marked_spam', names: ['Marked Email as Spam'], prefer: 'klaviyo', measurements: ['count'], windows: ['current'] },
  { key: 'unsubscribed_email', names: ['Unsubscribed from Email Marketing', 'Unsubscribed'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current'] },
  // SMS
  { key: 'received_sms', names: ['Received SMS'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'clicked_sms', names: ['Clicked SMS'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current'] },
  { key: 'failed_sms', names: ['Failed to Deliver SMS', 'Failed to deliver SMS'], prefer: 'klaviyo', measurements: ['count'], windows: ['current'] },
  { key: 'unsubscribed_sms', names: ['Unsubscribed from SMS Marketing', 'Unsubscribed from SMS'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current'] },
  // subscriber growth and forms
  { key: 'subscribed_to_list', names: ['Subscribed to List'], prefer: 'klaviyo', measurements: ['count', 'unique'], by: ['List'], windows: ['current'] },
  { key: 'subscribed_email', names: ['Subscribed to Email Marketing'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current', 'prior'] },
  { key: 'subscribed_sms', names: ['Subscribed to SMS Marketing'], prefer: 'klaviyo', measurements: ['count', 'unique'], windows: ['current', 'prior'] },
  { key: 'form_submits', names: ['Form submitted by profile', 'Submitted Form'], prefer: 'klaviyo', measurements: ['count', 'unique'], by: ['form_id'], windows: ['current'] },
];

async function aggregate(metricId, { measurements, by, window }, timezone) {
  const w = WINDOWS[window];
  const utcWindow = aggregateWindowUtc(w, timezone);
  const attributes = {
    metric_id: metricId,
    measurements,
    interval: 'month',
    page_size: 500,
    timezone,
    filter: [`greater-or-equal(datetime,${utcWindow.start})`, `less-than(datetime,${utcWindow.end})`],
  };
  if (by) attributes.by = by;
  const res = await request('/metric-aggregates', {
    method: 'POST',
    body: { data: { type: 'metric-aggregate', attributes } },
    label: `aggregate ${metricId} ${window}${by ? ` by ${by}` : ''}`,
    pace: 400, // 3/s burst, 60/m steady
  });
  return { window: w, utcWindow, dates: res?.data?.attributes?.dates || [], rows: res?.data?.attributes?.data || [], links: res?.links || null };
}

// ---- reporting API (1/s burst, 2/m steady) -----------------------------------

let lastReportAt = 0;
async function report(type, attributes, label) {
  const wait = lastReportAt + 31_000 - Date.now();
  if (wait > 0) {
    console.log(`    waiting ${Math.ceil(wait / 1000)}s for the reporting rate limit...`);
    await sleep(wait);
  }
  lastReportAt = Date.now();
  return request(`/${type}s`, { method: 'POST', body: { data: { type, attributes } }, label, pace: 0 });
}

// ---- output -------------------------------------------------------------------

const out = {
  capturedAt: new Date().toISOString(),
  revision: REVISION,
  accountSlug: ACCOUNT_SLUG,
  windows: WINDOWS,
  account: null,
  metrics: null,
  metricChoices: {},
  aggregates: {},
  campaigns: {},
  orders: null,
  reports: {},
  coupons: null,
  catalog: null,
  groupCounts: { lists: [], segments: [] },
  errors: [],
};

function save() {
  fs.writeFileSync(OUT_FILE, JSON.stringify(out, null, 2));
}

function compact(r) {
  return r ? { type: r.type, id: r.id, attributes: r.attributes || {}, relationships: r.relationships || {} } : null;
}

// ---- main -------------------------------------------------------------------------

(async () => {
  if (!API_KEY) throw new Error('KLAVIYO_PRIVATE_API_KEY is required');
  await require('./source-contract').bindAccount(API_KEY);
  if (!fs.existsSync(AUDIT_PATH)) throw new Error(`Missing ${AUDIT_PATH}. Run step 24 first.`);
  const audit = JSON.parse(fs.readFileSync(AUDIT_PATH, 'utf8'));

  // 1. account + metrics
  console.log('[1] Account and metrics...');
  const account = await safe('accounts', () => request('/accounts', { label: 'accounts' }));
  const acct = account.ok ? account.value?.data?.[0]?.attributes || {} : {};
  if (require('./source-contract').loadContract() && !acct.timezone) throw new Error('Account timezone unavailable; refusing to fall back to UTC for a contracted account');
  const timezone = acct.timezone || 'UTC';
  out.account = { timezone, currency: acct.preferred_currency || null, contact: acct.contact_information || null };
  const metricsRes = await safe('metrics', () => getAll('/metrics', { 'fields[metric]': 'name,integration,created,updated' }, 'metrics', 50));
  const metrics = metricsRes.ok
    ? metricsRes.value.rows.map((m) => ({ id: m.id, name: m.attributes?.name, integration: m.attributes?.integration || null, created: m.attributes?.created, updated: m.attributes?.updated }))
    : [];
  out.metrics = metrics;
  console.log(`    ${metrics.length} metrics; timezone ${timezone}.`);
  save();

  // 2. metric aggregates
  console.log('[2] Monthly metric aggregates...');
  const subscriptionMetrics = metrics.filter((m) => /subscription/i.test(m.name || '') && !/klaviyo/i.test(m.integration?.name || '')).slice(0, 8);
  const specs = [
    ...AGGREGATES,
    ...subscriptionMetrics.map((m) => ({ key: `subscription_${m.id}`, names: [m.name], fixedId: m.id, measurements: ['count', 'unique'], windows: ['current'] })),
  ];
  for (const spec of specs) {
    const choice = spec.fixedId
      ? { metric: metrics.find((m) => m.id === spec.fixedId), candidates: metrics.filter((m) => m.id === spec.fixedId), status: 'selected', reason: 'Explicit metric ID' }
      : pickMetric(metrics, spec.names, spec.prefer);
    const { metric } = choice;
    out.metricChoices[spec.key] = describeMetricChoice(choice);
    if (!metric) {
      out.aggregates[spec.key] = choice.status === 'ambiguous'
        ? { ambiguous: true, error: choice.reason }
        : { notFound: spec.names };
      console.log(`    - ${spec.key}: ${choice.reason}`);
      save();
      continue;
    }
    out.aggregates[spec.key] = { metricId: metric.id, measurements: spec.measurements, by: spec.by || null };
    for (const window of spec.windows) {
      const res = await safe(`${spec.key} ${window}`, () => aggregate(metric.id, { ...spec, window }, timezone));
      out.aggregates[spec.key][window] = res.ok ? res.value : { error: res.error };
    }
    console.log(`    ${spec.key}`);
    save();
  }

  // 3. campaigns with messages
  console.log('[3] Campaigns with messages...');
  for (const channel of ['email', 'sms']) {
    const res = await safe(`campaigns ${channel}`, () => getAll('/campaigns', {
      filter: `equals(messages.channel,'${channel}')`,
      'page[size]': 100,
      sort: '-created_at',
      include: 'campaign-messages',
    }, `campaigns ${channel}`, MAX_CAMPAIGN_PAGES, { pace: 400 }));
    out.campaigns[channel] = res.ok
      ? {
        count: res.value.rows.length,
        pages: res.value.pages,
        truncated: res.value.truncated,
        items: res.value.rows.map(compact),
        messages: res.value.included.filter((r) => r.type === 'campaign-message').map(compact),
      }
      : { error: res.error };
    console.log(`    ${channel}: ${res.ok ? res.value.rows.length : 'failed'}`);
    save();
  }

  // 4. Placed Order events, 24 months
  console.log('[4] Placed Order events (24 months)...');
  const placed = out.metricChoices.placed_order?.used;
  if (placed) {
    const start = `${monthStart(-23)}T00:00:00Z`;
    const end = new Date().toISOString();
    const rows = [];
    let pages = 0;
    let next = '/events';
    let pageSize = 1000;
    let truncated = false;
    let error = null;
    while (next) {
      if (pages >= MAX_EVENT_PAGES) { truncated = true; break; }
      const params = pages === 0 ? {
        filter: `equals(metric_id,"${placed.id}"),greater-or-equal(datetime,${start}),less-than(datetime,${end})`,
        'fields[event]': 'datetime,event_properties',
        'page[size]': pageSize,
        sort: 'datetime',
      } : {};
      let page;
      try {
        page = await request(next, { params, label: `orders p${pages + 1}`, pace: 50 });
      } catch (e) {
        // The docs disagree on the max page size (200 vs 1000): retry the first page smaller.
        if (pages === 0 && pageSize === 1000 && e.details?.status === 400) { pageSize = 200; continue; }
        error = e.details || { message: e.message };
        out.errors.push({ ok: false, label: 'placed order events', error });
        break;
      }
      pages += 1;
      for (const ev of page?.data || []) {
        const p = ev.attributes?.event_properties || {};
        const items = extractOrderItemNames(p);
        rows.push({
          p: ev.relationships?.profile?.data?.id || null,
          t: ev.attributes?.datetime || null,
          v: Number(p.$value ?? p.value ?? 0) || 0,
          i: items.slice(0, 8),
          d: p['Discount Codes'] || p.DiscountCodes || null,
          s: p['Source Name'] || p.SourceName || null,
          o: p.$event_id || p.OrderId || null,
        });
      }
      next = page?.links?.next || null;
      if (pages % 25 === 0) console.log(`    ${rows.length} orders so far...`);
    }
    fs.writeFileSync(ORDERS_FILE, JSON.stringify({ metric: placed, start, end, pageSize, pages, truncated, error, orders: rows }));
    out.orders = { metric: placed, start, end, pageSize, pages, truncated, error, fetched: rows.length, file: path.basename(ORDERS_FILE) };
    console.log(`    ${rows.length} orders${truncated ? ' (TRUNCATED: raise MAX_EVENT_PAGES)' : ''}.`);
  } else {
    out.orders = { notFound: 'Placed Order' };
    console.log('    no Placed Order metric in this account.');
  }
  save();

  // 5. reporting API
  console.log('[5] Form and weekly flow reports...');
  const formsRes = await safe('form values 365d', () => report('form-values-report', {
    statistics: ['viewed_form', 'viewed_form_uniques', 'submits', 'submit_rate', 'closed_form', 'qualified_form'],
    timeframe: { key: 'last_365_days' },
    group_by: ['form_id', 'form_version_id'],
  }, 'form values 365d'));
  out.reports.formValues365 = formsRes.ok ? formsRes.value : { error: formsRes.error };
  save();
  if (placed) {
    const seriesRes = await safe('flow series weekly 365d', () => report('flow-series-report', {
      statistics: ['recipients', 'delivered', 'open_rate', 'click_rate', 'conversion_uniques', 'conversion_value', 'unsubscribe_rate', 'spam_complaint_rate', 'bounce_rate'],
      timeframe: { key: 'last_365_days' },
      interval: 'weekly',
      conversion_metric_id: placed.id,
      group_by: ['flow_id', 'flow_name', 'send_channel'],
    }, 'flow series weekly 365d'));
    out.reports.flowSeriesWeekly365 = seriesRes.ok ? seriesRes.value : { error: seriesRes.error };
    save();
  }

  // 6. coupons and catalog
  console.log('[6] Coupons and catalog...');
  const coupons = await safe('coupons', () => getAll('/coupons', { 'page[size]': 100 }, 'coupons', 20));
  out.coupons = coupons.ok ? { count: coupons.value.rows.length, truncated: coupons.value.truncated, items: coupons.value.rows.map(compact) } : { error: coupons.error };
  const catalog = await safe('catalog items', () => getAll('/catalog-items', {
    'page[size]': 100,
    'fields[catalog-item]': 'external_id,title,price,url,published,created,updated',
  }, 'catalog items', 30));
  out.catalog = catalog.ok ? { count: catalog.value.rows.length, truncated: catalog.value.truncated, items: catalog.value.rows.map(compact) } : { error: catalog.error };
  save();

  // 7. exact list and segment sizes (15/min with profile_count)
  const lists = audit.lists?.items || [];
  const segments = (audit.segments?.items || []).slice().sort((a, b) => Number(Boolean(b.attributes?.is_active)) - Number(Boolean(a.attributes?.is_active)));
  const total = lists.length + segments.length;
  console.log(`[7] Exact sizes for ${lists.length} lists and ${segments.length} segments (~${Math.ceil(total / 15)} min)...`);
  let done = 0;
  for (const [kind, items] of [['list', lists], ['segment', segments]]) {
    for (const item of items) {
      const res = await safe(`${kind} ${item.id} count`, () => request(`/${kind}s/${item.id}`, {
        params: { [`additional-fields[${kind}]`]: 'profile_count' },
        label: `${kind} ${item.id} count`,
        pace: 4100,
      }));
      const attrs = res.ok ? res.value?.data?.attributes || {} : {};
      out.groupCounts[`${kind}s`].push({
        id: item.id,
        name: item.attributes?.name || attrs.name || null,
        profileCount: res.ok ? attrs.profile_count ?? null : null,
        isActive: item.attributes?.is_active ?? null,
        optInProcess: item.attributes?.opt_in_process ?? null,
        error: res.ok ? null : res.error,
      });
      done += 1;
      if (done % 10 === 0) { console.log(`    ${done}/${total}`); save(); }
    }
  }
  save();

  console.log(`[done] ${OUT_FILE}`);
  console.log(`       ${out.errors.length} section error(s)${out.errors.length ? ': see "errors" in the file' : ''}.`);
})().catch((error) => {
  console.error(`[!] ${error.stack || error.message}`);
  try { save(); } catch (_) { /* nothing to save yet */ }
  process.exit(1);
});
