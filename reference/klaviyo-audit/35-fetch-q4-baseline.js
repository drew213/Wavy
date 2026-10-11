// Read-only Q4 / BFCM baseline pull: what the last two holiday seasons looked like,
// so the growth plan can set record targets against real numbers.
//
// Usage:
//   ACCOUNT_SLUG=client-2026-10-02 node 35-fetch-q4-baseline.js
//   # Paste the private API key into stdin when prompted. The key is never persisted.
//
// Pulls:
//   - 24 months of monthly totals for Placed Order (orders, revenue, buyers), split by
//     attributed channel and attributed flow, plus funnel, list-growth and email
//     health metrics (Metric Aggregates API, event time)
//   - daily Placed Order for Nov-Dec of the last two years (BFCM shape)
//   - every email and SMS campaign scheduled since Oct 1 two years back, with audiences
//     and subject lines (the BFCM send calendar)
//   - flow and campaign Reporting API values for Q4 of the last two years (send date)
//   - sending domains
//
// Writes:
//   output/{ACCOUNT_SLUG}/q4-baseline.json

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { pickMetric, describeMetricChoice } = require('./metric-selection');
const { aggregateWindowUtc } = require('./aggregate-boundaries');

const API_BASE = 'https://a.klaviyo.com/api';
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
let API_KEY = process.env.KLAVIYO_PRIVATE_API_KEY || '';

// Season boundaries: the current year's Q4 hasn't happened yet, so the two most recent
// complete Q4s are this year - 1 and this year - 2.
const NOW = new Date();
const Y = NOW.getUTCFullYear();
const MONTHLY_WINDOWS = [
  [`${Y - 2}-10-01T00:00:00`, `${Y - 1}-10-01T00:00:00`],
  [`${Y - 1}-10-01T00:00:00`, `${Y}-10-01T00:00:00`],
];
const HOLIDAY_WINDOWS = [
  [`${Y - 2}-11-01T00:00:00`, `${Y - 1}-01-01T00:00:00`],
  [`${Y - 1}-11-01T00:00:00`, `${Y}-01-01T00:00:00`],
];
const Q4_REPORT_WINDOWS = [
  { label: `q4_${Y - 2}`, start: `${Y - 2}-10-01T00:00:00+00:00`, end: `${Y - 1}-01-01T00:00:00+00:00` },
  { label: `q4_${Y - 1}`, start: `${Y - 1}-10-01T00:00:00+00:00`, end: `${Y}-01-01T00:00:00+00:00` },
];
const CAMPAIGNS_SINCE = `${Y - 2}-10-01T00:00:00Z`;

// Monthly counts for these metrics, when the account has them.
const TREND_METRICS = [
  'Started Checkout', 'Added to Cart', 'Viewed Product', 'Active on Site',
  'Subscribed to Email Marketing', 'Subscribed to SMS Marketing', 'Subscribed to List',
  'Unsubscribed from Email Marketing', 'Unsubscribed from SMS Marketing', 'Unsubscribed',
  'Received Email', 'Opened Email', 'Clicked Email', 'Bounced Email', 'Marked Email as Spam', 'Dropped Email',
  'Received SMS', 'Clicked SMS', 'Failed to Deliver SMS',
];

