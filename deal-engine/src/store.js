// Tiny JSON-file store. One file holds leads, outreach batches, opt-outs and the activity log.
// Good for a solo operator with thousands of leads; swap for a database later if needed.
const fs = require('node:fs');
const path = require('node:path');

const EMPTY = () => ({ leads: [], batches: [], optOuts: [], buyers: [], log: [] });

class Store {
  constructor(dir = process.env.DEAL_ENGINE_DATA_DIR || path.join(__dirname, '..', 'data')) {
    this.dir = dir;
    this.file = path.join(dir, 'db.json');
    fs.mkdirSync(dir, { recursive: true });
    this.data = fs.existsSync(this.file) ? { ...EMPTY(), ...JSON.parse(fs.readFileSync(this.file, 'utf8')) } : EMPTY();
  }

  save() {
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2));
    fs.renameSync(tmp, this.file);
  }

  log(event, details = {}) {
    this.data.log.push({ at: new Date().toISOString(), event, ...details });
    if (this.data.log.length > 5000) this.data.log.splice(0, this.data.log.length - 5000);
  }

  lead(id) { return this.data.leads.find((l) => l.id === id); }
  batch(id) { return this.data.batches.find((b) => b.id === id); }

  setStage(id, stage, note) {
    const lead = this.lead(id);
    if (!lead) throw new Error(`No lead ${id}`);
    lead.stage = stage;
    lead.history = lead.history || [];
    lead.history.push({ at: new Date().toISOString(), stage, note });
    this.log('stage', { lead: id, stage, note });
    return lead;
  }

  optOut(phoneOrEmail, reason = 'requested') {
    if (!this.data.optOuts.includes(phoneOrEmail)) this.data.optOuts.push(phoneOrEmail);
    this.log('opt_out', { who: phoneOrEmail, reason });
  }
}

module.exports = { Store };
