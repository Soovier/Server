const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseCsv, toCsv } = require('../src/csv');
const { importLeads, normalizePhone } = require('../src/leads');
const { analyzeDeal } = require('../src/deal');
const { checkRecipient, withinCallingHours, parsePhoneList } = require('../src/compliance');
const { Store } = require('../src/store');
const { draftBatch, approveBatch, sendBatch } = require('../src/outreach');
const { importFile, runDaily, callList } = require('../src/pipeline');
const { dueFollowUps } = require('../src/followup');
const { purchaseAgreement, missingFields } = require('../src/contracts');
const { matchBuyers } = require('../src/buyers');

const SAMPLE = path.join(__dirname, '..', 'samples', 'probate-sample.csv');
const tmpStore = () => new Store(fs.mkdtempSync(path.join(os.tmpdir(), 'deal-')));
// 3 PM Central on a Wednesday: inside every calling window.
const AFTERNOON = new Date('2026-11-25T21:00:00Z');
// 11 PM Central: outside every calling window.
const NIGHT = new Date('2026-11-26T05:00:00Z');

test('csv round trip handles quotes and commas', () => {
  const rows = parseCsv('a,b\n"x, y","he said ""hi"""\n');
  assert.deepEqual(rows, [{ a: 'x, y', b: 'he said "hi"' }]);
  assert.equal(toCsv(rows), 'a,b\n"x, y","he said ""hi"""\n');
});

test('imports a probate export, maps executor, merges duplicates, scores stacked lists higher', () => {
  const rows = parseCsv(fs.readFileSync(SAMPLE, 'utf8'));
  const { leads, duplicates } = importLeads([...rows, rows[0]]);
  assert.equal(leads.length, 4);
  assert.equal(duplicates, 1);
  const elm = leads.find((l) => l.address === '1412 Elm Street');
  assert.equal(elm.contactName, 'Linda Jenkins');
  assert.equal(elm.ownerName, 'Harold Jenkins');
  assert.deepEqual(elm.phones, ['+12145550143']);
  const pine = leads.find((l) => l.address === '77 Pine Avenue');
  assert.ok(pine.score > elm.score - 20, 'stacked probate + tax delinquent lead scores well');
  assert.ok(pine.reasons.some((r) => r.includes('stacked')));
  const maple = leads.find((l) => l.address === '3301 Maple Rd');
  assert.ok(maple.reasons.some((r) => r.includes('skip trace')));
});

test('phone normalization', () => {
  assert.equal(normalizePhone('(214) 555-0143'), '+12145550143');
  assert.equal(normalizePhone('1-214-555-0143'), '+12145550143');
  assert.equal(normalizePhone('555-0143'), null);
});

test('deal math: MAO = ARV x 70% - repairs - fee', () => {
  const a = analyzeDeal({ estValue: 198000, sqft: 1450, condition: 'fair' }, { targetFee: 10000 });
  assert.equal(a.repairs, 51000); // 1450 x $35, rounded to $500
  assert.equal(a.buyerPrice, Math.round(198000 * 0.7 - 51000));
  assert.equal(a.mao, a.buyerPrice - 10000);
  assert.ok(a.openingOffer < a.mao);
  const withAsk = analyzeDeal({ estValue: 198000, sqft: 1450, condition: 'fair' }, { askingPrice: 70000 });
  assert.equal(withAsk.grade, 'A');
  const tooHigh = analyzeDeal({ estValue: 198000, sqft: 1450, condition: 'fair' }, { askingPrice: 85000 });
  assert.equal(tooHigh.grade, 'pass');
});

test('deal math flags underwater estates and missing ARV', () => {
  assert.equal(analyzeDeal({}).ok, false);
  const a = analyzeDeal({ estValue: 265000, sqft: 2100, condition: 'good', mortgageBalance: 190000 });
  assert.equal(a.grade, 'pass');
  assert.ok(a.notes.some((n) => n.includes('underwater')));
});

test('compliance: blocks DNC, opt-outs, night calls, and AI calls without consent', () => {
  const lead = { state: 'TX', consent: false };
  const phone = '+12145550143';
  assert.ok(checkRecipient({ lead, phone, channel: 'manual_call', now: AFTERNOON }).ok);
  assert.ok(!checkRecipient({ lead, phone, channel: 'manual_call', now: NIGHT }).ok);
  assert.ok(!checkRecipient({ lead, phone, channel: 'manual_call', dnc: new Set([phone]), now: AFTERNOON }).ok);
  assert.ok(!checkRecipient({ lead, phone, channel: 'sms', optOuts: new Set([phone]), now: AFTERNOON }).ok);
  const ai = checkRecipient({ lead, phone, channel: 'ai_call', now: AFTERNOON });
  assert.ok(!ai.ok && ai.blocks.some((b) => b.includes('consent')));
  assert.ok(checkRecipient({ lead: { ...lead, consent: true }, phone, channel: 'ai_call', now: AFTERNOON }).ok);
});

