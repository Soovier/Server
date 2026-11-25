// Cold email writer. Short, personal, plain text, and CAN-SPAM compliant:
// real sender identity, truthful subject, physical mailing address, and a working opt-out.
import { config } from "./config.mjs";

const usd = (n) => `$${Math.round(n).toLocaleString("en-US")}`;

export function missingSenderDetails(agency = config.agency) {
  return ["name", "owner", "email", "address"].filter((k) => !agency[k]);
}

function footer(agency) {
  return [
    "",
    `${agency.owner}`,
    `${agency.name}${agency.phone ? ` · ${agency.phone}` : ""}`,
    agency.address,
    "",
    "Not interested? Reply \"stop\" and I won't email you again.",
  ].join("\n");
}

// The most specific thing we noticed, phrased for the owner.
function hook(audit) {
  const ids = new Set(audit.findings.map((f) => f.id));
  if (ids.has("emergency-gap")) return "you advertise emergency service, but calls after hours seem to go to voicemail";
  if (ids.has("after-hours")) return "calls after hours and on weekends seem to go to voicemail";
  if (ids.has("closes-early")) return "your office closes before most homeowners get home from work";
  if (ids.has("no-booking")) return "every new job has to start with a phone call, since there's no online booking";
  return "busy days probably mean some calls go unanswered";
}

export function writeEmail(record, { step = 0, proposalUrl = "", agency = config.agency } = {}) {
  const missing = missingSenderDetails(agency);
  if (missing.length) throw new Error(`Cannot write outreach without sender details: ${missing.join(", ")} (CAN-SPAM).`);
  const { biz, audit } = record;
  const to = biz.emails?.[0];
  if (!to) throw new Error(`No email address for ${biz.name}`);
  const link = proposalUrl || record.agent?.talkUrl || "";

  if (step === 0) {
    return {
      to,
      subject: `Missed calls at ${biz.name}`,
      text: [
        `Hi ${biz.name} team,`,
        "",
        `I was looking at ${biz.name} and noticed ${hook(audit)}. For a shop your size that's often around ${audit.missedCallsPerMonth} missed calls a month, which can add up to ${usd(audit.lostRevenue.low)} or more in lost jobs.`,
        "",
        `So I built you an AI receptionist that already knows your services and hours. It answers 24/7 in your company's name, takes the caller's details and flags emergencies.${link ? ` You can talk to it here, like a customer would:\n${link}` : ""}`,
        "",
        "Worth a 15-minute call to see if it fits?",
        footer(agency),
      ].join("\n"),
    };
  }
  return {
    to,
    subject: `Re: Missed calls at ${biz.name}`,
    text: [
      `Hi again,`,
      "",
      `Just making sure this reached the right person. The AI receptionist I built for ${biz.name} is still live if you'd like to hear it${link ? `:\n${link}` : "."}`,
      "",
      agency.bookingUrl ? `If it's worth a quick call, grab any time here: ${agency.bookingUrl}` : "If it's worth a quick call, just reply with a good time.",
      footer(agency),
    ].join("\n"),
  };
}
