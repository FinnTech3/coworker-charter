// Draws docs/decision-order.png and docs/social-preview.png from real runs of charter_check.py.
// Run by build.sh. Env: WORK (scratch folder with the scripts and charter.json), ROOT (repo root), PLAYWRIGHT_DIR.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const { chromium } = createRequire(path.resolve(process.env.PLAYWRIGHT_DIR || '.') + '/')('playwright');

const WORK = process.env.WORK;
const ROOT = process.env.ROOT;
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// A suspended copy of the charter, for row 1.
const charter = JSON.parse(fs.readFileSync(path.join(WORK, 'charter.json'), 'utf8'));
fs.writeFileSync(path.join(WORK, 'suspended.json'), JSON.stringify({ ...charter, status: 'suspended' }, null, 2));

const run = (file, tool, action, extra = []) => {
  const args = ['charter_check.py', file, '--tool', tool, '--action', action, ...extra, '--json'];
  let out;
  try { out = execFileSync('python3', args, { cwd: WORK, encoding: 'utf8' }); } catch (e) { out = e.stdout; }
  return JSON.parse(out);
};
const SPENT = ['--spent-today', '8', '--spent-month', '8'];
const rows = [
  { n: 1, q: 'Is the charter suspended?', note: 'The owner\'s kill switch.', r: run('suspended.json', 'helpdesk.search', 'Search open tickets'), shown: 'charter status: "suspended"  +  helpdesk.search "Search open tickets"' },
  { n: 2, q: 'Is the tool in the charter?', note: 'A near miss gets a did-you-mean.', r: run('charter.json', 'refunds.isue', 'Refund order 1204', ['--amount', '42', ...SPENT]), shown: 'refunds.isue "Refund order 1204"' },
  { n: 3, q: 'Does the action text touch a data boundary?', note: 'must_not_access and must_not_share phrases.', r: run('charter.json', 'helpdesk.search', 'Read the payroll spreadsheet'), shown: 'helpdesk.search "Read the payroll spreadsheet"' },
  { n: 4, q: 'Does a never rule fire?', note: 'Things that must never happen.', r: run('charter.json', 'helpdesk.delete_ticket', 'Delete all tickets from 2024'), shown: 'helpdesk.delete_ticket "Delete all tickets from 2024"' },
  { n: 5, q: 'Would the amount pass a spend cap?', note: 'Equal to the cap is allowed. Over it is not.', r: run('charter.json', 'refunds.issue', 'Refund order 1190', ['--amount', '140', ...SPENT]), shown: 'refunds.issue "Refund order 1190" --amount 140' },
  { n: 6, q: 'Do approval rules fire?', note: 'deny beats everything; a rule naming the tool beats one naming the tier; then the stricter wins.', r: run('charter.json', 'refunds.issue', 'Refund order 1204', ['--amount', '42', ...SPENT]), shown: 'refunds.issue "Refund order 1204" --amount 42' },
  { n: 7, q: 'Nothing fired?', note: 'A read tool is allowed. Any other tier asks.', r: run('charter.json', 'helpdesk.search', 'Search open tickets'), shown: 'helpdesk.search "Search open tickets"' },
];
const missing = run('charter.json', 'refunds.issue', 'Refund order 5');
const want = ['charter-suspended', 'unknown-tool', 'data-boundary', 'N2', 'budget.per_action_max', 'A5', 'read-default'];
rows.forEach((row, i) => { if (row.r.rule !== want[i]) throw new Error(`row ${row.n}: got ${row.r.rule}, expected ${want[i]}`); });
if (missing.rule !== 'unverifiable') throw new Error('missing-info row changed');

const chip = (d) => `<span class="chip c-${d}">${d}</span>`;
const exitOf = (d) => (d === 'allow' ? 0 : 1);
const rowHtml = rows.map((row) => `
  <div class="row">
    <div class="num">${row.n}</div>
    <div class="q"><b>${esc(row.q)}</b><span>${esc(row.note)}</span></div>
    <div class="ex"><code>${esc(row.shown)}</code></div>
    <div class="res">${chip(row.r.decision)}<code class="rule">${esc(row.r.rule)}</code><em>exit ${exitOf(row.r.decision)}</em></div>
    <div class="why">${esc(row.r.reason)}</div>
  </div>`).join('<div class="down">no match: next</div>');

