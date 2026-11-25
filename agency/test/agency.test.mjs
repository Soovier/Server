import { test } from "node:test";
import assert from "node:assert/strict";
import { extractSignals, htmlToText, summarizeForAgent } from "../lib/site.mjs";
import { auditBusiness, analyzeHours, callsPerDayFromReviews } from "../lib/audit.mjs";
import { receptionistPrompt, firstMessage } from "../lib/prompt.mjs";
import { agentPayload } from "../lib/elevenlabs.mjs";
import { renderProposal } from "../lib/proposal.mjs";
import { processLead, slug } from "../lib/pipeline.mjs";
import { isLikelyChain, normalizePlace, findLeads } from "../lib/places.mjs";
import { config } from "../lib/config.mjs";

const HTML = `<html><head><script>var x="a@sentry.io"</script></head><body>
<h1>Acme Heating &amp; Air</h1><p>Family owned since 1987. Serving Los Angeles County and Pasadena.</p>
<p>We repair and install air conditioners, furnaces and heat pumps. Emergency service available.</p>
<p>Call (323) 555-0142 or email <a href="mailto:Office@AcmeHVAC.com">office@acmehvac.com</a></p>
<p>Free estimates on new system installs. Financing available.</p></body></html>`;

const weekdays9to5 = [1, 2, 3, 4, 5].map((d) => ({ open: { day: d, hour: 8, minute: 0 }, close: { day: d, hour: 17, minute: 0 } }));

test("extracts contact details and signals from a website", () => {
  const s = extractSignals(HTML);
  assert.deepEqual(s.emails, ["office@acmehvac.com"]);
  assert.deepEqual(s.phones, ["3235550142"]);
  assert.equal(s.yearFounded, "1987");
  assert.equal(s.emergencyService, true);
  assert.equal(s.onlineBooking, false);
  assert.equal(s.says247, false);
});

test("detects booking tools and 24/7 claims", () => {
  const s = extractSignals('<a href="https://book.housecallpro.com/x">Book online</a> We answer 24/7');
  assert.equal(s.onlineBooking, true);
  assert.ok(s.bookingTools.includes("Housecall Pro"));
  assert.equal(s.says247, true);
});

test("summary keeps service lines and drops scripts", () => {
  const sum = summarizeForAgent(htmlToText(HTML));
  assert.match(sum, /air conditioners, furnaces/);
  assert.doesNotMatch(sum, /sentry/);
});

test("hours analysis", () => {
  assert.deepEqual(analyzeHours(weekdays9to5), { known: true, open247: false, weekendClosed: true, closesEarly: true });
  assert.equal(analyzeHours([{ open: { day: 0, hour: 0, minute: 0 } }]).open247, true);
  assert.equal(analyzeHours([]).known, false);
});

test("audit is conservative and explained", () => {
  const lead = { name: "Acme", trade: "hvac", reviewCount: 120, hoursPeriods: weekdays9to5 };
  const a = auditBusiness(lead, extractSignals(HTML));
  assert.equal(callsPerDayFromReviews(120), 12);
  assert.equal(a.missRate, 0.5);
  // 12 calls/day * 30 * 0.5 missed * 0.6 new jobs * 0.35 book * $450 = $17,010
  assert.equal(a.lostRevenue.mid, 17000);
  assert.equal(a.recoverablePerMonth, 11900);
  assert.ok(a.lostRevenue.low < a.lostRevenue.mid && a.lostRevenue.mid < a.lostRevenue.high);
  assert.ok(a.findings.some((f) => f.id === "emergency-gap"));
});

test("a shop that already answers 24/7 ranks lower", () => {
  const lead = { name: "B", trade: "hvac", reviewCount: 120, hoursPeriods: [{ open: { day: 0, hour: 0, minute: 0 } }] };
  const covered = auditBusiness(lead, { onlineBooking: true, chatWidget: true, says247: true });
  const open = auditBusiness({ ...lead, hoursPeriods: weekdays9to5 }, {});
  assert.ok(covered.score < open.score);
  assert.ok(covered.recoverablePerMonth < open.recoverablePerMonth);
});

