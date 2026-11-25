import { test } from "node:test";
import assert from "node:assert/strict";
import { writeEmail, missingSenderDetails } from "../lib/email.mjs";
import { emptyState, dailyLimit, buildBatch, approveBatch, markSent, suppress, isSuppressed, classifyReply, handleReply } from "../lib/outreach.mjs";
import { auditBusiness } from "../lib/audit.mjs";
import { config } from "../lib/config.mjs";

const agency = { name: "Cool Calls AI", owner: "Stephen Test", email: "stephen@coolcalls.test", phone: "555-0100", address: "PO Box 1, Los Angeles, CA 90001", bookingUrl: "https://cal.test/15min" };
Object.assign(config.agency, agency);

const wk = [1, 2, 3, 4, 5].map((d) => ({ open: { day: d, hour: 8, minute: 0 }, close: { day: d, hour: 17, minute: 0 } }));
const rec = (name, email, reviewCount = 80, extra = {}) => ({
  biz: { name, emails: email ? [email] : [] },
  audit: auditBusiness({ name, trade: "hvac", reviewCount, hoursPeriods: wk }, { emergencyService: true, ...extra }),
  agent: { talkUrl: `https://talk.test/${name.length}` },
});

test("first email is personal, has the demo link and a CAN-SPAM footer", () => {
  const e = writeEmail(rec("Acme HVAC", "owner@acme.test"), { agency });
  assert.equal(e.to, "owner@acme.test");
  assert.equal(e.subject, "Missed calls at Acme HVAC");
  assert.match(e.text, /emergency service/);
  assert.match(e.text, /https:\/\/talk\.test\//);
  assert.match(e.text, /PO Box 1, Los Angeles/);
  assert.match(e.text, /Reply "stop"/);
  assert.ok(e.text.split(/\s+/).length < 170, "keep it short");
});

test("follow-up references the original and offers the booking link", () => {
  const e = writeEmail(rec("Acme HVAC", "owner@acme.test"), { step: 1, agency });
  assert.match(e.subject, /^Re: /);
  assert.match(e.text, /cal\.test\/15min/);
});

test("refuses to write without sender address (CAN-SPAM)", () => {
  assert.deepEqual(missingSenderDetails({ ...agency, address: "" }), ["address"]);
  assert.throws(() => writeEmail(rec("A", "a@a.test"), { agency: { ...agency, address: "" } }), /CAN-SPAM/);
});

test("warm-up ramps sending per inbox", () => {
  const s = { ...emptyState(), warmupStart: "2026-12-01" };
  assert.equal(dailyLimit(emptyState(), "2026-12-01"), 0);
  assert.equal(dailyLimit(s, "2026-12-01"), 5);
  assert.equal(dailyLimit(s, "2026-12-09", 2), 30);
  assert.equal(dailyLimit(s, "2026-12-16"), 30);
  assert.equal(dailyLimit(s, "2027-01-15"), 50);
});

test("batch respects limit, ranking, suppression and missing emails", () => {
  const s = { ...emptyState(), warmupStart: "2026-12-01" };
  suppress(s, "@blocked.test");
  const records = [
    rec("Small", "a@small.test", 30),
    rec("Big", "b@big.test", 300),
    rec("Blocked", "c@blocked.test", 300),
    rec("NoEmail", "", 300),
    rec("Mid", "d@mid.test", 80),
    rec("Extra1", "e@x1.test", 80), rec("Extra2", "f@x2.test", 80), rec("Extra3", "g@x3.test", 80),
  ];
  const b = buildBatch(s, records, { today: "2026-12-01" });
  assert.equal(b.limit, 5);
  assert.equal(b.items.length, 5);
  assert.equal(b.items[0].name, "Big");
  assert.ok(!b.items.some((i) => i.email === "c@blocked.test"));
  assert.ok(b.skipped.some((x) => x.name === "Blocked"));
  assert.ok(b.skipped.some((x) => x.name === "NoEmail"));
  assert.equal(b.approved, false);
});

test("sends need approval, and follow-ups come due after 3 days", () => {
  const s = { ...emptyState(), warmupStart: "2026-12-01" };
  const records = [rec("Acme", "owner@acme.test")];
  const b = buildBatch(s, records, { today: "2026-12-01" });
  assert.throws(() => markSent(s, b, b.items[0], "2026-12-01T17:00:00Z"), /unapproved/);
  assert.throws(() => approveBatch(s, b), /person/);
  approveBatch(s, b, "Stephen");
  markSent(s, b, b.items[0], "2026-12-01T17:00:00Z");
  assert.equal(buildBatch(s, records, { today: "2026-12-02" }).items.length, 0);
  const later = buildBatch(s, records, { today: "2026-12-04" });
  assert.equal(later.items.length, 1);
  assert.equal(later.items[0].step, 1);
});

test("reply classification", () => {
  assert.equal(classifyReply("Please STOP emailing me"), "unsubscribe");
  assert.equal(classifyReply("Not interested, thanks"), "not_interested");
  assert.equal(classifyReply("How much does it cost per month?"), "interested");
  assert.equal(classifyReply("Automatic reply: I'm out of the office"), "out_of_office");
  assert.equal(classifyReply("Who are you exactly?"), "question");
});

test("an unsubscribe is permanent and blocks later sends", () => {
  const s = { ...emptyState(), warmupStart: "2026-12-01" };
  const records = [rec("Acme", "owner@acme.test")];
  const b = approveBatch(s, buildBatch(s, records, { today: "2026-12-01" }), "Stephen");
  markSent(s, b, b.items[0], "2026-12-01T17:00:00Z");
  assert.deepEqual(handleReply(s, "Owner@Acme.test", "remove me please", "2026-12-02T10:00:00Z"), { label: "unsubscribe", action: "suppress" });
  assert.ok(isSuppressed(s, "owner@acme.test"));
  assert.equal(buildBatch(s, records, { today: "2026-12-05" }).items.length, 0);
  assert.throws(() => markSent(s, b, b.items[0], "2026-12-05T17:00:00Z"), /do-not-contact/);
});

test("an interested reply asks for the booking link and Stephen", () => {
  const s = emptyState();
  assert.equal(handleReply(s, "x@y.test", "Sounds good, call me tomorrow", "t").action, "send_booking_link_and_notify");
  assert.equal(s.contacts["x@y.test"].status, "interested");
});
