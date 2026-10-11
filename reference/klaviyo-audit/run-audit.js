// One-shot read-only Klaviyo audit for a single account.
//
// Usage:
//   node run-audit.js                 # slug auto-named from the account, e.g. acme-co-2026-10-02
//   node run-audit.js acme-co-oct     # explicit slug
//   node run-audit.js --no-render     # skip the flow-email PNG renders
//   node run-audit.js --steps 31,38   # rerun only these steps (needs an existing snapshot from 24)
//   node run-audit.js --fresh         # empty this account's output folder first
//
// Asks for the private API key once (input hidden), checks it against /accounts,
// then runs every step with the key passed through the environment. The key lives
// only in this process's memory: it is never written to disk or printed.
//
// Writes:
//   output/{slug}/account-audit-api.json   (+ forms, list growth, cohorts, performance,
//   output/{slug}/run.json                     template weight, flow-email-renders/)

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const REVISION = process.env.KLAVIYO_REVISION || '2026-04-15';
const args = process.argv.slice(2);
const RENDER = !args.includes('--no-render');
const FRESH = args.includes('--fresh');
const SLUG_ARG = args.find((a, i) => !a.startsWith('--') && args[i - 1] !== '--steps');

const STEPS = [
  { id: '24', file: '24-fetch-account-audit-api.js', label: 'Account snapshot (flows, campaigns, lists, segments, forms, templates)', required: true },
  { id: '31', file: '31-fetch-form-details.js', label: 'Signup form details' },
  { id: '34', file: '34-fetch-performance-cross-review.js', label: 'Flow and campaign performance (90 and 365 days)' },
  { id: '35', file: '35-fetch-q4-baseline.js', label: 'Q4 / BFCM baseline (last two holiday seasons)' },
  { id: '37', file: '37-audit-live-template-weight.js', label: 'Live template weight (clipping risk)' },
  { id: '38', file: '38-fetch-account-totals.js', label: 'Revenue, list health, deliverability, forms, campaigns, repeat purchase' },
  { id: '40', file: '../email-archive/fetch-klaviyo-archive.js', label: 'Email archive: every campaign ever sent, with HTML and performance' },
  { id: '29',file: '29-render-all-flow-emails.js', label: 'Render every flow email to PNG', render: true },
];
const STEPS_ARG = (() => {
  const i = args.indexOf('--steps');
  return i >= 0 && args[i + 1] ? args[i + 1].split(',').map((s) => s.trim()) : null;
})();

function slugify(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'klaviyo-account';
}

function readHidden(prompt) {
  if (process.env.KLAVIYO_PRIVATE_API_KEY) return Promise.resolve(process.env.KLAVIYO_PRIVATE_API_KEY.trim());
  const stdin = process.stdin;
  if (!stdin.isTTY) {
    return new Promise((resolve) => {
      const chunks = [];
      stdin.on('data', (c) => chunks.push(Buffer.from(c)));
      stdin.on('end', () => resolve(Buffer.concat(chunks).toString('utf8').trim()));
    });
  }
  process.stderr.write(prompt);
  return new Promise((resolve) => {
    let value = '';
    stdin.setRawMode(true);
    stdin.resume();
    stdin.setEncoding('utf8');
    const onData = (chunk) => {
      for (const ch of chunk) {
        if (ch === '\r' || ch === '\n') {
          stdin.setRawMode(false);
          stdin.pause();
          stdin.removeListener('data', onData);
          process.stderr.write('\n');
          resolve(value.trim());
          return;
        }
        if (ch === '\u0003') { process.stderr.write('\n'); process.exit(130); }
        if (ch === '\u007f' || ch === '\b') { value = value.slice(0, -1); continue; }
        value += ch;
      }
      process.stderr.write(`\r${prompt}${'*'.repeat(Math.min(value.length, 40))}`);
    };
    stdin.on('data', onData);
  });
}

