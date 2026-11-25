#!/usr/bin/env node
// Command line for the deal engine.
//   node deal-engine/cli.js import <leads.csv> [--list probate] [--state TX]
//   node deal-engine/cli.js dnc <dnc-download.txt>
//   node deal-engine/cli.js buyers <buyers.csv>
//   node deal-engine/cli.js run                  analyze + draft today's batches (nothing sends)
//   node deal-engine/cli.js calls [out.csv]      today's call list
//   node deal-engine/cli.js pending              batches waiting for approval
//   node deal-engine/cli.js deal <leadId> [--asking 90000]
//   node deal-engine/cli.js contract <leadId> [--price 80000]
const fs = require('node:fs');
const { Store } = require('./src/store');
const { importFile, loadDnc, runDaily, callList, writeCallList } = require('./src/pipeline');
const { analyzeDeal } = require('./src/deal');
const { purchaseAgreement, missingFields } = require('./src/contracts');
const { parseCsv } = require('./src/csv');
const { importBuyers, matchBuyers } = require('./src/buyers');
const settings = require('./src/settings');

const [cmd, arg, ...rest] = process.argv.slice(2);
const flag = (name) => {
  const all = [arg, ...rest];
  const i = all.indexOf(`--${name}`);
  return i >= 0 ? all[i + 1] : undefined;
};
const store = new Store();

switch (cmd) {
  case 'import': {
    const res = importFile(store, arg, { listType: flag('list') || 'probate', state: flag('state') });
    store.save();
    console.log(`Added ${res.leads.length} leads, merged ${res.duplicates} duplicates, skipped ${res.skipped} without an address.`);
    console.log('Matched columns:', res.columns);
    break;
  }
  case 'dnc':
    console.log(`Loaded ${loadDnc(store, arg)} Do Not Call numbers.`);
    store.save();
    break;
  case 'buyers': {
    store.data.buyers = importBuyers(parseCsv(fs.readFileSync(arg, 'utf8')));
    store.save();
    console.log(`Loaded ${store.data.buyers.length} cash buyers.`);
    break;
  }
  case 'run':
    console.log(JSON.stringify(runDaily(store, settings), null, 2));
    console.log('Nothing was sent. Approve batches on the dashboard.');
    break;
  case 'calls':
    if (arg) console.log(`Wrote ${writeCallList(store, arg, settings)} leads to ${arg}`);
    else console.table(callList(store, settings).map(({ opener, why, ...c }) => c));
    break;
  case 'pending':
    for (const b of store.data.batches.filter((x) => x.status === 'pending_approval')) {
      console.log(`${b.id}  ${b.channel}  ${b.items.length} recipients  ${b.excluded.length} blocked`);
    }
    break;
  case 'deal': {
    const lead = store.lead(arg);
    if (!lead) { console.error('No such lead'); process.exit(1); }
    const asking = flag('asking') ? Number(flag('asking')) : lead.askingPrice ?? null;
    const a = analyzeDeal(lead, { ...settings.deal, comps: lead.comps || [], askingPrice: asking });
    console.log(a);
    if (a.ok) console.log('Matching buyers:', matchBuyers(lead, a, store.data.buyers).slice(0, 5).map((b) => b.name));
    break;
  }
  case 'contract': {
    const lead = store.lead(arg);
    if (!lead) { console.error('No such lead'); process.exit(1); }
    const price = Number(flag('price')) || (lead.analysis && lead.analysis.openingOffer);
    const text = purchaseAgreement(lead, { price, settings });
    console.log(text);
    const missing = missingFields(text);
    if (missing.length) console.error(`\nFill before signing: ${missing.join(', ')}`);
    break;
  }
  default:
    console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(1, 11).map((l) => l.replace(/^\/\/ ?/, '')).join('\n'));
}
