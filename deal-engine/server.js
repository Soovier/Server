// Deal engine dashboard and API.
//   DEAL_ENGINE_TOKEN=... node deal-engine/server.js
// Open http://localhost:3002/?token=... to see the call list and approve batches with one tap.
const crypto = require('node:crypto');
const express = require('express');
const { Store } = require('./src/store');
const { approveBatch, rejectBatch, sendBatch } = require('./src/outreach');
const { senders } = require('./src/providers');
const { callList, runDaily } = require('./src/pipeline');
const { purchaseAgreement, missingFields } = require('./src/contracts');
const { toCsv } = require('./src/csv');
const settings = require('./src/settings');

const TOKEN = process.env.DEAL_ENGINE_TOKEN;
const PORT = Number(process.env.DEAL_ENGINE_PORT || 3002);
const HOST = process.env.DEAL_ENGINE_HOST || '127.0.0.1';
const STAGES = ['new', 'contacted', 'talking', 'not_now', 'offer_sent', 'under_contract', 'assigned', 'closed', 'dead', 'opted_out'];

function createApp(store = new Store(), { token = TOKEN } = {}) {
  if (!token) throw new Error('Set DEAL_ENGINE_TOKEN to a long random string before starting the dashboard.');
  const app = express();
  app.use(express.urlencoded({ extended: false }));
  app.use(express.json());

  // Twilio inbound texts: STOP opts out; any other reply marks the seller as engaged.
  app.post('/webhooks/twilio/sms', (req, res) => {
    if (!validTwilio(req)) return res.status(403).end();
    const from = req.body.From;
    const body = String(req.body.Body || '').trim();
    const lead = store.data.leads.find((l) => (l.phones || []).includes(from));
    if (/^(stop|stopall|unsubscribe|cancel|end|quit)$/i.test(body)) {
      store.optOut(from, 'STOP reply');
      if (lead) store.setStage(lead.id, 'opted_out', 'replied STOP');
    } else if (lead) {
      lead.consent = true; // they started the conversation; a callback is now expected
      lead.lastReply = body;
      store.setStage(lead.id, 'talking', `texted: ${body.slice(0, 200)}`);
    } else {
      store.log('sms_unknown', { from, body: body.slice(0, 200) });
    }
    store.save();
    res.type('text/xml').send('<Response></Response>');
  });

  const auth = (req, res, next) => {
    const given = String(req.query.token || req.get('x-deal-token') || '');
    const ok = given.length === token.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(token));
    return ok ? next() : res.status(401).send('Unauthorized');
  };
  app.use(auth);
  const q = (req) => `?token=${encodeURIComponent(req.query.token || '')}`;

  app.get('/', (req, res) => res.send(dashboard(store, q(req))));

  app.post('/run', (req, res) => { runDaily(store, settings); res.redirect(`/${q(req)}`); });

  app.post('/batches/:id/approve', async (req, res) => {
    try {
      approveBatch(store, req.params.id, settings.myName || 'owner');
      await sendBatch(store, req.params.id, senders());
      store.save();
      res.redirect(`/${q(req)}`);
    } catch (e) { res.status(400).send(escape(e.message)); }
  });

  app.post('/batches/:id/reject', (req, res) => {
    try { rejectBatch(store, req.params.id, settings.myName || 'owner'); store.save(); res.redirect(`/${q(req)}`); }
    catch (e) { res.status(400).send(escape(e.message)); }
  });

  app.get('/batches/:id/print.txt', (req, res) => {
    const b = store.batch(req.params.id);
    if (!b) return res.status(404).end();
    res.type('text/plain').send(b.items.map((i) => `TO: ${i.name}\n${i.to}\n\n${i.body}`).join('\n\n\f\n'));
  });

  app.get('/call-list.csv', (req, res) => res.type('text/csv').send(toCsv(callList(store, settings))));

  app.post('/leads/:id/stage', (req, res) => {
    const lead = store.lead(req.params.id);
    if (!lead) return res.status(404).send('No such lead');
    if (!STAGES.includes(req.body.stage)) return res.status(400).send('Unknown stage');
    if (req.body.askingPrice) lead.askingPrice = Number(String(req.body.askingPrice).replace(/[$,]/g, '')) || null;
    if (req.body.stage === 'opted_out') (lead.phones || []).forEach((p) => store.optOut(p, 'marked on call'));
    store.setStage(lead.id, req.body.stage, req.body.note || '');
    store.save();
    res.redirect(`/${q(req)}`);
  });

  app.get('/leads/:id/contract.md', (req, res) => {
    const lead = store.lead(req.params.id);
    if (!lead) return res.status(404).end();
    const price = Number(req.query.price) || (lead.analysis && lead.analysis.openingOffer);
    const text = purchaseAgreement(lead, { price, settings });
    const missing = missingFields(text);
    res.type('text/markdown').send(missing.length ? `<!-- Fill before signing: ${missing.join(', ')} -->\n${text}` : text);
  });

  app.get('/api/state', (req, res) => res.json({
    counts: countBy(store.data.leads, 'stage'),
    pending: store.data.batches.filter((b) => b.status === 'pending_approval').map(({ items, ...b }) => ({ ...b, items: items.length })),
  }));

  return app;
}

