// The daily run: import -> analyze -> call list -> follow-ups -> draft batches for approval.
const fs = require('node:fs');
const { parseCsv, toCsv } = require('./csv');
const { importLeads } = require('./leads');
const { analyzeDeal } = require('./deal');
const { dueFollowUps } = require('./followup');
const { draftBatch } = require('./outreach');
const { CALL_SCRIPT, fill, varsFor } = require('./messages');
const { parsePhoneList } = require('./compliance');

const usd = (n) => (n == null ? '' : `$${Math.round(n).toLocaleString('en-US')}`);

function importFile(store, csvPath, defaults = {}) {
  const rows = parseCsv(fs.readFileSync(csvPath, 'utf8'));
  const res = importLeads(rows, { defaults, existing: store.data.leads });
  store.data.leads.push(...res.leads);
  store.log('import', { file: csvPath, added: res.leads.length, duplicates: res.duplicates, skipped: res.skipped });
  return res;
}

function loadDnc(store, filePath) {
  const set = parsePhoneList(fs.readFileSync(filePath, 'utf8'));
  store.data.dnc = [...new Set([...(store.data.dnc || []), ...set])];
  store.log('dnc_loaded', { file: filePath, numbers: set.size });
  return set.size;
}

function analyzeAll(store, settings = {}) {
  let n = 0;
  for (const lead of store.data.leads) {
    if (['dead', 'opted_out', 'closed'].includes(lead.stage)) continue;
    lead.analysis = analyzeDeal(lead, { ...settings.deal, comps: lead.comps || [], askingPrice: lead.askingPrice ?? null });
    n++;
  }
  return n;
}

// Your call list for today: highest score first, with the numbers you need on the call.
function callList(store, settings = {}, limit = 50) {
  return store.data.leads
    .filter((l) => ['new', 'contacted', 'not_now'].includes(l.stage) && l.phones && l.phones.length)
    .filter((l) => !(store.data.optOuts || []).includes(l.phones[0]) && !(store.data.dnc || []).includes(l.phones[0]))
    .filter((l) => !l.analysis || l.analysis.grade !== 'pass')
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map((l) => ({
      id: l.id,
      score: l.score,
      contact: l.contactName || l.ownerName,
      phone: l.phones[0],
      address: `${l.address}, ${l.city} ${l.state}`,
      why: (l.reasons || []).join('; '),
      arv: usd(l.analysis && l.analysis.arv),
      repairs: usd(l.analysis && l.analysis.repairs),
      openingOffer: usd(l.analysis && l.analysis.openingOffer),
      maxOffer: usd(l.analysis && l.analysis.mao),
      opener: fill(CALL_SCRIPT[0], varsFor(l, settings)),
    }));
}

function writeCallList(store, outPath, settings = {}) {
  const list = callList(store, settings);
  fs.writeFileSync(outPath, toCsv(list));
  return list.length;
}

// Drafts (never sends) the day's batches: first-touch letters for new probate leads, and due follow-ups.
function draftDaily(store, settings = {}) {
  const drafted = [];
  const fresh = store.data.leads.filter((l) => l.stage === 'new' && (!l.analysis || l.analysis.grade !== 'pass') && !l.letterDrafted);
  if (fresh.length) {
    const b = draftBatch(store, { leadIds: fresh.map((l) => l.id), channel: 'mail', settings });
    fresh.forEach((l) => { l.letterDrafted = b.id; });
    drafted.push(b);
  }
  const due = dueFollowUps(store.data.leads);
  for (const channel of ['sms_followup', 'email', 'mail']) {
    const ids = due.filter((d) => d.channel === channel).map((d) => d.leadId);
    if (ids.length) drafted.push(draftBatch(store, { leadIds: ids, channel, settings }));
  }
  return drafted;
}

function runDaily(store, settings = {}) {
  const analyzed = analyzeAll(store, settings);
  const batches = draftDaily(store, settings);
  store.save();
  return { analyzed, batches: batches.map((b) => ({ id: b.id, channel: b.channel, items: b.items.length, excluded: b.excluded.length, status: b.status })) };
}

module.exports = { importFile, loadDnc, analyzeAll, callList, writeCallList, draftDaily, runDaily };