const html = `<style>
*{box-sizing:border-box}body{margin:0;background:#16101a;color:#f1ecf4;font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}
.wrap{width:1180px;padding:42px 44px 36px}
h1{margin:0 0 6px;font-size:34px;letter-spacing:-.3px}.sub{color:#b9aec2;font-size:17px;margin-bottom:26px;line-height:1.45}
.row{display:grid;grid-template-columns:44px 320px 1fr 330px;grid-template-areas:'num q ex res' '. . why why';column-gap:16px;row-gap:6px;background:#221828;border:1px solid #3a2a42;border-radius:12px;padding:14px 18px;align-items:center}
.num{grid-area:num;width:34px;height:34px;border-radius:50%;background:#7a1031;color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center}
.q{grid-area:q;display:flex;flex-direction:column;gap:3px}.q b{font-size:17px}.q span{color:#a99bb3;font-size:13px;line-height:1.35}
.ex{grid-area:ex}.ex code{font:13px 'DejaVu Sans Mono',Menlo,monospace;color:#e9deef;word-break:break-word}
.res{grid-area:res;display:flex;align-items:center;gap:10px;justify-content:flex-end}
.rule{font:600 13px 'DejaVu Sans Mono',Menlo,monospace;color:#fbbf24}.res em{font-style:normal;color:#a99bb3;font-size:12px;white-space:nowrap}.miss .res{grid-area:auto}
.why{grid-area:why;color:#cdbfd6;font-size:14px;padding-left:2px}
.chip{display:inline-block;min-width:64px;text-align:center;font:700 13px 'DejaVu Sans Mono',monospace;border-radius:7px;padding:4px 10px;text-transform:uppercase;letter-spacing:.5px}
.c-deny{background:#7f1d2d;color:#ffd4da}.c-ask{background:#7a5a0c;color:#ffe9a8}.c-allow{background:#14532d;color:#c4f5d3}
.down{margin:5px 0 5px 36px;color:#7d6e88;font-size:12px;letter-spacing:.6px;text-transform:uppercase}.down:before{content:'\\2193  '}
.miss{margin-top:20px;border:1px dashed #6b4a78;border-radius:12px;padding:14px 18px;display:grid;grid-template-columns:1fr 330px;gap:16px;align-items:center}
.miss b{font-size:16px}.miss div span{display:block;color:#a99bb3;font-size:13px;margin-top:3px}
.foot{color:#8d7e98;font-size:13px;margin-top:14px}
</style>
<div class="wrap" id="wrap">
<h1>One order of decisions. First match wins.</h1>
<div class="sub">Every row below is a real run of charter_check.py against the support charter. Same charter and same inputs give the same answer. Only an allow exits 0.</div>
${rowHtml}
<div class="miss"><div><b>Missing information never opens a door.</b><span>Refund with no amount: <code style="font:12px 'DejaVu Sans Mono',monospace">refunds.issue "Refund order 5"</code>. ${esc(missing.reason)}</span></div>
<div class="res">${chip(missing.decision)}<code class="rule">${esc(missing.rule)}</code><em>exit 1</em></div></div>
<div class="foot">Real output. Rule ids come from the charter (A5, N2) or from the checker (the others). Row 1 uses a copy of the charter with status set to suspended.</div>
</div>`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1180, height: 900 }, deviceScaleFactor: 1.6 });
await page.setContent(html);
await page.locator('#wrap').screenshot({ path: path.join(WORK, 'decision-order.png') });

// Social preview: 1280 x 640.
const logo = fs.readFileSync(path.join(ROOT, 'agensi/media/logo.png')).toString('base64');
const social = await browser.newPage({ viewport: { width: 1280, height: 640 }, deviceScaleFactor: 1 });
await social.setContent(`<style>
*{box-sizing:border-box}body{margin:0;width:1280px;height:640px;background:linear-gradient(135deg,#b91c3c,#3f0416);font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;color:#fff;display:flex;flex-direction:column;justify-content:center;padding:0 90px}
.top{display:flex;align-items:center;gap:36px}img{width:170px;height:170px;border-radius:38px;box-shadow:0 14px 40px rgba(0,0,0,.35)}
h1{margin:0;font-size:84px;letter-spacing:-1.5px;line-height:1}p{margin:14px 0 0;font-size:33px;line-height:1.25;color:#ffe1e7;max-width:780px}
.chips{margin-top:50px;display:flex;gap:22px}.c{background:#16101a;border-radius:14px;padding:18px 26px;font:24px/1.5 'DejaVu Sans Mono',Menlo,monospace;box-shadow:0 14px 40px rgba(0,0,0,.35)}
.c b{display:block;font-size:28px}.allow b{color:#7ee787}.ask b{color:#fbbf24}.deny b{color:#ff7b72}.c small{color:#a99bb3;font-size:20px}
</style><div class="top"><img src="data:image/png;base64,${logo}" alt=""><div><h1>Coworker Charter</h1><p>Write down what your AI coworker may do. It asks before anything risky, and the log shows if anyone edits it.</p></div></div>
<div class="chips">${rows.filter((r) => ['A5', 'budget.per_action_max', 'read-default'].includes(r.r.rule)).sort((a, b) => 'allow ask deny'.indexOf(a.r.decision) - 'allow ask deny'.indexOf(b.r.decision)).map((r) => `<div class="c ${r.r.decision}"><b>${r.r.decision}</b><small>rule ${esc(r.r.rule)}, exit ${exitOf(r.r.decision)}</small></div>`).join('')}</div>`);
await social.screenshot({ path: path.join(WORK, 'social-preview.png') });
await browser.close();
console.log('wrote decision-order.png and social-preview.png');
