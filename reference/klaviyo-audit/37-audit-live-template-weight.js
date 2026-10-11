// Read-only live-flow template weight audit.
// Uses the account-audit snapshot to discover template IDs, then fetches template HTML.
// Stores only structural measurements and small text/code samples, not full HTML.

const fs = require('fs');
const path = require('path');

const API_BASE = 'https://a.klaviyo.com/api';
const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const ACCOUNT_SLUG = process.env.ACCOUNT_SLUG || `account-api-${new Date().toISOString().slice(0, 10)}`;
const OUT_DIR = path.join(__dirname, 'output', ACCOUNT_SLUG);
const AUDIT_PATH = path.join(OUT_DIR, 'account-audit-api.json');
let API_KEY = process.env.KLAVIYO_PRIVATE_API_KEY || '';

async function readSecret() {
  if (API_KEY.trim()) return API_KEY.trim();
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString('utf8').trim();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function getTemplate(templateId) {
  for (let attempt = 1; attempt <= 6; attempt += 1) {
    await sleep(140);
    const response = await fetch(`${API_BASE}/templates/${templateId}`, {
      headers: {
        Authorization: `Klaviyo-API-Key ${API_KEY}`,
        accept: 'application/vnd.api+json',
        revision: REVISION,
      },
    });
    const text = await response.text();
    let json;
    try { json = text ? JSON.parse(text) : null; } catch (_) { json = { rawText: text.slice(0, 1000) }; }
    if (response.ok) return json;
    if (response.status !== 429 || attempt === 6) {
      const error = new Error(`template ${templateId} failed (${response.status})`);
      error.details = json;
      throw error;
    }
    await sleep(1000 * attempt);
  }
  throw new Error(`template ${templateId} failed`);
}

function walk(value, visit) {
  if (!value || typeof value !== 'object') return;
  visit(value);
  if (Array.isArray(value)) {
    for (const item of value) walk(item, visit);
  } else {
    for (const child of Object.values(value)) walk(child, visit);
  }
}

function discoverTemplates(audit) {
  const templates = new Map();
  for (const detail of audit.flows?.details || []) {
    if (detail.flow?.attributes?.status !== 'live') continue;
    const flow = { id: detail.flow.id, name: detail.flow.attributes?.name || '' };
    walk(detail.definition, (value) => {
      if (!value.template_id) return;
      const current = templates.get(value.template_id) || { templateId: value.template_id, messages: [] };
      current.messages.push({
        flowId: flow.id,
        flowName: flow.name,
        messageId: value.id || null,
        messageName: value.name || null,
        messageStatus: value.status || null,
        subject: value.subject || null,
        previewText: value.preview_text || null,
      });
      templates.set(value.template_id, current);
    });
  }
  for (const template of templates.values()) {
    template.messages = [...new Map(template.messages.map((message) => [
      `${message.flowId}:${message.messageId}:${message.messageName}`,
      message,
    ])).values()];
  }
  return [...templates.values()];
}

function htmlFromTemplate(template) {
  const attrs = template?.data?.attributes || {};
  return attrs.html || attrs.content || attrs.body || attrs.editor_html || '';
}

function stripHtml(html) {
  return String(html || '')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

function codeSamples(html) {
  const matches = String(html || '').match(/(?:\{\{|\{%)[\s\S]{0,180}?(?:\}\}|%\})/g) || [];
  return [...new Set(matches.map((match) => match.replace(/\s+/g, ' ').trim()))].slice(0, 20);
}

async function main() {
  API_KEY = await readSecret();
  if (!API_KEY) throw new Error('KLAVIYO_PRIVATE_API_KEY is required');
  if (!fs.existsSync(AUDIT_PATH)) throw new Error(`Missing ${AUDIT_PATH}`);
  const audit = JSON.parse(fs.readFileSync(AUDIT_PATH, 'utf8'));
  const discovered = discoverTemplates(audit);
  console.log(`[templates] ${discovered.length} unique templates in live flows`);

  const templates = [];
  for (const entry of discovered) {
    try {
      const template = await getTemplate(entry.templateId);
      const html = String(htmlFromTemplate(template) || '');
      const text = stripHtml(html);
      templates.push({
        ...entry,
        templateName: template?.data?.attributes?.name || null,
        htmlBytes: Buffer.byteLength(html, 'utf8'),
        textChars: text.length,
        imageCount: (html.match(/<img\b/gi) || []).length,
        linkCount: (html.match(/<a\b/gi) || []).length,
        dynamicCodeCount: (html.match(/\{\{|\{%/g) || []).length,
        overGmailClipThreshold: Buffer.byteLength(html, 'utf8') > 102_400,
        textSample: text.slice(0, 500),
        dynamicCodeSamples: codeSamples(html),
      });
    } catch (error) {
      templates.push({ ...entry, error: { message: error.message, details: error.details || null } });
    }
  }

  const out = {
    capturedAt: new Date().toISOString(),
    revision: REVISION,
    accountSlug: ACCOUNT_SLUG,
    uniqueLiveTemplates: templates.length,
    overGmailClipThreshold: templates.filter((template) => template.overGmailClipThreshold).length,
    errors: templates.filter((template) => template.error).length,
    templates,
  };
  const outFile = path.join(OUT_DIR, 'live-template-weight-audit.json');
  fs.writeFileSync(outFile, JSON.stringify(out, null, 2));
  console.log(`[done] ${outFile}`);
}

main().catch((error) => {
  console.error(`[!] ${error.stack || error.message}`);
  process.exit(1);
});
