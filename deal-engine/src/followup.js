// Follow-up cadence. Most probate deals close on the 4th to 8th touch, months after the first letter,
// because heirs are not ready until the court appoints them or the house becomes a burden.
const CADENCE_DAYS = [3, 7, 14, 30, 60, 90, 180];

// Stages: new -> contacted -> talking -> offer_sent -> under_contract -> assigned -> closed
//         any -> not_now (keeps following up) | dead (stops) | opted_out (stops forever)
const ACTIVE = new Set(['contacted', 'not_now', 'offer_sent']);

function lastTouch(lead) {
  const h = (lead.history || []).filter((e) => e.stage !== 'new');
  return h.length ? Date.parse(h[h.length - 1].at) : Date.parse(lead.createdAt);
}

function touchCount(lead) {
  return (lead.history || []).filter((e) => /^(contacted|followup|not_now|offer_sent)$/.test(e.stage)).length;
}

// Returns leads whose next follow-up is due, with the suggested channel.
function dueFollowUps(leads, now = Date.now()) {
  const due = [];
  for (const lead of leads) {
    if (!ACTIVE.has(lead.stage)) continue;
    const n = touchCount(lead);
    if (n > CADENCE_DAYS.length) continue;
    const waitDays = CADENCE_DAYS[Math.min(n, CADENCE_DAYS.length) - 1] || CADENCE_DAYS[0];
    const dueAt = lastTouch(lead) + waitDays * 864e5;
    if (dueAt <= now) {
      const channel = lead.email ? 'email' : lead.phones && lead.phones.length ? 'sms_followup' : 'mail';
      due.push({ leadId: lead.id, address: lead.address, touch: n + 1, channel, dueAt: new Date(dueAt).toISOString() });
    }
  }
  return due;
}

module.exports = { dueFollowUps, CADENCE_DAYS };
