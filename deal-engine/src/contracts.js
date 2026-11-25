// Fills contract templates from the deal. Output is a draft for you to review and sign in your
// e-sign app; nothing is ever signed or sent automatically.
const fs = require('node:fs');
const path = require('node:path');

const TEMPLATE_DIR = path.join(__dirname, '..', 'templates');
const usd = (n) => (n == null ? '{price}' : `$${Math.round(n).toLocaleString('en-US')}`);

function render(name, vars) {
  const tpl = fs.readFileSync(path.join(TEMPLATE_DIR, `${name}.md`), 'utf8');
  return tpl.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null && vars[k] !== '' ? String(vars[k]) : m));
}

function addDays(d, n) {
  return new Date(d.getTime() + n * 864e5).toISOString().slice(0, 10);
}

function purchaseAgreement(lead, { price, settings = {}, today = new Date() }) {
  return render('purchase-agreement', {
    date: today.toISOString().slice(0, 10),
    sellerName: lead.contactName || lead.ownerName,
    decedent: lead.ownerName,
    buyerEntity: settings.buyerEntity || settings.myName,
    address: lead.address, city: lead.city, state: lead.state, zip: lead.zip,
    price: usd(price),
    earnestMoney: usd(settings.earnestMoney ?? 500),
    titleCompany: settings.titleCompany,
    inspectionDays: settings.inspectionDays ?? 14,
    closingDate: addDays(today, settings.closingDays ?? 45),
  });
}

function assignmentAgreement(lead, { price, assignmentFee, assigneeName, contractDate, settings = {}, today = new Date() }) {
  return render('assignment', {
    date: today.toISOString().slice(0, 10),
    buyerEntity: settings.buyerEntity || settings.myName,
    assigneeName,
    sellerName: lead.contactName || lead.ownerName,
    address: lead.address, city: lead.city, state: lead.state, zip: lead.zip,
    contractDate, price: usd(price), assignmentFee: usd(assignmentFee),
    assigneeDeposit: usd(settings.assigneeDeposit ?? 5000),
    titleCompany: settings.titleCompany,
    closingDate: addDays(today, settings.assignmentClosingDays ?? 21),
  });
}

// Lists placeholders still unfilled, so you know what to complete before sending for signature.
function missingFields(text) {
  return [...new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]))];
}

module.exports = { purchaseAgreement, assignmentAgreement, missingFields };