test('compliance: Florida ends at 8 PM, Texas Sunday starts at noon', () => {
  // 8:30 PM Eastern
  assert.equal(withinCallingHours('FL', new Date('2026-11-26T01:30:00Z')).ok, false);
  assert.equal(withinCallingHours('GA', new Date('2026-11-26T01:30:00Z')).ok, true);
  // Sunday 10 AM Central
  assert.equal(withinCallingHours('TX', new Date('2026-11-29T16:00:00Z')).ok, false);
});

test('DNC parser reads any phone format', () => {
  const s = parsePhoneList('2145550143\n(214) 555-0177, +1 214.555.0199');
  assert.deepEqual([...s].sort(), ['+12145550143', '+12145550177', '+12145550199']);
});

test('nothing sends without approval, and dry run sends nothing even after approval', async () => {
  const store = tmpStore();
  importFile(store, SAMPLE, { listType: 'probate' });
  const ids = store.data.leads.map((l) => l.id);
  const batch = draftBatch(store, { leadIds: ids, channel: 'sms', settings: { myName: 'Stephen', company: 'Acme Homes' }, now: AFTERNOON });
  assert.equal(batch.status, 'pending_approval');
  assert.equal(batch.excluded.length, 1, 'lead without phone is excluded');
  assert.ok(batch.items[0].body.includes('Reply STOP'));
  let calls = 0;
  const senders = { sms: async () => { calls++; return { sid: 'x' }; } };
  await assert.rejects(() => sendBatch(store, batch.id, senders, { live: true, now: AFTERNOON }), /not approved/);
  approveBatch(store, batch.id, 'Stephen');
  await sendBatch(store, batch.id, senders, { live: false, now: AFTERNOON });
  assert.equal(calls, 0);
  assert.ok(batch.items.every((i) => i.status === 'dry_run'));
  await sendBatch(store, batch.id, senders, { live: true, now: AFTERNOON });
  assert.equal(calls, batch.items.length);
  assert.equal(batch.status, 'sent');
});

test('send re-checks hours at send time', async () => {
  const store = tmpStore();
  importFile(store, SAMPLE, { listType: 'probate' });
  const batch = draftBatch(store, { leadIds: [store.data.leads[0].id], channel: 'sms', now: AFTERNOON });
  approveBatch(store, batch.id, 'Stephen');
  let calls = 0;
  await sendBatch(store, batch.id, { sms: async () => { calls++; } }, { live: true, now: NIGHT });
  assert.equal(calls, 0);
  assert.equal(batch.items[0].status, 'held');
});

test('daily run analyzes and drafts letters only; call list is sorted with numbers', () => {
  const store = tmpStore();
  importFile(store, SAMPLE, { listType: 'probate' });
  const res = runDaily(store, { myName: 'Stephen' });
  assert.equal(res.analyzed, 4);
  assert.ok(res.batches.every((b) => b.channel === 'mail' && b.status === 'pending_approval'));
  const list = callList(store, {});
  assert.ok(list.length >= 1);
  for (let i = 1; i < list.length; i++) assert.ok(list[i - 1].score >= list[i].score);
  assert.ok(list[0].maxOffer.startsWith('$'));
  assert.ok(store.data.batches[0].items[0].body.includes('sorry for your loss'));
});

test('follow-ups come due on the cadence', () => {
  const t0 = Date.parse('2026-11-01T12:00:00Z');
  const lead = { id: 'a', stage: 'contacted', createdAt: new Date(t0).toISOString(), phones: ['+12145550143'],
    history: [{ at: new Date(t0).toISOString(), stage: 'contacted' }] };
  assert.equal(dueFollowUps([lead], t0 + 2 * 864e5).length, 0);
  assert.equal(dueFollowUps([lead], t0 + 3 * 864e5).length, 1);
  assert.equal(dueFollowUps([{ ...lead, stage: 'dead' }], t0 + 30 * 864e5).length, 0);
});

test('contract draft fills deal fields and lists what is missing', () => {
  const lead = { ownerName: 'Harold Jenkins', contactName: 'Linda Jenkins', address: '1412 Elm Street', city: 'Dallas', state: 'TX', zip: '75216' };
  const text = purchaseAgreement(lead, { price: 72000, settings: { myName: 'Stephen' }, today: new Date('2026-11-25T12:00:00Z') });
  assert.ok(text.includes('$72,000'));
  assert.ok(text.includes('Linda Jenkins, as Personal Representative of the Estate of Harold Jenkins'));
  assert.ok(text.includes('2027-01-09'));
  assert.deepEqual(missingFields(text), ['titleCompany']);
});

test('buyer matching by zip and budget', () => {
  const buyers = [
    { name: 'A', zips: ['75216'], maxPrice: 150000 },
    { name: 'B', zips: ['75001'], maxPrice: 500000 },
    { name: 'C', zips: ['75216'], maxPrice: 50000 },
  ];
  const m = matchBuyers({ zip: '75216' }, { buyerPrice: 87600 }, buyers);
  assert.deepEqual(m.map((b) => b.name), ['A']);
});