async function checkKey(key) {
  const res = await fetch('https://a.klaviyo.com/api/accounts', {
    headers: { Authorization: `Klaviyo-API-Key ${key}`, revision: REVISION, accept: 'application/vnd.api+json' },
  });
  if (res.status === 401 || res.status === 403) {
    throw new Error(`Klaviyo rejected that key (${res.status}). Check it was copied in full and has read access to Accounts.`);
  }
  if (!res.ok) throw new Error(`Klaviyo /accounts returned ${res.status}`);
  const body = await res.json();
  const attrs = body?.data?.[0]?.attributes || {};
  return {
    id: body?.data?.[0]?.id,
    name: attrs.contact_information?.organization_name || attrs.contact_information?.default_sender_name || 'klaviyo-account',
    website: attrs.contact_information?.website_url || null,
    timezone: attrs.timezone || null,
    currency: attrs.preferred_currency || null,
  };
}

function runStep(step, env) {
  return new Promise((resolve) => {
    const started = Date.now();
    const child = spawn(process.execPath, [path.join(__dirname, step.file)], {
      cwd: __dirname,
      env,
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    child.on('close', (code) => resolve({ file: step.file, label: step.label, ok: code === 0, code, seconds: Math.round((Date.now() - started) / 1000) }));
  });
}

async function main() {
  const key = await readHidden('Paste the Klaviyo private API key (hidden): ');
  if (!key) throw new Error('No key entered.');
  if (!key.startsWith('pk_')) throw new Error('That does not look like a private key (they start with pk_). The public Site ID will not work.');

  console.log('\nChecking the key...');
  const account = await checkKey(key);
  const slug = SLUG_ARG || `${slugify(account.name)}-${new Date().toISOString().slice(0, 10)}`;
  const outDir = path.join(__dirname, 'output', slug);
  fs.mkdirSync(outDir, { recursive: true });
  console.log(`Key OK: ${account.name}${account.website ? ` (${account.website})` : ''}`);
  console.log(`Output: output/${slug}/\n`);

  const env = { ...process.env, KLAVIYO_PRIVATE_API_KEY: key, ACCOUNT_SLUG: slug, AUDIT_RUNS_ARCHIVE: '1' };
  const results = [];
  const steps = STEPS.filter((s) => (STEPS_ARG ? STEPS_ARG.includes(s.id) : RENDER || !s.render));
  if (!steps.length) throw new Error(`No steps match --steps ${STEPS_ARG}. Valid: ${STEPS.map((s) => s.id).join(', ')}`);
  if (FRESH) {
    if (!steps.some((s) => s.id === '24')) throw new Error('--fresh clears the snapshot, so it needs step 24 in the run.');
    const old = fs.readdirSync(outDir);
    for (const name of old) fs.rmSync(path.join(outDir, name), { recursive: true, force: true });
    if (old.length) console.log(`Cleared ${old.length} old file(s) from output/${slug}/\n`);
  }
  if (!steps.some((s) => s.id === '24') && !fs.existsSync(path.join(outDir, 'account-audit-api.json'))) {
    throw new Error(`output/${slug}/ has no account snapshot yet. Run step 24 first (or the full audit).`);
  }
  for (const [i, step] of steps.entries()) {
    console.log(`\n=== Step ${i + 1}/${steps.length}: ${step.label} ===`);
    const result = await runStep(step, env);
    results.push(result);
    console.log(result.ok ? `--- done in ${result.seconds}s` : `--- FAILED (exit ${result.code}) after ${result.seconds}s`);
    if (!result.ok && step.required) {
      console.log('\nThe account snapshot failed, and every later step depends on it. Stopping.');
      break;
    }
  }

  const runFile = path.join(outDir, 'run.json');
  let history = [];
  try { history = JSON.parse(fs.readFileSync(runFile, 'utf8')).history || []; } catch (_) { /* first run */ }
  history.push({ ranAt: new Date().toISOString(), steps: results });
  fs.writeFileSync(runFile, JSON.stringify({ slug, account, revision: REVISION, history }, null, 2));

  console.log('\n=== Summary ===');
  for (const r of results) console.log(`${r.ok ? 'ok  ' : 'FAIL'}  ${r.label}`);
  console.log(`\nFiles: output/${slug}/`);
  if (results.some((r) => !r.ok)) process.exitCode = 1;
}

main().catch((err) => {
  console.error(`\n[!] ${err.message}`);
  process.exit(1);
});
