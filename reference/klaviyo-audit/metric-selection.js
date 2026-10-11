// Shared, offline metric selection for the audit collectors.
// These accounts use Shopify as their canonical commerce source. Prefer it before
// considering event-name aliases, so legacy Magento events cannot win by spelling
// or API ordering. Other accounts still work when one ecommerce metric matches.
// Never resolve a remaining tie by list order: retain every candidate and skip it.
const { resolvePinned } = require('./source-contract');

const ALIAS_GROUPS = [
  ['Started Checkout', 'Checkout Started'],
  ['Added to Cart', 'Add to Cart'],
  ['Received SMS', 'Received Text Message'],
  ['Clicked SMS', 'Clicked Text Message'],
  ['Failed to Deliver SMS', 'Failed to Deliver Text Message'],
  ['Subscribed to SMS Marketing', 'Subscribed to Text Messaging Marketing'],
  ['Unsubscribed from SMS Marketing', 'Unsubscribed from SMS', 'Unsubscribed from Text Messaging Marketing'],
];

const normalized = (value) => String(value || '').toLowerCase();
const integrationName = (metric) => typeof metric.integration === 'string'
  ? metric.integration : metric.integration?.name || null;
const integrationText = (metric) => typeof metric.integration === 'string'
  ? metric.integration : `${metric.integration?.name || ''} ${metric.integration?.key || ''} ${metric.integration?.category || ''}`;

function metricAliases(name) {
  return [...(ALIAS_GROUPS.find((group) => group.some((alias) => normalized(alias) === normalized(name))) || [name])];
}

function pickMetric(metrics, names, prefer) {
  const pinnedNames = [names].flat().flatMap(metricAliases);
  const pinned = resolvePinned(metrics, pinnedNames); // throws on a missing or mismatched pin
  if (pinned) {
    const candidates = metrics.filter((m) => pinnedNames.some((n) => normalized(n) === normalized(m.name)));
    return { metric: pinned, candidates, status: 'selected', reason: `Pinned by source-contract.json (${pinned.id})` };
  }
  const namePriority = new Map();
  [names].flat().forEach((name, index) => {
    for (const alias of metricAliases(name)) {
      if (!namePriority.has(normalized(alias))) namePriority.set(normalized(alias), index);
    }
  });
  const wanted = new Set(namePriority.keys());
  const candidates = metrics.filter((metric) => wanted.has(normalized(metric.name)));
  let pool = candidates;
  let reason = 'Only matching metric';
  if (prefer === 'ecommerce') {
    const shopify = candidates.filter((metric) => /\bshopify\b/i.test(integrationText(metric)));
    const ecommerce = candidates.filter((metric) => /shopify|woocommerce|bigcommerce|magento|salesforce commerce|wix|squarespace|ecommerce/i.test(integrationText(metric)));
    if (shopify.length) {
      pool = shopify;
      reason = 'Canonical Shopify commerce source';
    } else if (ecommerce.length) {
      pool = ecommerce;
      reason = 'Only matching ecommerce metric; no Shopify candidate';
    }
  } else if (prefer === 'klaviyo') {
    const klaviyo = candidates.filter((metric) => /\bklaviyo\b/i.test(integrationText(metric)));
    if (klaviyo.length) {
      pool = klaviyo;
      reason = 'Klaviyo channel metric';
    }
  }
  if (!pool.length) return { metric: null, candidates, status: 'missing', reason: 'No matching metric name or alias' };
  // Preserve intentional name fallbacks such as Form submitted by profile ->
  // Submitted Form within ONE integration. Names in an alias group have equal
  // priority. Name ordering must never choose between different integrations.
  if (pool.length > 1 && new Set(pool.map((metric) => normalized(integrationName(metric)))).size === 1) {
    const bestPriority = Math.min(...pool.map((metric) => namePriority.get(normalized(metric.name))));
    const preferredNames = pool.filter((metric) => namePriority.get(normalized(metric.name)) === bestPriority);
    if (preferredNames.length < pool.length) reason += '; preferred requested metric name within the same integration';
    pool = preferredNames;
  }
  if (pool.length > 1) return {
    metric: null, candidates, status: 'ambiguous',
    reason: `Multiple eligible metrics remain (${pool.map((metric) => metric.id).join(', ')}); explicit source/metric selection required`,
  };
  return { metric: pool[0], candidates, status: 'selected', reason };
}

function describeMetricChoice({ metric, candidates = [], status, reason }) {
  const summarize = (item) => ({ id: item.id, name: item.name, integration: integrationName(item) });
  return {
    used: metric ? summarize(metric) : null,
    candidates: candidates.map(summarize),
    otherCandidates: candidates.filter((item) => item.id !== metric?.id).map(summarize),
    status,
    reason,
  };
}

module.exports = { pickMetric, metricAliases, describeMetricChoice };
