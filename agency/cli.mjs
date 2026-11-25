#!/usr/bin/env node
// Usage:
//   node cli.mjs find-leads --trade hvac --city "Los Angeles, CA" [--max 20]
//   node cli.mjs run --leads out/leads-hvac-los-angeles-ca.json [--limit 5] [--create-agents]
// Nothing here contacts a business. Outreach is a separate, approval-gated step.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { findLeads } from "./lib/places.mjs";
import { processLead, slug } from "./lib/pipeline.mjs";

function args(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) out[a.slice(2)] = true;
      else out[a.slice(2)] = argv[++i];
    } else out._.push(a);
  }
  return out;
}

const OUT = new URL("./out/", import.meta.url).pathname;

async function cmdFindLeads(o) {
  if (!o.trade || !o.city) throw new Error('Need --trade and --city, e.g. --trade hvac --city "Los Angeles, CA"');
  const leads = await findLeads({ trade: o.trade, city: o.city, max: Number(o.max || 20) });
  await mkdir(OUT, { recursive: true });
  const file = join(OUT, `leads-${slug(o.trade)}-${slug(o.city)}.json`);
  await writeFile(file, JSON.stringify(leads, null, 2));
  console.log(`Saved ${leads.length} leads to ${file}`);
}

async function cmdRun(o) {
  if (!o.leads) throw new Error("Need --leads <file.json>");
  const leads = JSON.parse(await readFile(o.leads, "utf8")).slice(0, Number(o.limit || 5));
  const rows = [];
  for (const lead of leads) {
    try {
      const r = await processLead(lead, { createAgents: Boolean(o["create-agents"]) });
      const dir = join(OUT, r.id);
      await mkdir(dir, { recursive: true });
      await writeFile(join(dir, "proposal.html"), r.proposalHtml);
      const { proposalHtml, ...record } = r;
      await writeFile(join(dir, "record.json"), JSON.stringify(record, null, 2));
      rows.push(r);
      console.log(`ok   ${lead.name}: ~$${r.audit.recoverablePerMonth}/mo recoverable, score ${r.audit.score}`);
    } catch (e) {
      console.log(`fail ${lead.name}: ${e.message}`);
    }
  }
  rows.sort((a, b) => b.audit.score - a.audit.score);
  const csv = ["name,score,recoverable_per_month,missed_calls_per_month,email,phone,website,demo_url"]
    .concat(rows.map((r) => [r.biz.name, r.audit.score, r.audit.recoverablePerMonth, r.audit.missedCallsPerMonth,
      r.biz.emails[0] || "", r.biz.phone, r.biz.website, r.agent?.talkUrl || ""]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")));
  await mkdir(OUT, { recursive: true });
  await writeFile(join(OUT, "pipeline.csv"), csv.join("\n"));
  console.log(`Ranked ${rows.length} businesses in ${join(OUT, "pipeline.csv")}`);
}

const o = args(process.argv.slice(2));
const commands = { "find-leads": cmdFindLeads, run: cmdRun };
const cmd = commands[o._[0]];
if (!cmd) {
  console.log("Commands: find-leads, run. See the top of cli.mjs for usage.");
  process.exit(1);
}
cmd(o).catch((e) => {
  console.error(e.message);
  process.exit(1);
});