function validTwilio(req) {
  const tokenTw = process.env.TWILIO_AUTH_TOKEN;
  const url = process.env.TWILIO_WEBHOOK_URL;
  if (!tokenTw || !url) return false; // refuse unsigned traffic
  const data = url + Object.keys(req.body).sort().map((k) => k + req.body[k]).join('');
  const expected = crypto.createHmac('sha1', tokenTw).update(data).digest('base64');
  const given = String(req.get('x-twilio-signature') || '');
  return given.length === expected.length && crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
}

const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const countBy = (arr, k) => arr.reduce((a, x) => ({ ...a, [x[k]]: (a[x[k]] || 0) + 1 }), {});

function dashboard(store, qs) {
  const pending = store.data.batches.filter((b) => b.status === 'pending_approval');
  const counts = countBy(store.data.leads, 'stage');
  const calls = callList(store, settings, 25);
  const live = process.env.DEAL_ENGINE_LIVE === '1';
  const batchCard = (b) => `
    <div class="card">
      <b>${escape(b.channel)}</b> · ${b.items.length} recipients · ${b.excluded.length} blocked by compliance
      <details><summary>Preview</summary>${b.items.slice(0, 5).map((i) => `<pre>${escape(i.name)} (${escape(i.to)})\n${escape(i.body)}</pre>`).join('')}
        ${b.excluded.length ? `<p>Blocked: ${b.excluded.slice(0, 10).map((x) => `${escape(x.address)}: ${escape(x.reasons.join(', '))}`).join('<br>')}</p>` : ''}
        ${[...new Set(b.items.flatMap((i) => i.warnings))].map((w) => `<p class="warn">${escape(w)}</p>`).join('')}
      </details>
      <form method="post" action="/batches/${b.id}/approve${qs}"><button class="go">Approve${b.channel === 'mail' ? ' (then print)' : ''}</button></form>
      <form method="post" action="/batches/${b.id}/reject${qs}"><button>Reject</button></form>
      ${b.channel === 'mail' ? `<a href="/batches/${b.id}/print.txt${qs}">Letters to print</a>` : ''}
    </div>`;
  const stageForm = (l) => `<form method="post" action="/leads/${l.id}/stage${qs}">
      <select name="stage">${STAGES.map((s) => `<option>${s}</option>`).join('')}</select>
      <input name="askingPrice" placeholder="their price" size="8"><input name="note" placeholder="note" size="14"><button>Save</button></form>`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deal engine</title><style>
body{font:15px system-ui;margin:16px;max-width:1100px;color:#1d1d1f;background:#fafafa}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:8px 0}
form{display:inline}button{padding:8px 14px;border-radius:6px;border:1px solid #bbb;background:#fff;cursor:pointer}
.go{background:#1a7f37;color:#fff;border-color:#1a7f37;font-weight:600}.warn{color:#9a6700}
table{border-collapse:collapse;width:100%;background:#fff}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left;vertical-align:top}
pre{white-space:pre-wrap;background:#f4f4f4;padding:8px;border-radius:6px}
</style></head><body>
<h1>Deal engine</h1>
<p>${live ? '<b>LIVE:</b> approved texts and AI calls really send.' : 'Dry run: approvals are recorded but nothing is sent. Set DEAL_ENGINE_LIVE=1 to go live.'}</p>
<p>${Object.entries(counts).map(([k, v]) => `${escape(k)}: ${v}`).join(' · ') || 'No leads yet. Import a list with the CLI.'}</p>
<form method="post" action="/run${qs}"><button>Run today's analysis and drafts</button></form>
<h2>Waiting for your tap (${pending.length})</h2>${pending.map(batchCard).join('') || '<p>Nothing to approve.</p>'}
<h2>Your call list</h2><p><a href="/call-list.csv${qs}">Download CSV</a></p>
<table><tr><th>Score</th><th>Who</th><th>Property</th><th>Numbers</th><th>Update</th></tr>
${calls.map((c) => `<tr><td>${c.score}</td><td>${escape(c.contact)}<br><a href="tel:${escape(c.phone)}">${escape(c.phone)}</a></td>
<td>${escape(c.address)}<br><small>${escape(c.why)}</small></td>
<td>ARV ${escape(c.arv)}<br>Repairs ${escape(c.repairs)}<br>Open ${escape(c.openingOffer)} · Max ${escape(c.maxOffer)}<br><a href="/leads/${c.id}/contract.md${qs}">Contract draft</a></td>
<td>${stageForm(store.lead(c.id))}</td></tr>`).join('')}</table>
</body></html>`;
}

if (require.main === module) {
  const app = createApp();
  app.listen(PORT, HOST, () => console.log(`Deal engine on http://${HOST}:${PORT}/?token=<DEAL_ENGINE_TOKEN>`));
}

module.exports = { createApp };