const REPORT_STATISTICS = [
  'recipients', 'delivered', 'opens_unique', 'open_rate', 'clicks_unique', 'click_rate',
  'conversion_uniques', 'conversion_rate', 'conversion_value', 'revenue_per_recipient',
  'average_order_value', 'unsubscribes', 'unsubscribe_rate', 'spam_complaints', 'spam_complaint_rate',
  'bounced', 'bounce_rate',
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readStdinSecret() {
  if (API_KEY.trim()) return API_KEY.trim();
  if (!process.stdin.isTTY) {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
    return Buffer.concat(chunks).toString('utf8').trim();
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  const answer = await new Promise((resolve) => rl.question('Paste KLAVIYO_PRIVATE_API_KEY: ', resolve));
  rl.close();
  return answer.trim();
}

async function request(pathname, { method = 'GET', body = null, label = pathname, maxAttempts = 6 } = {}) {
  const url = pathname.startsWith('http') ? pathname : `${API_BASE}${pathname}`;
  let last = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: `Klaviyo-API-Key ${API_KEY}`,
        accept: 'application/vnd.api+json',
        'content-type': 'application/vnd.api+json',
        revision: REVISION,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let json;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (_) {
      json = { rawText: text.slice(0, 1000) };
    }
    if (response.ok) return json;
    last = { status: response.status, body: json };
    if (![408, 429, 500, 502, 503, 504].includes(response.status)) break;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(retryAfter ? retryAfter * 1000 : 2000 * attempt);
  }
  const error = new Error(`${label} failed (${last?.status})`);
  error.details = last;
  throw error;
}

async function safe(label, fn) {
  try {
    return { ok: true, value: await fn() };
  } catch (error) {
    console.log(`    [!] ${error.message}`);
    return { ok: false, error: { label, message: error.message, details: error.details || null } };
  }
}

async function getAllPages(pathname, label, maxPages = 40) {
  const pages = [];
  let next = pathname;
  while (next && pages.length < maxPages) {
    pages.push(await request(next, { label: `${label} page ${pages.length + 1}` }));
    next = pages[pages.length - 1]?.links?.next || null;
    await sleep(300);
  }
  return { pages, capped: Boolean(next) };
}

async function getAllMetrics() {
  const { pages } = await getAllPages('/metrics', 'metrics', 20);
  return pages.flatMap((page) => page.data || []).map((metric) => ({
    id: metric.id,
    name: metric.attributes?.name || null,
    integration: metric.attributes?.integration?.name || null,
  }));
}

// Metric Aggregates: one query per window, all result pages, compacted to dates + rows.
async function aggregate({ metricId, measurements, start, end, interval, by, timezone, label }) {
  const utcWindow = aggregateWindowUtc({ start, end }, timezone);
  const rows = [];
  let dates = null;
  let cursor = null;
  for (let page = 0; page < 20; page += 1) {
    const attributes = {
      metric_id: metricId,
      measurements,
      interval,
      filter: [`greater-or-equal(datetime,${utcWindow.start})`, `less-than(datetime,${utcWindow.end})`],
      timezone,
      page_size: 500,
    };
    if (by) attributes.by = by;
    if (cursor) attributes.page_cursor = cursor;
    const json = await request('/metric-aggregates', {
      method: 'POST',
      label,
      body: { data: { type: 'metric-aggregate', attributes } },
    });
    dates = dates || json?.data?.attributes?.dates || [];
    rows.push(...(json?.data?.attributes?.data || []));
    cursor = json?.links?.next ? new URL(json.links.next).searchParams.get('page_cursor') : null;
    await sleep(400);
    if (!cursor) break;
  }
  return {
    start, end, utcWindow, timezone, interval, by: by || null, measurements,
    dates: (dates || []).map((d) => String(d).slice(0, 10)),
    rows: rows.map((r) => ({ dimensions: r.dimensions || [], measurements: r.measurements || {} })),
  };
}

// Base statistics are the ones step 5 already pulls successfully; the extras (opens,
// bounces) are retried without if the API rejects them.
const BASE_STATISTICS = REPORT_STATISTICS.filter((s) => !/^(opens_unique|open_rate|bounced|bounce_rate)$/.test(s));

async function reportValues(kind, window, conversionMetricId) {
  try {
    return await reportValuesWith(kind, window, conversionMetricId, REPORT_STATISTICS);
  } catch (error) {
    if (error.details?.status !== 400) throw error;
    console.log(`    ${kind} ${window.label}: retrying without open/bounce statistics`);
    await sleep(31_000);
    return reportValuesWith(kind, window, conversionMetricId, BASE_STATISTICS);
  }
}

async function reportValuesWith(kind, window, conversionMetricId, statistics) {
  const groupBy = kind === 'flow'
    ? ['flow_id', 'flow_name', 'flow_message_id', 'flow_message_name', 'send_channel']
    : ['campaign_id', 'campaign_message_id', 'campaign_message_name', 'send_channel'];
  return request(`/${kind}-values-reports`, {
    method: 'POST',
    label: `${kind} ${window.label}`,
    body: {
      data: {
        type: `${kind}-values-report`,
        attributes: {
          statistics,
          timeframe: { start: window.start, end: window.end },
          conversion_metric_id: conversionMetricId,
          group_by: groupBy,
        },
      },
    },
  });
}

async function listCampaigns(channel) {
  const filter = `and(equals(messages.channel,'${channel}'),greater-or-equal(scheduled_at,${CAMPAIGNS_SINCE}))`;
  const params = new URLSearchParams({
    filter,
    sort: '-scheduled_at',
    include: 'campaign-messages',
  });
  const { pages, capped } = await getAllPages(`/campaigns?${params}`, `campaigns ${channel}`, 100);
  const messages = new Map(pages.flatMap((p) => p.included || []).filter((x) => x.type === 'campaign-message').map((m) => [m.id, m]));
  const campaigns = pages.flatMap((p) => p.data || []).map((c) => {
    const a = c.attributes || {};
    const msgs = (c.relationships?.['campaign-messages']?.data || []).map((ref) => messages.get(ref.id)).filter(Boolean);
    return {
      id: c.id,
      name: a.name,
      status: a.status,
      archived: a.archived,
      send_time: a.send_time || null,
      scheduled_at: a.scheduled_at || null,
      audiences: a.audiences || null,
      send_strategy: a.send_strategy?.method || null,
      smart_sending: a.send_options?.use_smart_sending ?? null,
      messages: msgs.map((m) => ({
        id: m.id,
        channel: m.attributes?.definition?.channel || m.attributes?.channel || channel,
        subject: m.attributes?.definition?.content?.subject || m.attributes?.content?.subject || null,
        preview_text: m.attributes?.definition?.content?.preview_text || m.attributes?.content?.preview_text || null,
        sms_body: channel === 'sms' ? (m.attributes?.definition?.content?.body || m.attributes?.content?.body || null) : undefined,
      })),
    };
  });
  return { channel, since: CAMPAIGNS_SINCE, count: campaigns.length, capped, campaigns };
}

async function main() {
  API_KEY = await readStdinSecret();
  await require('./source-contract').bindAccount(API_KEY);
  if (!API_KEY) throw new Error('KLAVIYO_PRIVATE_API_KEY is required');
  fs.mkdirSync(OUT_DIR, { recursive: true });

  console.log('[1] Account timezone and metrics...');
  const account = await request('/accounts', { label: 'accounts' });
  const timezone = account?.data?.[0]?.attributes?.timezone || 'UTC';
  const metrics = await getAllMetrics();
  const placedOrderChoice = pickMetric(metrics, 'Placed Order', 'ecommerce');
  const placedOrder = placedOrderChoice.metric;
  if (!placedOrder) throw new Error(`Placed Order: ${placedOrderChoice.reason}`);
  console.log(`    timezone ${timezone}; ${metrics.length} metrics; Placed Order ${placedOrder.id} (${placedOrder.integration})`);

  const out = {
    capturedAt: new Date().toISOString(),
    revision: REVISION,
    accountSlug: ACCOUNT_SLUG,
    timezone,
    notes: [
      'metricAggregates are by EVENT time; reports are by SEND date. They will not match exactly.',
      'Attributed revenue uses the account attribution window. It is not incremental revenue.',
      'Opened Email counts include machine opens (Apple Mail Privacy Protection).',
      'A blank dimension in by-$attributed_channel / by-$attributed_flow rows means the order was not attributed to that dimension.',
    ],
    placedOrder,
    metricChoices: { placedOrder: describeMetricChoice(placedOrderChoice) },
    monthly: {},
    holidayDaily: {},
    reports: {},
    campaigns: {},
    sendingDomains: null,
  };

  console.log('[2] Placed Order monthly: total, by attributed channel, by attributed flow...');
  for (const [by, key] of [[null, 'placedOrderTotal'], [['$attributed_channel'], 'placedOrderByChannel'], [['$attributed_flow'], 'placedOrderByFlow']]) {
    out.monthly[key] = [];
    for (const [start, end] of MONTHLY_WINDOWS) {
      out.monthly[key].push(await safe(`${key} ${start}`, () => aggregate({
        metricId: placedOrder.id, measurements: ['count', 'sum_value', 'unique'], start, end, interval: 'month', by, timezone, label: `${key} ${start}`,
      })));
    }
  }

  console.log('[3] Placed Order daily for Nov-Dec of the last two years...');
  for (const [by, key] of [[null, 'placedOrderTotal'], [['$attributed_channel'], 'placedOrderByChannel']]) {
    out.holidayDaily[key] = [];
    for (const [start, end] of HOLIDAY_WINDOWS) {
      out.holidayDaily[key].push(await safe(`daily ${key} ${start}`, () => aggregate({
        metricId: placedOrder.id, measurements: ['count', 'sum_value', 'unique'], start, end, interval: 'day', by, timezone, label: `daily ${key} ${start}`,
      })));
    }
  }

  console.log('[4] Monthly trend metrics (funnel, list growth, email and SMS health)...');
  out.monthly.trends = {};
  for (const name of TREND_METRICS) {
    const prefer = ['Started Checkout', 'Added to Cart'].includes(name) ? 'ecommerce' : 'klaviyo';
    const choice = pickMetric(metrics, name, prefer);
    const { metric } = choice;
    out.metricChoices[name] = describeMetricChoice(choice);
    if (!metric) {
      out.monthly.trends[name] = { ok: false, error: { message: choice.reason }, ambiguous: choice.status === 'ambiguous' };
      console.log(`    - ${name}: ${choice.reason}`);
      continue;
    }
    const windows = [];
    for (const [start, end] of MONTHLY_WINDOWS) {
      windows.push(await safe(`${name} ${start}`, () => aggregate({
        metricId: metric.id, measurements: ['count', 'unique'], start, end, interval: 'month', timezone, label: `${name} ${start}`,
      })));
    }
    out.monthly.trends[name] = { ok: true, metric, windows };
    console.log(`    ${name}`);
  }

  console.log('[5] Campaign calendar since', CAMPAIGNS_SINCE, '...');
  for (const channel of ['email', 'sms']) {
    out.campaigns[channel] = await safe(`campaigns ${channel}`, () => listCampaigns(channel));
    if (out.campaigns[channel].ok) console.log(`    ${channel}: ${out.campaigns[channel].value.count} campaigns`);
  }

  console.log('[6] Q4 flow and campaign reports (rate-limited, about 2 minutes)...');
  for (const window of Q4_REPORT_WINDOWS) {
    out.reports[`flow_${window.label}`] = await safe(`flow ${window.label}`, () => reportValues('flow', window, placedOrder.id));
    out.reports[`campaign_${window.label}`] = await safe(`campaign ${window.label}`, () => reportValues('campaign', window, placedOrder.id));
    // Reporting values endpoints share a 2/min steady-state limit.
    await sleep(31_000);
  }

  console.log('[7] Sending domains...');
  out.sendingDomains = await safe('sending domains', () => request('/sending-domains', { label: 'sending domains' }));

  const outFile = path.join(OUT_DIR, 'q4-baseline.json');
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
  const failed = [];
  const walk = (node, p) => {
    if (!node || typeof node !== 'object') return;
    if (node.ok === false && node.error?.label) failed.push(node.error.label);
    for (const [k, v] of Object.entries(node)) walk(v, `${p}.${k}`);
  };
  walk(out, '');
  console.log(`[8] Wrote ${outFile}${failed.length ? `\n    ${failed.length} sub-pulls failed: ${failed.join('; ')}` : ''}`);
}

main().catch((error) => {
  console.error(`[!] ${error.stack || error.message}`);
  process.exit(1);
});
