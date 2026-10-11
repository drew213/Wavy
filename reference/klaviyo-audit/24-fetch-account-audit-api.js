// Read-only Klaviyo API account audit pull.
//
// Usage:
//   ACCOUNT_SLUG=client-2026-10-02 node 24-fetch-account-audit-api.js
//   # If KLAVIYO_PRIVATE_API_KEY is not set, paste it into stdin when prompted.
//
// Writes:
//   output/{ACCOUNT_SLUG}/account-audit-api.json

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const API_BASE = 'https://a.klaviyo.com/api';
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const CAMPAIGN_REVISION = process.env.KLAVIYO_CAMPAIGN_REVISION || '2026-04-15.pre';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
const MAX_FLOW_DETAILS = Number(process.env.MAX_FLOW_DETAILS || Infinity);
const MAX_CAMPAIGN_DETAILS = Number(process.env.MAX_CAMPAIGN_DETAILS || Infinity);
const MAX_MESSAGE_DETAILS = Number(process.env.MAX_MESSAGE_DETAILS || 5000);

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
  const answer = await new Promise((resolve) => {
    rl.question('Paste KLAVIYO_PRIVATE_API_KEY: ', resolve);
  });
  rl.close();
  return answer.trim();
}

function ensureOutDir() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
}

function apiUrl(pathname, params = {}) {
  const url = new URL(`${API_BASE}${pathname}`);
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === '') continue;
    if (Array.isArray(value)) {
      for (const item of value) url.searchParams.append(key, item);
    } else {
      url.searchParams.set(key, value);
    }
  }
  return url.toString();
}

function truncate(value, max = 4000) {
  if (typeof value !== 'string') return value;
  return value.length > max ? `${value.slice(0, max)}...[truncated ${value.length - max} chars]` : value;
}

function stripHtml(html) {
  if (!html || typeof html !== 'string') return html;
  return html
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/\s+/g, ' ')
    .trim();
}

function compactResource(resource) {
  if (!resource) return null;
  return {
    type: resource.type,
    id: resource.id,
    attributes: resource.attributes || {},
    relationships: resource.relationships || {},
    links: resource.links || {},
  };
}

function compactMessage(resource) {
  const attrs = resource?.data?.attributes || resource?.attributes || {};
  const content = attrs.content || {};
  const body = content.body || attrs.body || '';
  return {
    type: resource?.data?.type || resource?.type || null,
    id: resource?.data?.id || resource?.id || null,
    name: attrs.name || null,
    channel: attrs.channel || null,
    status: attrs.status || null,
    subject: content.subject || attrs.subject || null,
    previewText: content.preview_text || attrs.preview_text || null,
    fromEmail: content.from_email || attrs.from_email || null,
    fromLabel: content.from_label || attrs.from_label || null,
    created: attrs.created || attrs.created_at || null,
    updated: attrs.updated || attrs.updated_at || null,
    bodyCharCount: typeof body === 'string' ? body.length : 0,
    bodyText: truncate(stripHtml(body), 2500),
  };
}

function dataItems(pagesOrResponse) {
  const pages = Array.isArray(pagesOrResponse) ? pagesOrResponse : [pagesOrResponse];
  return pages.flatMap((page) => {
    if (!page) return [];
    if (Array.isArray(page.data)) return page.data;
    return page.data ? [page.data] : [];
  });
}

function includedItems(pagesOrResponse) {
  const pages = Array.isArray(pagesOrResponse) ? pagesOrResponse : [pagesOrResponse];
  return pages.flatMap((page) => Array.isArray(page?.included) ? page.included : []);
}

async function request(url, label, options = {}) {
  const revision = options.revision || REVISION;
  const maxAttempts = options.maxAttempts || 6;
  let lastError = null;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    await sleep(options.delayMs || 450);
    let response;
    try {
      response = await fetch(url, {
        headers: {
          Authorization: `Klaviyo-API-Key ${API_KEY}`,
          accept: 'application/vnd.api+json',
          revision,
        },
      });
    } catch (error) {
      lastError = {
        label,
        url: url.replace(/[?&]api_key=[^&]+/g, ''),
        status: 'network',
        revision,
        error: { message: error.message },
      };
      await sleep(1000 * attempt);
      continue;
    }
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch (_) {
      json = { rawText: truncate(text, 1000) };
    }
    if (response.ok) return json;
    lastError = {
      label,
      url: url.replace(/[?&]api_key=[^&]+/g, ''),
      status: response.status,
      revision,
      error: json,
    };
    if (![408, 429, 500, 502, 503, 504].includes(response.status)) break;
    const retryAfter = Number(response.headers.get('retry-after') || 0);
    await sleep(retryAfter ? retryAfter * 1000 : 1000 * attempt);
  }
  const err = new Error(`${label} failed (${lastError?.status || 'unknown'})`);
  err.details = lastError;
  throw err;
}

