// One-page proposal the owner opens from the outreach email.
import { config } from "./config.mjs";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s = "") => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
const usd = (n) => `$${Math.round(n).toLocaleString("en-US")}`;

export function renderProposal(biz, audit, { talkUrl = "", agency = config.agency, pricing = config.pricing } = {}) {
  const a = audit.assumptions;
  const findings = audit.findings.map((f) => `<li>${esc(f.text)}</li>`).join("");
  const footer = [agency.name, agency.owner, agency.phone, agency.email].filter(Boolean).map(esc).join(" · ");
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(biz.name)}: missed-call report</title>
<style>
:root{--ink:#1d1d1f;--muted:#5f6368;--line:#e3e3e3;--accent:#0a6c4f;--bg:#fff;--tint:#f1f8f5}
@media (prefers-color-scheme:dark){:root{--ink:#f1f1f1;--muted:#a8a8a8;--line:#333;--accent:#4cc99c;--bg:#141414;--tint:#1c2622}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;line-height:1.2;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px}
.muted{color:var(--muted)}.big{font-size:36px;font-weight:700;color:var(--accent);margin:8px 0 0}
.card{border:1px solid var(--line);border-radius:12px;padding:20px;margin-top:16px}
.cta{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;font-weight:600;padding:12px 20px;border-radius:10px;margin:8px 8px 0 0}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 0;border-bottom:1px solid var(--line)}td:last-child{text-align:right;padding-left:12px}
.tint{background:var(--tint);border:none}
</style></head><body><main>
<p class="muted">Prepared for ${esc(biz.name)}${biz.city ? `, ${esc(biz.city)}` : ""}</p>
<h1>You may be missing about ${audit.missedCallsPerMonth} calls a month</h1>
<p class="muted">Based on public information about your business. Your real numbers may differ, and we're happy to check them with you.</p>

<div class="card tint">
<div class="muted">Estimated revenue from missed calls</div>
<div class="big">${usd(audit.lostRevenue.low)} to ${usd(audit.lostRevenue.high)} / month</div>
<div class="muted">An AI receptionist could realistically recover about ${usd(audit.recoverablePerMonth)} of that each month.</div>
</div>

<h2>What we noticed</h2>
<ul>${findings || "<li>Your call handling already looks strong. An AI receptionist would mainly cover overflow.</li>"}</ul>

<h2>Hear your AI receptionist</h2>
<p>We built a demo that already knows your services and hours from your website. Try it like a customer would: ask about a repair, an emergency, or booking a visit.</p>
${talkUrl ? `<a class="cta" href="${esc(talkUrl)}">Talk to your AI receptionist</a>` : ""}
${agency.bookingUrl ? `<a class="cta" href="${esc(agency.bookingUrl)}">Book a 15-minute call</a>` : ""}

<h2>What it does</h2>
<ul>
<li>Answers every call 24/7 in your company's name, including nights, weekends and while your team is on jobs.</li>
<li>Collects name, number, address and the problem, flags emergencies, and sends you the details right away.</li>
<li>Transfers urgent calls to you when you want, and tells callers it's an AI assistant.</li>
<li>Monthly report: calls answered, jobs requested, and estimated revenue saved.</li>
</ul>

<h2>Pricing</h2>
<div class="card"><table>
<tr><td>Setup, custom training and phone number</td><td>${usd(pricing.setup)} one time</td></tr>
<tr><td>AI receptionist, monitoring and monthly report</td><td>${usd(pricing.monthly)} / month</td></tr>
<tr><td>Contract</td><td>Month to month, cancel anytime</td></tr>
</table></div>

<h2>How we estimated this</h2>
<div class="card"><table>
<tr><td>Calls per day (from your ${biz.reviewCount || 0} Google reviews)</td><td>${a.callsPerDay}</td></tr>
<tr><td>Share of calls missed</td><td>${Math.round(a.missRate * 100)}%</td></tr>
<tr><td>Share of calls that are new jobs</td><td>${Math.round(a.newJobShare * 100)}%</td></tr>
<tr><td>Share of those that book</td><td>${Math.round(a.bookRate * 100)}%</td></tr>
<tr><td>Average job value</td><td>${usd(a.avgTicket)}</td></tr>
</table>
<p class="muted">The range shown is 60% to 140% of the estimate.</p></div>

<p class="muted" style="margin-top:40px">${footer}${agency.address ? `<br>${esc(agency.address)}` : ""}</p>
</main></body></html>`;
}
