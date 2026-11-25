// Outreach state: who was emailed, what step they're on, replies, and the do-not-contact list.
// Batches are built here but never sent here: a batch must be approved by a person first.
import { writeEmail } from "./email.mjs";

export const FOLLOW_UP_AFTER_DAYS = 3;
export const PER_INBOX_CAP = 50;

export function emptyState() {
  return { warmupStart: null, contacts: {}, suppressed: [], batches: {} };
}

const domainOf = (email) => email.split("@")[1]?.toLowerCase() || "";
const dayMs = 864e5;
const daysBetween = (a, b) => Math.floor((Date.parse(b) - Date.parse(a)) / dayMs);

// Gradual ramp so a new domain builds a sending reputation: per inbox per day.
export function dailyLimit(state, today, inboxes = 1) {
  if (!state.warmupStart) return 0;
  const day = daysBetween(state.warmupStart, today);
  const perInbox = day < 7 ? 5 : day < 14 ? 15 : day < 21 ? 30 : PER_INBOX_CAP;
  return day < 0 ? 0 : perInbox * inboxes;
}

export function isSuppressed(state, email) {
  const e = email.toLowerCase();
  return state.suppressed.includes(e) || state.suppressed.includes(`@${domainOf(e)}`);
}

export function suppress(state, emailOrDomain, reason = "unsubscribed") {
  const v = emailOrDomain.toLowerCase();
  if (!state.suppressed.includes(v)) state.suppressed.push(v);
  const c = state.contacts[v];
  if (c) c.status = reason;
  return state;
}

// Picks follow-ups that are due first, then new leads by score, up to today's limit.
export function buildBatch(state, records, { today, inboxes = 1, proposalUrlFor = () => "" } = {}) {
  const limit = dailyLimit(state, today, inboxes);
  const items = [];
  const skipped = [];

  for (const [email, c] of Object.entries(state.contacts)) {
    if (items.length >= limit) break;
    if (c.status !== "sent" || c.step !== 0 || isSuppressed(state, email)) continue;
    if (daysBetween(c.lastSentAt.slice(0, 10), today) < FOLLOW_UP_AFTER_DAYS) continue;
    const record = records.find((r) => r.biz.emails?.[0]?.toLowerCase() === email);
    if (!record) continue;
    items.push({ email, name: record.biz.name, step: 1, ...writeEmail(record, { step: 1, proposalUrl: proposalUrlFor(record) }) });
  }

  const fresh = [...records].sort((a, b) => b.audit.score - a.audit.score);
  for (const r of fresh) {
    if (items.length >= limit) break;
    const email = r.biz.emails?.[0]?.toLowerCase();
    if (!email) { skipped.push({ name: r.biz.name, reason: "no email found" }); continue; }
    if (state.contacts[email]) continue;
    if (isSuppressed(state, email)) { skipped.push({ name: r.biz.name, reason: "on do-not-contact list" }); continue; }
    if (r.audit.score <= 0) { skipped.push({ name: r.biz.name, reason: "already well covered" }); continue; }
    items.push({ email, name: r.biz.name, step: 0, ...writeEmail(r, { step: 0, proposalUrl: proposalUrlFor(r) }) });
  }

  return { date: today, limit, approved: false, items, skipped };
}

export function approveBatch(state, batch, approvedBy) {
  if (!approvedBy) throw new Error("A batch needs the name of the person approving it.");
  batch.approved = true;
  batch.approvedBy = approvedBy;
  state.batches[batch.date] = { approved: true, approvedBy, count: batch.items.length };
  return batch;
}

// Called after each email actually goes out. Refuses unapproved batches and suppressed contacts.
export function markSent(state, batch, item, sentAt) {
  if (!batch.approved) throw new Error("Refusing to record a send from an unapproved batch.");
  if (isSuppressed(state, item.email)) throw new Error(`${item.email} is on the do-not-contact list.`);
  state.contacts[item.email] = { name: item.name, step: item.step, status: "sent", lastSentAt: sentAt };
  return state;
}

const RULES = [
  ["unsubscribe", /\b(stop|unsubscribe|remove me|take me off|do not (email|contact)|don't (email|contact))\b/i],
  ["out_of_office", /\b(out of (the )?office|on vacation|auto-?reply|automatic reply)\b/i],
  ["not_interested", /\b(not interested|no thanks|no thank you|we('re| are) good|already have)\b/i],
  ["interested", /\b(interested|sounds good|let'?s talk|call me|give me a call|set (up )?a (call|time)|how much|pricing|price|cost|demo|tell me more|yes)\b/i],
];

export function classifyReply(text = "") {
  for (const [label, re] of RULES) if (re.test(text)) return label;
  return "question";
}

// What the system does with a reply. Only "notify" items need Stephen.
export function handleReply(state, email, text, receivedAt) {
  const label = classifyReply(text);
  const e = email.toLowerCase();
  const c = (state.contacts[e] ||= { name: "", step: 0 });
  c.lastReplyAt = receivedAt;
  if (label === "unsubscribe" || label === "not_interested") {
    suppress(state, e, label);
    return { label, action: "suppress" };
  }
  if (label === "out_of_office") return { label, action: "ignore" };
  c.status = label === "interested" ? "interested" : "replied";
  return { label, action: label === "interested" ? "send_booking_link_and_notify" : "notify" };
}