async function get(pathname, params = {}, label = pathname, options = {}) {
  return request(apiUrl(pathname, params), label, options);
}

async function getPaginated(pathname, params = {}, label = pathname, options = {}) {
  const pages = [];
  let url = apiUrl(pathname, params);
  const maxPages = options.maxPages || 20;
  while (url && pages.length < maxPages) {
    const page = await request(url, `${label} page ${pages.length + 1}`, options);
    pages.push(page);
    url = page?.links?.next || null;
  }
  return pages;
}

async function safe(label, fn) {
  try {
    const value = await fn();
    return { ok: true, value };
  } catch (error) {
    return {
      ok: false,
      error: error.details || {
        label,
        message: error.message,
      },
    };
  }
}

function actionSummary(actionDetail, messages) {
  const attrs = actionDetail?.data?.attributes || actionDetail?.attributes || {};
  return {
    id: actionDetail?.data?.id || actionDetail?.id || null,
    type: actionDetail?.data?.type || actionDetail?.type || null,
    actionType: attrs.action_type || attrs.type || null,
    status: attrs.status || null,
    name: attrs.name || attrs.label || null,
    settings: attrs.settings || null,
    trackingOptions: attrs.tracking_options || null,
    created: attrs.created || attrs.created_at || null,
    updated: attrs.updated || attrs.updated_at || null,
    messages,
  };
}

async function fetchFlowDetail(flow, counters) {
  const attrs = flow.attributes || {};
  const flowId = flow.id;
  console.log(`    flow ${flowId} ${attrs.name || ''}`);

  const detail = await safe(`flow ${flowId}`, () => get(`/flows/${flowId}`, {
    'additional-fields[flow]': 'definition',
    include: 'flow-actions,tags',
  }, `flow ${flowId}`));

  const actionPages = await safe(`actions ${flowId}`, () => getPaginated(`/flows/${flowId}/flow-actions`, {
    'page[size]': 50,
  }, `actions ${flowId}`, { maxPages: 10 }));
  const actionStubs = actionPages.ok ? dataItems(actionPages.value) : [];

  const actions = [];
  for (const action of actionStubs) {
    const actionDetail = await safe(`action ${action.id}`, () => get(`/flow-actions/${action.id}`, {
      include: 'flow-messages',
    }, `action ${action.id}`));
    const messagePages = await safe(`messages ${action.id}`, () => getPaginated(`/flow-actions/${action.id}/flow-messages`, {
      'page[size]': 50,
    }, `messages ${action.id}`, { maxPages: 5 }));
    const messageStubs = messagePages.ok ? dataItems(messagePages.value) : [];
    const messages = [];
    for (const message of messageStubs) {
      if (counters.messageDetails >= MAX_MESSAGE_DETAILS) {
        messages.push({ id: message.id, skipped: 'MAX_MESSAGE_DETAILS reached' });
        continue;
      }
      const messageDetail = await safe(`message ${message.id}`, () => get(`/flow-messages/${message.id}`, {}, `message ${message.id}`));
      counters.messageDetails += 1;
      messages.push(messageDetail.ok ? compactMessage(messageDetail.value) : { id: message.id, error: messageDetail.error });
    }
    actions.push(actionDetail.ok
      ? actionSummary(actionDetail.value, messages)
      : { id: action.id, error: actionDetail.error, messages });
  }

  const definition = detail.ok ? detail.value?.data?.attributes?.definition || null : null;
  return {
    flow: compactResource(flow),
    detail: detail.ok ? compactResource(detail.value?.data) : null,
    included: detail.ok ? includedItems(detail.value).map(compactResource) : [],
    definition,
    actionFetch: actionPages.ok ? { ok: true, count: actionStubs.length } : actionPages,
    actionCount: actions.length,
    messageCount: actions.reduce((sum, action) => sum + (Array.isArray(action.messages) ? action.messages.length : 0), 0),
    actions,
    errors: [detail, actionPages].filter((result) => !result.ok).map((result) => result.error),
  };
}

