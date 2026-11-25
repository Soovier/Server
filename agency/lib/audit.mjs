// "X-ray" audit: a conservative, explained estimate of revenue lost to unanswered calls.
// Every number here is an assumption we show to the owner, not a fact about their books.

export const TRADE_DEFAULTS = {
  hvac: { avgTicket: 450, label: "HVAC" },
  plumbing: { avgTicket: 350, label: "plumbing" },
  roofing: { avgTicket: 1200, label: "roofing" },
  electrical: { avgTicket: 300, label: "electrical" },
  "garage door": { avgTicket: 350, label: "garage door" },
  default: { avgTicket: 350, label: "service" },
};

export const ASSUMPTIONS = {
  newJobShare: 0.6, // share of inbound calls that are new work (not vendors, existing jobs, spam)
  bookRate: 0.35, // share of answered new-job calls that become a booked job
  baseMissRate: 0.2, // share of calls missed during business hours by a typical small shop
  coveredMissRate: 0.1, // overflow misses at a shop that already answers 24/7
  afterHoursShare: 0.25, // share of calls that arrive outside business hours
  aiRecoveryRate: 0.7, // share of missed-call revenue an AI receptionist can realistically recover
};

export function tradeKey(trade = "") {
  const t = trade.toLowerCase();
  return Object.keys(TRADE_DEFAULTS).find((k) => k !== "default" && t.includes(k)) || "default";
}

// Review volume is the only public proxy for call volume. Tiers are deliberately modest.
export function callsPerDayFromReviews(reviewCount = 0) {
  if (reviewCount >= 600) return 30;
  if (reviewCount >= 200) return 20;
  if (reviewCount >= 50) return 12;
  return 6;
}

// Google Places "periods": [{open:{day,hour,minute}, close:{day,hour,minute}}]. day 0 = Sunday.
export function analyzeHours(periods = []) {
  if (!periods.length) return { known: false, open247: false, weekendClosed: null, closesEarly: null };
  const open247 = periods.length === 1 && !periods[0].close;
  const days = new Set(periods.map((p) => p.open?.day));
  const weekendClosed = !days.has(0) && !days.has(6);
  const closeHours = periods.filter((p) => p.close).map((p) => p.close.hour + (p.close.minute || 0) / 60);
  const closesEarly = closeHours.length > 0 && Math.max(...closeHours) <= 18;
  return { known: true, open247, weekendClosed, closesEarly };
}

export function auditBusiness(lead, signals = {}) {
  const key = tradeKey(lead.trade);
  const avgTicket = TRADE_DEFAULTS[key].avgTicket;
  const callsPerDay = callsPerDayFromReviews(lead.reviewCount);
  const hours = analyzeHours(lead.hoursPeriods);
  const findings = [];

  const coversAfterHours = hours.open247 || signals.says247;
  let missRate = coversAfterHours ? ASSUMPTIONS.coveredMissRate : ASSUMPTIONS.baseMissRate;
  if (!coversAfterHours) {
    missRate += ASSUMPTIONS.afterHoursShare;
    findings.push({ id: "after-hours", text: "No sign of live after-hours answering, so night and weekend callers likely reach voicemail." });
  }
  if (hours.weekendClosed) findings.push({ id: "weekends", text: "Closed on weekends, when many no-cool and no-heat calls come in." });
  if (hours.closesEarly) findings.push({ id: "closes-early", text: "Office closes by 6 PM, before most homeowners get home from work." });
  if (!signals.onlineBooking) {
    missRate += 0.05;
    findings.push({ id: "no-booking", text: "No online booking found, so every job has to start with a phone call." });
  }
  if (!signals.chatWidget) findings.push({ id: "no-chat", text: "No website chat or text-back tool found." });
  if (signals.emergencyService && !coversAfterHours) {
    findings.push({ id: "emergency-gap", text: "Advertises emergency service but shows no 24/7 answering." });
  }
  missRate = Math.min(missRate, 0.5);

  const missedCallsPerMonth = callsPerDay * 30 * missRate;
  const lostJobsPerMonth = missedCallsPerMonth * ASSUMPTIONS.newJobShare * ASSUMPTIONS.bookRate;
  const lostRevenue = lostJobsPerMonth * avgTicket;
  const recoverable = lostRevenue * ASSUMPTIONS.aiRecoveryRate;
  const round = (n) => Math.round(n / 100) * 100;

  // Priority: clear gaps first, money at stake second (capped so big shops don't dominate).
  // Shops that already answer 24/7, or are large enough to run a call center, rank last.
  const score = Math.min(Math.round(recoverable / 100), 100)
    + findings.length * 10
    - (coversAfterHours ? 40 : 0)
    - (signals.chatWidget ? 15 : 0)
    - (signals.onlineBooking ? 10 : 0)
    - ((lead.reviewCount || 0) > 1500 ? 30 : 0);

  return {
    trade: key,
    callsPerDay,
    missRate: Math.round(missRate * 100) / 100,
    missedCallsPerMonth: Math.round(missedCallsPerMonth),
    lostJobsPerMonth: Math.round(lostJobsPerMonth * 10) / 10,
    lostRevenue: { low: round(lostRevenue * 0.6), mid: round(lostRevenue), high: round(lostRevenue * 1.4) },
    recoverablePerMonth: round(recoverable),
    avgTicket,
    hours,
    findings,
    score,
    assumptions: { ...ASSUMPTIONS, avgTicket, callsPerDay, missRate },
  };
}
