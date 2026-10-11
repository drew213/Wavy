// Read-only Klaviyo form detail pull.
//
// Usage:
//   ACCOUNT_SLUG=client-2026-10-02 node 31-fetch-form-details.js
//
// Writes:
//   output/{ACCOUNT_SLUG}/form-details.json

const fs = require('fs');
const path = require('path');
const readline = require('readline');

const API_BASE = 'https://a.klaviyo.com/api';
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
const AUDIT_PATH = path.join(OUT_DIR, 'account-audit-api.json');

let API_KEY = process.env.KLAVIYO_PRIVATE_API_KEY || '';

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

function apiUrl(pathname, params = {}) {
  const url = new URL(`${API_BASE}${pathname}`);
  for (const [key, value] of Object.entries(params)) {
    if (value == null || value === '') continue;
    url.searchParams.set(key, value);
  }
  return url.toString();
}

async function request(url, label) {
  const response = await fetch(url, {
    headers: {
      Authorization: `Klaviyo-API-Key ${API_KEY}`,
      accept: 'application/vnd.api+json',
      revision: REVISION,
    },
  });
  const text = await response.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch (_) {
    json = { rawText: text.slice(0, 1200) };
  }
  if (!response.ok) {
    return { ok: false, label, status: response.status, error: json };
  }
  return { ok: true, value: json };
}

async function get(pathname, params = {}, label = pathname) {
  return request(apiUrl(pathname, params), label);
}

async function getPaginated(pathname, params = {}, label = pathname, maxPages = 10) {
  const pages = [];
  let url = apiUrl(pathname, params);
  while (url && pages.length < maxPages) {
    const page = await request(url, `${label} page ${pages.length + 1}`);
    pages.push(page);
    if (!page.ok) break;
    url = page.value?.links?.next || null;
  }
  return pages;
}

function dataItems(pageOrPages) {
  const pages = Array.isArray(pageOrPages) ? pageOrPages : [pageOrPages];
  return pages.flatMap((page) => {
    if (!page?.ok) return [];
    const data = page.value?.data;
    if (Array.isArray(data)) return data;
    return data ? [data] : [];
  });
}

(async () => {
  API_KEY = await readStdinSecret();
  if (!API_KEY) throw new Error('KLAVIYO_PRIVATE_API_KEY is required');
  if (!fs.existsSync(AUDIT_PATH)) throw new Error(`Missing ${AUDIT_PATH}`);
  const audit = JSON.parse(fs.readFileSync(AUDIT_PATH, 'utf8'));
  const forms = audit.forms?.items || [];
  const details = [];

  for (const form of forms) {
    console.log(`[form] ${form.id} ${form.attributes?.name || ''}`);
    // The full definition (steps, copy, offer, fields) is an additional field; fall back to the plain record.
    let formDetail = await get(`/forms/${form.id}`, { 'additional-fields[form]': 'definition' }, `form ${form.id}`);
    if (formDetail?.ok === false) formDetail = await get(`/forms/${form.id}`, {}, `form ${form.id}`);
    const versionsPages = await getPaginated(`/forms/${form.id}/form-versions`, { 'page[size]': 50 }, `versions ${form.id}`, 5);
    const versions = dataItems(versionsPages);
    const versionDetails = [];
    for (const version of versions) {
      const versionDetail = await get(`/form-versions/${version.id}`, {}, `form-version ${version.id}`);
      versionDetails.push({ stub: version, detail: versionDetail });
    }
    details.push({
      form,
      formDetail,
      versions,
      versionDetails,
      versionErrors: versionsPages.filter((page) => !page.ok),
    });
  }

  const out = {
    capturedAt: new Date().toISOString(),
    revision: REVISION,
    count: details.length,
    details,
  };
  const outFile = path.join(OUT_DIR, 'form-details.json');
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
  console.log(`[done] ${outFile}`);
})();
