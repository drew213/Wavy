// Optional per-account source contract: output/{ACCOUNT_SLUG}/source-contract.json.
// Pins the canonical metric IDs (keyed by metric name) after they were verified from
// event evidence, so dormant integrations (for example an old Shopify connection on a
// WooCommerce store) cannot win selection. No file means unchanged legacy behaviour.
// Once a file exists, every defect fails closed (throws) instead of falling back:
// malformed JSON, a non-object or empty metricIds, missing required pins, pins with
// unknown fields, contradictory pins within an alias group, a pinned ID absent from
// the account, a pinned metric whose actual name is not one of the requested names or
// whose integration differs, and (via bindAccount) an API key for a different account.
const fs = require('fs');
const path = require('path');

const ALIAS_GROUPS = [
  ['Started Checkout', 'Checkout Started'],
  ['Added to Cart', 'Add to Cart'],
  ['Received SMS', 'Received Text Message'],
  ['Clicked SMS', 'Clicked Text Message'],
];
const norm = (s) => String(s || '').trim().toLowerCase();
const groupOf = (name) => (ALIAS_GROUPS.find((g) => g.some((a) => norm(a) === norm(name))) || [name]).map(norm);
const PIN_FIELDS = new Set(['id', 'integration', 'events_2024-10-01_to_asof', 'sum_value_2024-10-01_to_asof']);

let cached;
function contractPath() {
  const slug = process.env.ACCOUNT_SLUG;
  return slug ? path.join(__dirname, 'output', slug, 'source-contract.json') : null;
}

function validate(c, file) {
  const fail = (msg) => { throw new Error(`source-contract.json at ${file}: ${msg}`); };
  if (!c || typeof c !== 'object' || Array.isArray(c)) fail('top level must be an object');
  if (typeof c.account_id !== 'string' || !c.account_id) fail('account_id (the Klaviyo account ID) is required');
  const ids = c.metricIds;
  if (!ids || typeof ids !== 'object' || Array.isArray(ids) || !Object.keys(ids).length) fail('metricIds must be a non-empty object');
  const pins = new Map(); // canonical alias-group key -> { key, id, integration }
  for (const [key, raw] of Object.entries(ids)) {
    let pin;
    if (typeof raw === 'string' && raw) pin = { id: raw, integration: null };
    else if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
      const unknown = Object.keys(raw).filter((k) => !PIN_FIELDS.has(k));
      if (unknown.length) fail(`pin "${key}" has unsupported field(s) ${unknown.join(', ')} (the mapping key is the event name)`);
      if (typeof raw.id !== 'string' || !raw.id) fail(`pin "${key}" needs a string id`);
      if (raw.integration != null && typeof raw.integration !== 'string') fail(`pin "${key}" integration must be a string`);
      pin = { id: raw.id, integration: raw.integration || null };
    } else fail(`pin "${key}" must be a metric ID string or an object with id`);
    const gk = groupOf(key).join('|');
    const prior = pins.get(gk);
    if (prior && prior.id !== pin.id) fail(`contradictory pins "${prior.key}" = ${prior.id} and "${key}" = ${pin.id} name the same event`);
    pins.set(gk, { key, ...pin });
  }
  const required = c.requiredPins ?? (c.commerceIntegration ? ['Placed Order'] : []);
  if (!Array.isArray(required)) fail('requiredPins must be an array');
  for (const name of required) if (!pins.has(groupOf(name).join('|'))) fail(`required pin "${name}" is missing`);
  return { ...c, pins };
}

function loadContract() {
  if (cached !== undefined) return cached;
  cached = null;
  const file = contractPath();
  if (!file || !fs.existsSync(file)) return cached;
  let parsed;
  try { parsed = JSON.parse(fs.readFileSync(file, 'utf8')); } catch (err) {
    throw new Error(`source-contract.json at ${file} is not valid JSON: ${err.message}`);
  }
  cached = validate(parsed, file);
  return cached;
}

// Pinned { key, id, integration } for the requested names (all aliases), or null.
function pinnedMetric(names) {
  const c = loadContract();
  if (!c) return null;
  const hits = new Map();
  for (const name of [names].flat()) {
    const pin = c.pins.get(groupOf(name).join('|'));
    if (pin) hits.set(pin.id, pin);
  }
  if (hits.size > 1) throw new Error(`source-contract has different pins for the requested names ${[names].flat().join(' / ')}`);
  return hits.size ? [...hits.values()][0] : null;
}

function pinnedMetricId(names) {
  return pinnedMetric(names)?.id || null;
}

// Finds the pinned metric in a metric list and checks that its actual name is one of
// the requested names (or their aliases) and its integration matches the pin.
// Accepts flattened ({ name, integration }) or raw API ({ attributes }) metrics.
function resolvePinned(metrics, names) {
  const pin = pinnedMetric(names);
  if (!pin) return null;
  const metric = metrics.find((m) => m.id === pin.id);
  if (!metric) throw new Error(`source-contract pins ${pin.key} = ${pin.id}, but that metric is not in this account's metric list`);
  const actual = metric.name ?? metric.attributes?.name;
  const allowed = new Set([names].flat().flatMap(groupOf));
  if (!allowed.has(norm(actual))) throw new Error(`source-contract pin ${pin.id} is "${actual}", not one of the requested ${[names].flat().join(' / ')}`);
  const integ = metric.integration ?? metric.attributes?.integration;
  const integName = typeof integ === 'string' ? integ : integ?.name;
  if (pin.integration && norm(integName) !== norm(pin.integration)) {
    throw new Error(`source-contract pin ${pin.id} (${actual}) has integration "${integName}", expected "${pin.integration}"`);
  }
  return metric;
}

// Fetches /accounts with the given key and throws unless it is the contract's account.
// No-op without a contract. Call before writing any output.
async function bindAccount(apiKey, revision = '2026-04-15') {
  const c = loadContract();
  if (!c) return null;
  const res = await fetch('https://a.klaviyo.com/api/accounts', {
    headers: { Authorization: `Klaviyo-API-Key ${apiKey}`, revision, accept: 'application/vnd.api+json' },
  });
  if (!res.ok) throw new Error(`bindAccount: /accounts returned ${res.status}; cannot confirm the account`);
  const id = (await res.json())?.data?.[0]?.id;
  if (id !== c.account_id) throw new Error(`bindAccount: this key is for account ${id}, but output/${process.env.ACCOUNT_SLUG} is contracted to ${c.account_id}`);
  return id;
}

module.exports = { loadContract, pinnedMetric, pinnedMetricId, resolvePinned, bindAccount, validate };
