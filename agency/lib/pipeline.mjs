// Runs one lead through: website read -> audit -> business profile -> (demo agent) -> proposal.
import { readWebsite, extractSignals, htmlToText, summarizeForAgent } from "./site.mjs";
import { auditBusiness, TRADE_DEFAULTS, tradeKey } from "./audit.mjs";
import { createAgent } from "./elevenlabs.mjs";
import { renderProposal } from "./proposal.mjs";

export const slug = (s = "") => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);

export function businessProfile(lead, site) {
  return {
    name: lead.name,
    trade: tradeKey(lead.trade),
    tradeLabel: TRADE_DEFAULTS[tradeKey(lead.trade)].label,
    city: lead.city,
    phone: lead.phone,
    website: lead.website,
    hoursText: lead.hoursText || [],
    reviewCount: lead.reviewCount || 0,
    yearFounded: site.signals.yearFounded,
    emails: site.signals.emails,
    knowledge: summarizeForAgent(site.text),
  };
}

export async function processLead(lead, { createAgents = false, readSite = readWebsite, makeAgent = createAgent } = {}) {
  // Leads may carry pre-fetched HTML (tests, or pages saved by another tool).
  const site = lead.siteHtml
    ? { url: lead.website, ok: true, html: lead.siteHtml, text: htmlToText(lead.siteHtml), signals: extractSignals(lead.siteHtml) }
    : await readSite(lead.website);
  const audit = auditBusiness(lead, site.signals);
  const biz = businessProfile(lead, site);
  let agent = null;
  if (createAgents) agent = await makeAgent(biz, { demo: true });
  const proposalHtml = renderProposal(biz, audit, { talkUrl: agent?.talkUrl || "" });
  return { id: slug(lead.name), lead: { ...lead, siteHtml: undefined }, siteOk: site.ok, signals: site.signals, audit, biz, agent, proposalHtml };
}
