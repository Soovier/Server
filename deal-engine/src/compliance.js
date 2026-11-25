// Outreach compliance gate. A safety net, not legal advice.
//
// Enforced before anything is sent:
//   - Do Not Call: the DNC scrub list you download, plus your own opt-out list.
//   - Calling hours: 8 AM to 9 PM in the recipient's time zone (TCPA), tighter where states require.
//   - AI / prerecorded voice calls only to people who gave prior express consent (TCPA, FCC 2024 ruling).
//   - Daily frequency caps where states set them.
//   - Every batch is approved by a person before it sends (see outreach.js).

const STATE_TZ = {
  AL: 'America/Chicago', AK: 'America/Anchorage', AZ: 'America/Phoenix', AR: 'America/Chicago',
  CA: 'America/Los_Angeles', CO: 'America/Denver', CT: 'America/New_York', DE: 'America/New_York',
  DC: 'America/New_York', FL: 'America/New_York', GA: 'America/New_York', HI: 'Pacific/Honolulu',
  ID: 'America/Boise', IL: 'America/Chicago', IN: 'America/Indiana/Indianapolis', IA: 'America/Chicago',
  KS: 'America/Chicago', KY: 'America/New_York', LA: 'America/Chicago', ME: 'America/New_York',
  MD: 'America/New_York', MA: 'America/New_York', MI: 'America/Detroit', MN: 'America/Chicago',
  MS: 'America/Chicago', MO: 'America/Chicago', MT: 'America/Denver', NE: 'America/Chicago',
  NV: 'America/Los_Angeles', NH: 'America/New_York', NJ: 'America/New_York', NM: 'America/Denver',
  NY: 'America/New_York', NC: 'America/New_York', ND: 'America/Chicago', OH: 'America/New_York',
  OK: 'America/Chicago', OR: 'America/Los_Angeles', PA: 'America/New_York', RI: 'America/New_York',
  SC: 'America/New_York', SD: 'America/Chicago', TN: 'America/Chicago', TX: 'America/Chicago',
  UT: 'America/Denver', VT: 'America/New_York', VA: 'America/New_York', WA: 'America/Los_Angeles',
  WV: 'America/New_York', WI: 'America/Chicago', WY: 'America/Denver',
};

// Stricter state rules (local hours, [start, end)). Check your state's current law before relying on these.
const STATE_RULES = {
  FL: { start: 8, end: 20, maxPerDay: 3, note: 'Florida Telephone Solicitation Act: 8 AM-8 PM, max 3 calls per 24h on the same subject' },
  OK: { start: 8, end: 20, maxPerDay: 3, note: 'Oklahoma Telephone Solicitation Act: 8 AM-8 PM, max 3 calls per 24h' },
  MD: { maxPerDay: 3, note: 'Maryland Stop the Spam Calls Act: max 3 calls per 24h' },
  TX: { sundayStart: 12, note: 'Texas: no telephone solicitation before noon on Sunday' },
};

const DEFAULT_WINDOW = { start: 8, end: 21 };

function localParts(date, tz) {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23', weekday: 'short' });
  const parts = Object.fromEntries(f.formatToParts(date).map((p) => [p.type, p.value]));
  return { hour: Number(parts.hour), weekday: parts.weekday };
}

function withinCallingHours(state, date = new Date()) {
  const tz = STATE_TZ[state] || 'America/New_York';
  const rule = { ...DEFAULT_WINDOW, ...(STATE_RULES[state] || {}) };
  const { hour, weekday } = localParts(date, tz);
  const start = weekday === 'Sun' && rule.sundayStart ? rule.sundayStart : rule.start;
  return { ok: hour >= start && hour < rule.end, tz, localHour: hour, window: `${start}:00-${rule.end}:00` };
}

const PHONE_CHANNELS = ['manual_call', 'ai_call', 'sms'];

/**
 * Checks one recipient for one channel.
 * channel: 'manual_call' (you dial), 'ai_call' (ElevenLabs agent), 'sms', 'email', 'mail'
 */
function checkRecipient({ lead, phone, channel, dnc = new Set(), optOuts = new Set(), sentToday = 0, now = new Date() }) {
  const blocks = [];
  const warnings = [];
  if (PHONE_CHANNELS.includes(channel)) {
    if (!phone) blocks.push('no phone number (skip trace first)');
    if (phone && dnc.has(phone)) blocks.push('on Do Not Call list');
    if (phone && optOuts.has(phone)) blocks.push('opted out');
    const hours = withinCallingHours(lead.state, now);
    if (!hours.ok) blocks.push(`outside calling hours (${hours.window} ${hours.tz}, now ${hours.localHour}:00)`);
    const rule = STATE_RULES[lead.state];
    if (rule && rule.maxPerDay && sentToday >= rule.maxPerDay) blocks.push(`state cap of ${rule.maxPerDay} per day reached`);
    if (rule && rule.note) warnings.push(rule.note);
  }
  if (channel === 'ai_call' && !lead.consent) {
    blocks.push('AI voice calls need prior express consent: call manually, or let the AI handle sellers who call or text in');
  }
  if (channel === 'sms' && !lead.consent) {
    warnings.push('Cold texting: several states treat automated texts like calls. Send from a registered 10DLC number, say who you are, include "Reply STOP to opt out".');
  }
  if (channel === 'email' && !lead.email) blocks.push('no email address');
  if (channel === 'mail' && !(lead.contactMailing || lead.mailingAddress || lead.address)) blocks.push('no mailing address');
  return { ok: blocks.length === 0, blocks, warnings };
}

// Reads any text containing US phone numbers (a DNC download, a CSV) into a Set of +1XXXXXXXXXX.
function parsePhoneList(text) {
  const set = new Set();
  for (const m of String(text || '').matchAll(/(?:\+?1[\s.-]?)?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})/g)) {
    set.add(`+1${m[1]}${m[2]}${m[3]}`);
  }
  return set;
}

module.exports = { checkRecipient, withinCallingHours, parsePhoneList, STATE_TZ, STATE_RULES };
