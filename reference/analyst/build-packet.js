// Builds the analyst packet for one audited account: a sanitized copy of the audit
// output (no profile-level personal data, no raw Klaviyo profile or order IDs) plus the
// brief, for the ChatGPT analyst.
//
// Usage: node build-packet.js <slug>
// Reads:  ../klaviyo-audit/output/<slug>/, ./dns/<slug>.txt
// Writes: ./<slug>/data/, ./<slug>/renders/, ./<slug>/BRIEF.md (from BRIEF.template.md)

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const slug = process.argv[2];
if (!slug) { console.error('usage: node build-packet.js <slug>'); process.exit(1); }
const SRC = path.join(__dirname, '..', 'klaviyo-audit', 'output', slug);
const DEST = path.join(__dirname, slug);

// Outputs of the current toolkit. Anything else in the folder is left over from an older run.
const FILES = [
  'run.json',
  'account-audit-api.json',
  'form-details.json',
  'performance-cross-review.json',
  'q4-baseline.json',
  'account-totals.json',
  'placed-orders-24mo.json',
  'live-template-weight-audit.json',
];
const PERSONAL_KEYS = new Set(['email', 'phone_number', 'first_name', 'last_name', 'external_id', 'anonymous_id', 'whatsapp_bsuid', 'location', 'image', 'organization', 'title']);

// Profile objects are the only place personal data lives; everything else is account config.
function scrub(node) {
  if (Array.isArray(node)) return node.filter((x) => !(x && x.type === 'profile')).map(scrub);
  if (!node || typeof node !== 'object') return node;
  const out = {};
  for (const [k, v] of Object.entries(node)) {
    if (k === 'profilesSample') continue;
    if (node.type === 'profile' && PERSONAL_KEYS.has(k)) continue;
    out[k] = scrub(v);
  }
  return out;
}

// Same input, same pseudonym within one packet, so repeat-purchase math still works.
const SALT = crypto.randomBytes(16);
const pseudo = (prefix, id) => (id == null ? id : `${prefix}_${crypto.createHmac('sha256', SALT).update(String(id)).digest('hex').slice(0, 12)}`);

fs.rmSync(path.join(DEST, 'data'), { recursive: true, force: true });
fs.mkdirSync(path.join(DEST, 'data'), { recursive: true });
const report = [];
for (const file of FILES) {
  const src = path.join(SRC, file);
  if (!fs.existsSync(src)) { report.push(`${file}: missing`); continue; }
  let data = scrub(JSON.parse(fs.readFileSync(src, 'utf8')));
  if (file === 'placed-orders-24mo.json' && Array.isArray(data.orders)) {
    data.orders = data.orders.map((o) => ({ ...o, p: pseudo('cust', o.p), o: pseudo('ord', o.o) }));
    data.note = 'p (customer) and o (order) are salted pseudonyms of the Klaviyo profile and order IDs: stable within this packet, not reversible.';
  }
  // Staff and personal addresses (sender, reply-to, alert recipients) aren't needed for the analysis:
  // keep role inboxes like support@, mask the rest.
  const ROLE = /^(info|support|hello|help|team|orders?|customer-?service|care|contact|news|noreply|no-reply|marketing|shop|sales)$/i;
  const text = JSON.stringify(data, null, file === 'placed-orders-24mo.json' ? 0 : 1)
    .replace(/([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g, (m, local, domain) => (ROLE.test(local) ? m : `[person]@${domain}`));
  fs.writeFileSync(path.join(DEST, 'data', file), text);
  const emails = [...new Set(text.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [])];
  report.push(`${file}: ${(text.length / 1024).toFixed(0)} KB, email-like strings: ${emails.length ? emails.join(', ') : 'none'}`);
}
const dns = path.join(__dirname, 'dns', `${slug}.txt`);
if (fs.existsSync(dns)) fs.copyFileSync(dns, path.join(DEST, 'data', 'dns-check.txt'));
// Mobile renders plus their metadata are enough for the analyst; desktop PNGs and raw HTML double the size.
const renders = path.join(SRC, 'flow-email-renders');
if (fs.existsSync(renders)) {
  fs.rmSync(path.join(DEST, 'renders'), { recursive: true, force: true });
  fs.mkdirSync(path.join(DEST, 'renders'));
  const keep = fs.readdirSync(renders).filter((f) => f.endsWith('-mobile.png') || f.endsWith('.json'));
  for (const f of keep) fs.copyFileSync(path.join(renders, f), path.join(DEST, 'renders', f));
  report.push(`renders: ${keep.length} files (mobile PNG + metadata)`);
}

// run.json is written when the runner exits; until then, take the account from step 38's output.
let run = fs.existsSync(path.join(SRC, 'run.json')) ? JSON.parse(fs.readFileSync(path.join(SRC, 'run.json'), 'utf8')) : null;
if (!run && fs.existsSync(path.join(SRC, 'account-totals.json'))) {
  const t = JSON.parse(fs.readFileSync(path.join(SRC, 'account-totals.json'), 'utf8'));
  const c = t.account?.contact || {};
  run = {
    account: { name: c.organization_name || slug, website: c.website_url, currency: t.account?.currency, timezone: t.account?.timezone },
    ranAt: t.capturedAt,
    steps: FILES.filter((f) => f !== 'run.json').map((f) => ({ file: f, label: f, ok: fs.existsSync(path.join(SRC, f)) })),
  };
}
const tpl = fs.readFileSync(path.join(__dirname, 'BRIEF.template.md'), 'utf8');
const brief = tpl
  .replaceAll('{{ACCOUNT}}', run?.account?.name || slug)
  .replaceAll('{{WEBSITE}}', run?.account?.website || 'unknown')
  .replaceAll('{{CURRENCY}}', run?.account?.currency || 'unknown')
  .replaceAll('{{TIMEZONE}}', run?.account?.timezone || 'unknown')
  .replaceAll('{{RAN_AT}}', run?.ranAt || 'unknown')
  .replaceAll('{{STEPS}}', (run?.steps || []).map((s) => `- ${s.ok ? 'ok' : 'FAILED'}: ${s.label} (\`${s.file}\`)`).join('\n') || '- run.json missing')
  .replaceAll('{{FILES}}', fs.readdirSync(path.join(DEST, 'data')).map((f) => `- \`data/${f}\``).join('\n'));
fs.writeFileSync(path.join(DEST, 'BRIEF.md'), brief);
console.log(report.join('\n'));