async function fetchCampaigns() {
  const primary = await safe('campaigns', () => getPaginated('/campaigns', {
    'page[size]': 50,
    sort: '-updated_at',
  }, 'campaigns', { revision: CAMPAIGN_REVISION, maxPages: 40 }));

  if (primary.ok) {
    return {
      mode: 'omni',
      revision: CAMPAIGN_REVISION,
      truncated: hasMore(primary.value),
      pages: primary.value,
      items: dataItems(primary.value).slice(0, MAX_CAMPAIGN_DETAILS).map(compactResource),
    };
  }

  const channelFilters = [
    ['email', "equals(messages.channel,'email')"],
    ['sms', "equals(messages.channel,'sms')"],
    ['mobile_push', "equals(messages.channel,'mobile_push')"],
  ];
  const byChannel = {};
  for (const [channel, filter] of channelFilters) {
    const result = await safe(`campaigns ${channel}`, () => getPaginated('/campaigns', {
      'page[size]': 50,
      sort: '-updated_at',
      filter,
    }, `campaigns ${channel}`, { revision: REVISION, maxPages: 40 }));
    byChannel[channel] = result.ok
      ? dataItems(result.value).slice(0, MAX_CAMPAIGN_DETAILS).map(compactResource)
      : result;
  }
  return {
    mode: 'channel-filter-fallback',
    primaryError: primary.error,
    revision: REVISION,
    byChannel,
  };
}

async function fetchCollection(pathname, params, label, options = {}) {
  const result = await safe(label, () => getPaginated(pathname, params, label, options));
  return result.ok
    ? { ok: true, count: dataItems(result.value).length, truncated: hasMore(result.value), items: dataItems(result.value).map(compactResource) }
    : result;
}

function hasMore(pages) {
  return Boolean(pages.length && pages[pages.length - 1]?.links?.next);
}

async function main() {
  API_KEY = await readStdinSecret();
  if (!API_KEY) {
    console.error('[!] KLAVIYO_PRIVATE_API_KEY is required.');
    process.exit(1);
  }

  ensureOutDir();
  console.log(`[1] Capturing Klaviyo account audit data with revision ${REVISION}...`);

  const account = await safe('accounts', () => get('/accounts', {}, 'accounts'));

  console.log('[2] Listing flows...');
  const flowPages = await getPaginated('/flows', { 'page[size]': 50 }, 'flows', { maxPages: 40 });
  const allFlows = dataItems(flowPages);
  console.log(`    ${allFlows.length} flows found.`);

  const flowDetails = [];
  const counters = { messageDetails: 0 };
  for (const flow of allFlows.slice(0, MAX_FLOW_DETAILS)) {
    const result = await safe(`flow-detail ${flow.id}`, () => fetchFlowDetail(flow, counters));
    flowDetails.push(result.ok ? result.value : { flow: compactResource(flow), errors: [result.error] });
  }

  console.log('[3] Listing campaigns, forms, lists, segments, metrics, templates...');
  const campaigns = await fetchCampaigns();
  const forms = await fetchCollection('/forms', { 'page[size]': 10, sort: '-updated_at' }, 'forms', { maxPages: 100 });
  const lists = await fetchCollection('/lists', { 'page[size]': 10 }, 'lists', { maxPages: 100 });
  const segments = await fetchCollection('/segments', { 'page[size]': 10 }, 'segments', { maxPages: 100 });
  const metrics = await fetchCollection('/metrics', {}, 'metrics', { maxPages: 50 });
  const templates = await fetchCollection('/templates', { 'page[size]': 10, 'fields[template]': 'name,editor_type,created,updated' }, 'templates', { maxPages: 100 });
  const profilesSample = await fetchCollection('/profiles', { 'page[size]': 1 }, 'profiles sample', { maxPages: 1 });

  const out = {
    capturedAt: new Date().toISOString(),
    revision: REVISION,
    campaignRevision: CAMPAIGN_REVISION,
    accountSlug: ACCOUNT_SLUG,
    limits: {
      maxFlowDetails: MAX_FLOW_DETAILS,
      maxCampaignDetails: MAX_CAMPAIGN_DETAILS,
      maxMessageDetails: MAX_MESSAGE_DETAILS,
    },
    account: account.ok
      ? { ok: true, items: dataItems(account.value).map(compactResource) }
      : account,
    flows: {
      count: allFlows.length,
      pages: flowPages.length,
      truncated: hasMore(flowPages),
      detailed: flowDetails.length,
      messageDetails: counters.messageDetails,
      messageCapHit: counters.messageDetails >= MAX_MESSAGE_DETAILS,
      items: allFlows.map(compactResource),
      details: flowDetails,
    },
    campaigns,
    forms,
    lists,
    segments,
    metrics,
    templates,
    profilesSample,
  };

  const outFile = path.join(OUT_DIR, 'account-audit-api.json');
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
  console.log(`[4] Wrote ${outFile}`);
  console.log(`[5] Flow details: ${flowDetails.length}; message details: ${counters.messageDetails}.`);
}

main().catch((error) => {
  console.error(`[!] ${error.stack || error.message}`);
  process.exit(1);
});
