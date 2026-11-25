#!/usr/bin/env node
// Usage:
//   node cli.mjs find-leads --trade hvac --city "Los Angeles, CA" [--max 20]
//   node cli.mjs run --leads out/leads-hvac-los-angeles-ca.json [--limit 5] [--create-agents]
//   node cli.mjs start-warmup --date 2026-12-01
//   node cli.mjs batch --date 2026-12-01 [--inboxes 2]      builds today's emails for review
//   node cli.mjs approve --date 2026-12-01 --by Stephen     the human approval step
//   node cli.mjs mark-sent --date 2026-12-01 --email x@y.com
//   node cli.mjs reply --from x@y.com --text "how much is it?"
// Nothing here contacts a business. Outreach is a separate, approval-gated step.
import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { join } from "node:path";
import { findLeads } from "./lib/places.mjs";
import { processLead, slug } from "./lib/pipeline.mjs";
import { emptyState, buildBatch, approveBatch, markSent, handleReply } from "./lib/outreach.mjs";

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

const STATE = join(OUT, "outreach-state.json");
const today = () => new Date().toISOString().slice(0, 10);

async function loadState() {
  try { return JSON.parse(await readFile(STATE, "utf8")); } catch { return emptyState(); }
}
async function saveState(state) {
  await mkdir(OUT, { recursive: true });
  await writeFile(STATE, JSON.stringify(state, null, 2));
}
async function loadRecords() {
  const records = [];
  for (const d of await readdir(OUT, { withFileTypes: true })) {
    if (!d.isDirectory()) continue;
    try { records.push(JSON.parse(await readFile(join(OUT, d.name, "record.json"), "utf8"))); } catch {}
  }
  return records;
}
const batchFile = (date) => join(OUT, "batches", `${date}.json`);

async function cmdStartWarmup(o) {
  const state = await loadState();
  state.warmupStart = o.date || today();
  await saveState(state);
  console.log(`Warm-up starts ${state.warmupStart}: 5/day per inbox in week 1, 15 in week 2, 30 in week 3, then 50.`);
}

async function cmdBatch(o) {
  const state = await loadState();
  const date = o.date || today();
  const batch = buildBatch(state, await loadRecords(), { today: date, inboxes: Number(o.inboxes || 1) });
  await mkdir(join(OUT, "batches"), { recursive: true });
  await writeFile(batchFile(date), JSON.stringify(batch, null, 2));
  const review = [`# Outreach batch ${date}`, "", `${batch.items.length} emails (limit ${batch.limit}). Not sent until approved.`, ""]
    .concat(batch.items.map((i, n) => `## ${n + 1}. ${i.name} (${i.step === 0 ? "first email" : "follow-up"})\nTo: ${i.email}\nSubject: ${i.subject}\n\n${i.text}\n`))
    .concat(batch.skipped.length ? ["## Skipped", ...batch.skipped.map((s) => `- ${s.name}: ${s.reason}`)] : []);
  await writeFile(join(OUT, "batches", `${date}.md`), review.join("\n"));
  console.log(`Batch ${date}: ${batch.items.length} emails ready for review, ${batch.skipped.length} skipped.`);
}

async function cmdApprove(o) {
  const date = o.date || today();
  const state = await loadState();
  const batch = JSON.parse(await readFile(batchFile(date), "utf8"));
  approveBatch(state, batch, o.by);
  await writeFile(batchFile(date), JSON.stringify(batch, null, 2));
  await saveState(state);
  console.log(`Batch ${date} approved by ${o.by}: ${batch.items.length} emails may now be sent.`);
}

async function cmdMarkSent(o) {
  const date = o.date || today();
  const state = await loadState();
  const batch = JSON.parse(await readFile(batchFile(date), "utf8"));
  const item = batch.items.find((i) => i.email === String(o.email).toLowerCase());
  if (!item) throw new Error(`${o.email} is not in batch ${date}`);
  markSent(state, batch, item, o.at || new Date().toISOString());
  await saveState(state);
  console.log(`Recorded send to ${item.email}`);
}

async function cmdReply(o) {
  const state = await loadState();
  const result = handleReply(state, o.from, o.text || "", new Date().toISOString());
  await saveState(state);
  console.log(JSON.stringify(result));
}

const o = args(process.argv.slice(2));
const commands = {
  "find-leads": cmdFindLeads, run: cmdRun, "start-warmup": cmdStartWarmup,
  batch: cmdBatch, approve: cmdApprove, "mark-sent": cmdMarkSent, reply: cmdReply,
};
const cmd = commands[o._[0]];
if (!cmd) {
  console.log(`Commands: ${Object.keys(commands).join(", ")}. See the top of cli.mjs for usage.`);
  process.exit(1);
}
cmd(o).catch((e) => {
  console.error(e.message);
  process.exit(1);
});