test("receptionist discloses AI and recording, and has gas safety rule", () => {
  const biz = { name: "Acme Heating", tradeLabel: "HVAC", city: "Los Angeles", hoursText: ["Monday: 8 AM to 5 PM"], knowledge: "We fix ACs." };
  assert.match(firstMessage(biz, { demo: true }), /AI assistant.*may be recorded/);
  const p = receptionistPrompt(biz, { demo: true, transferNumber: "+13235550142" });
  assert.match(p, /smells gas/);
  assert.match(p, /transfer the call to \+13235550142/);
  assert.match(p, /Monday: 8 AM to 5 PM/);
  assert.match(p, /We fix ACs\./);
});

test("agent payload shape", () => {
  const p = agentPayload({ name: "Acme", trade: "hvac" }, { demo: true, voiceId: "voice123" });
  assert.equal(p.name, "[DEMO] Acme receptionist");
  assert.equal(p.conversation_config.tts.voice_id, "voice123");
  assert.equal(p.conversation_config.agent.language, "en");
  assert.ok(p.conversation_config.agent.prompt.prompt.length > 200);
});

test("proposal escapes business text and shows the demo link", () => {
  const lead = { name: 'Bad <script>alert("x")</script> Co', trade: "hvac", reviewCount: 60, hoursPeriods: weekdays9to5 };
  const audit = auditBusiness(lead, {});
  const html = renderProposal(lead, audit, { talkUrl: "https://elevenlabs.io/app/talk-to?agent_id=abc", agency: { name: "Test Agency" }, pricing: { setup: 997, monthly: 497 } });
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /agent_id=abc/);
  assert.match(html, /\$497 \/ month/);
});

test("pipeline runs offline with pre-fetched HTML and a fake agent maker", async () => {
  const lead = { name: "Acme Heating & Air", trade: "hvac", city: "Los Angeles, CA", reviewCount: 250, hoursPeriods: weekdays9to5, website: "https://acme.example", siteHtml: HTML };
  const r = await processLead(lead, { createAgents: true, makeAgent: async (biz) => ({ agentId: "a1", talkUrl: `https://x/${biz.name.length}` }) });
  assert.equal(r.id, "acme-heating-air");
  assert.equal(r.biz.yearFounded, "1987");
  assert.equal(r.agent.agentId, "a1");
  assert.equal(r.lead.siteHtml, undefined);
  assert.match(r.proposalHtml, /Talk to your AI receptionist/);
});

test("places helpers", async () => {
  assert.equal(isLikelyChain("One Hour Heating & Air Conditioning of LA"), true);
  assert.equal(isLikelyChain("Brody Pennell Heating & Air"), false);
  const n = normalizePlace({ id: "p1", displayName: { text: "X" }, userRatingCount: 9, businessStatus: "OPERATIONAL" });
  assert.equal(n.name, "X");
  assert.equal(n.reviewCount, 9);
  assert.equal(slug("Brody Pennell Heating & Air!"), "brody-pennell-heating-air");

  config.placesApiKey = "test-key";
  const calls = [];
  const fakeFetch = async (url, init) => {
    calls.push(JSON.parse(init.body));
    return { ok: true, json: async () => ({ places: [
      { id: "1", displayName: { text: "Good HVAC" }, businessStatus: "OPERATIONAL" },
      { id: "2", displayName: { text: "Service Experts Heating" }, businessStatus: "OPERATIONAL" },
      { id: "3", displayName: { text: "Closed HVAC" }, businessStatus: "CLOSED_PERMANENTLY" },
    ] }) };
  };
  const leads = await findLeads({ trade: "hvac", city: "Los Angeles, CA", max: 5, fetchImpl: fakeFetch });
  assert.deepEqual(leads.map((l) => l.name), ["Good HVAC"]);
  assert.equal(calls[0].textQuery, "hvac in Los Angeles, CA");
});
