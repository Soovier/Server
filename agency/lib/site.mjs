// Website reader: pulls the facts a receptionist needs and the signals the audit uses.

export function htmlToText(html = "") {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<br\s*\/?>|<\/(p|div|li|h[1-6]|tr|section)>/gi, "\n")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&#39;|&rsquo;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/[ \t]+/g, " ")
    .replace(/\n\s*\n+/g, "\n")
    .trim();
}

const EMAIL_RE = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
const BAD_EMAIL = /(example\.com|sentry|wixpress|\.png|\.jpg|\.webp|domain\.com|email\.com|godaddy)/i;
const PHONE_RE = /\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/g;

const BOOKING_TOOLS = [
  ["ServiceTitan", /servicetitan|scheduler\.servicetitan/i],
  ["Housecall Pro", /housecallpro|housecall pro/i],
  ["Jobber", /getjobber|jobber\.com/i],
  ["Calendly", /calendly\.com/i],
  ["Schedule Engine", /scheduleengine/i],
  ["generic", /book (now|online|a service|an appointment)|schedule (service|online|now)/i],
];
const CHAT_TOOLS = /podium|birdeye|intercom|drift\.com|livechat|tawk\.to|hubspot.*chat|webchat/i;

export function extractSignals(html = "", text = htmlToText(html)) {
  const emails = [...new Set((html.match(EMAIL_RE) || []).map((e) => e.toLowerCase()))]
    .filter((e) => !BAD_EMAIL.test(e));
  const phones = [...new Set((text.match(PHONE_RE) || []).map((p) => p.replace(/\D/g, "")))];
  const booking = BOOKING_TOOLS.filter(([, re]) => re.test(html)).map(([n]) => n);
  return {
    emails,
    phones,
    onlineBooking: booking.length > 0,
    bookingTools: booking,
    chatWidget: CHAT_TOOLS.test(html),
    says247: /24\s*\/\s*7|24 hours|24-hour|around the clock/i.test(text),
    emergencyService: /emergency/i.test(text),
    yearFounded: (text.match(/(?:since|established|est\.?|founded in)\s*(19\d{2}|20\d{2})/i) || [])[1] || null,
    financing: /financing/i.test(text),
    freeEstimates: /free (estimate|quote)/i.test(text),
  };
}

// Pick sentences that describe services, area and policies, capped for the agent's knowledge.
export function summarizeForAgent(text = "", maxChars = 6000) {
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l.length > 25 && l.length < 400);
  const keep = lines.filter((l) =>
    /(repair|install|replace|maintenance|tune|service|furnace|air condition|\bac\b|heat pump|duct|thermostat|water heater|plumb|drain|warranty|financ|serv(e|ing)|area|county|hours|emergency|licensed|insured|guarantee|estimate|price|\$)/i.test(l),
  );
  const out = [];
  let size = 0;
  for (const l of [...new Set(keep)]) {
    if (size + l.length > maxChars) break;
    out.push(l);
    size += l.length + 1;
  }
  return out.join("\n");
}

export async function readWebsite(url, { fetchImpl = fetch, extraPaths = ["/contact", "/services", "/about"] } = {}) {
  if (!url) return { url, ok: false, html: "", text: "", signals: extractSignals("") };
  const pages = [];
  const base = new URL(url);
  for (const path of ["", ...extraPaths]) {
    const target = path ? new URL(path, base).toString() : base.toString();
    try {
      const res = await fetchImpl(target, {
        headers: { "User-Agent": "Mozilla/5.0 (compatible; business-audit/1.0)" },
        redirect: "follow",
        signal: AbortSignal.timeout(15000),
      });
      if (res.ok && /html/i.test(res.headers.get("content-type") || "html")) pages.push(await res.text());
    } catch {
      // A missing subpage is normal; keep going.
    }
  }
  const html = pages.join("\n");
  const text = htmlToText(html);
  return { url, ok: pages.length > 0, html, text, signals: extractSignals(html, text) };
}
