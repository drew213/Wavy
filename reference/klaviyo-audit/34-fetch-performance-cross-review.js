// Read-only Klaviyo Reporting API pull for account-audit cross-review.
//
// Usage:
//   ACCOUNT_SLUG=client-2026-10-02 node 34-fetch-performance-cross-review.js
//   # Paste the private API key into stdin when prompted. The key is never persisted.
//
// Writes:
//   output/{ACCOUNT_SLUG}/performance-cross-review.json

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const API_BASE = 'https://a.klaviyo.com/api';
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
let API_KEY = process.env.KLAVIYO_PRIVATE_API_KEY || '';

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
  for (let attempt = 1; ; attempt += 1) {
    const response = await fetch(`${API_BASE}${pathname}`, {
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
    // Reporting endpoints throttle at 1/s burst, 2/min steady: wait it out rather than fail.
    if ([429, 500, 502, 503, 504].includes(response.status) && attempt < maxAttempts) {
      const retryAfter = Number(response.headers.get('retry-after') || 0);
      const wait = response.status === 429 ? Math.max(retryAfter * 1000, 31_000) : 2000 * attempt;
      console.log(`    ${label}: ${response.status}, retrying in ${Math.round(wait / 1000)}s`);
      await sleep(wait);
      continue;
    }
    const error = new Error(`${label} failed (${response.status})`);
    error.details = json;
    throw error;
  }
}

async function getAllMetrics() {
  const pages = [];
  let pathname = '/metrics';
  while (pathname && pages.length < 20) {
    const page = await request(pathname, { label: `metrics page ${pages.length + 1}` });
    pages.push(page);
    const next = page?.links?.next;
    pathname = next ? next.replace(API_BASE, '') : null;
  }
  return pages.flatMap((page) => page.data || []).map((metric) => ({
    id: metric.id,
    name: metric.attributes?.name || null,
    integration: metric.attributes?.integration || null,
    created: metric.attributes?.created || null,
    updated: metric.attributes?.updated || null,
  }));
}

const STATISTICS = [
  'recipients',
  'delivered',
  'delivery_rate',
  'bounced',
  'bounce_rate',
  'opens_unique',
  'open_rate',
  'clicks_unique',
  'click_rate',
  'conversion_uniques',
  'conversion_rate',
  'conversion_value',
  'revenue_per_recipient',
  'average_order_value',
  'unsubscribes',
  'unsubscribe_rate',
  'spam_complaints',
  'spam_complaint_rate',
];

async function report(kind, timeframe, conversionMetricId) {
  const type = `${kind}-values-report`;
  const groupBy = kind === 'flow'
    ? ['flow_message_id', 'flow_id', 'flow_message_name', 'flow_name', 'send_channel']
    : ['campaign_message_id', 'campaign_id', 'campaign_message_name', 'send_channel'];
  return request(`/${kind}-values-reports`, {
    method: 'POST',
    label: `${kind} ${timeframe}`,
    body: {
      data: {
        type,
        attributes: {
          statistics: STATISTICS,
          timeframe: { key: timeframe },
          conversion_metric_id: conversionMetricId,
          group_by: groupBy,
        },
      },
    },
  });
}

async function safe(label, fn) {
  try {
    return { ok: true, value: await fn() };
  } catch (error) {
    return { ok: false, error: { label, message: error.message, details: error.details || null } };
  }
}

async function main() {
  API_KEY = await readStdinSecret();
  await require('./source-contract').bindAccount(API_KEY);
  if (!API_KEY) throw new Error('KLAVIYO_PRIVATE_API_KEY is required');
  fs.mkdirSync(OUT_DIR, { recursive: true });

  console.log('[1] Listing metrics...');
  const metrics = await getAllMetrics();
  const placedOrderCandidates = metrics.filter((metric) => /placed order/i.test(metric.name || ''));
  const pinnedPlaced = require('./source-contract').resolvePinned(metrics, 'Placed Order');
  const placedOrder = pinnedPlaced
    || placedOrderCandidates.find((metric) => /shopify/i.test(metric.integration?.name || ''))
    || placedOrderCandidates[0];
  if (!placedOrder) throw new Error('No Placed Order metric found');
  console.log(`    ${metrics.length} metrics; using Placed Order metric ${placedOrder.id}.`);

  console.log('[2] Querying 90-day flow report...');
  const flow90 = await safe('flow last_90_days', () => report('flow', 'last_90_days', placedOrder.id));
  console.log('[3] Querying 365-day flow report...');
  const flow365 = await safe('flow last_365_days', () => report('flow', 'last_365_days', placedOrder.id));

  // Reporting values endpoints share a 2/min steady-state limit.
  console.log('[4] Waiting for the reporting rate-limit window...');
  await sleep(31_000);

  console.log('[5] Querying 90-day campaign report...');
  const campaign90 = await safe('campaign last_90_days', () => report('campaign', 'last_90_days', placedOrder.id));
  console.log('[6] Querying 365-day campaign report...');
  const campaign365 = await safe('campaign last_365_days', () => report('campaign', 'last_365_days', placedOrder.id));

  const out = {
    capturedAt: new Date().toISOString(),
    revision: REVISION,
    accountSlug: ACCOUNT_SLUG,
    conversionMetric: placedOrder,
    placedOrderCandidates,
    metrics,
    reports: { flow90, flow365, campaign90, campaign365 },
  };
  const outFile = path.join(OUT_DIR, 'performance-cross-review.json');
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
  console.log(`[7] Wrote ${outFile}`);
}

main().catch((error) => {
  console.error(`[!] ${error.stack || error.message}`);
  process.exit(1);
});
