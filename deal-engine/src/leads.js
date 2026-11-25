// Normalizes any lead list (PropStream, DealMachine, county export) into one shape,
// de-duplicates it, and scores each lead by how motivated the seller is likely to be.
const crypto = require('node:crypto');

// Column aliases seen in common exports. Matching is case- and punctuation-insensitive.
const ALIASES = {
  ownerName: ['owner', 'owner name', 'owner 1 full name', 'owner1', 'name', 'decedent', 'decedent name', 'deceased'],
  // Probate: the person you talk to is the personal representative (executor / administrator), not the owner.
  contactName: ['personal representative', 'pr name', 'executor', 'executor name', 'administrator', 'petitioner', 'heir name', 'contact name'],
  contactMailing: ['pr address', 'personal representative address', 'executor address', 'petitioner address', 'contact address'],
  ownerFirst: ['owner first name', 'owner 1 first name', 'first name'],
  ownerLast: ['owner last name', 'owner 1 last name', 'last name'],
  address: ['property address', 'address', 'situs address', 'site address', 'street'],
  city: ['property city', 'city', 'situs city'],
  state: ['property state', 'state', 'st'],
  zip: ['property zip', 'zip', 'zip code', 'postal code'],
  mailingAddress: ['mailing address', 'owner mailing address', 'mail address'],
  phone: ['phone', 'phone 1', 'mobile', 'cell', 'phone number', 'primary phone'],
  phone2: ['phone 2', 'landline', 'secondary phone'],
  email: ['email', 'email 1', 'email address'],
  beds: ['beds', 'bedrooms', 'br'],
  baths: ['baths', 'bathrooms', 'ba'],
  sqft: ['sqft', 'square feet', 'building sqft', 'living sqft', 'living area'],
  yearBuilt: ['year built', 'yr built'],
  estValue: ['estimated value', 'est value', 'avm', 'market value', 'zestimate'],
  assessedValue: ['assessed value', 'tax assessed value', 'total assessed value'],
  equityPct: ['equity %', 'equity percent', 'estimated equity %', 'equity percentage'],
  mortgageBalance: ['mortgage balance', 'loan balance', 'open mortgage balance', 'est mortgage balance'],
  taxDelinquentYears: ['tax delinquent years', 'years delinquent', 'delinquent years'],
  taxOwed: ['tax owed', 'delinquent amount', 'amount due', 'taxes owed'],
  lastSaleDate: ['last sale date', 'sale date', 'last sold'],
  vacant: ['vacant', 'vacancy', 'is vacant'],
  absentee: ['absentee', 'absentee owner', 'out of state owner'],
  condition: ['condition', 'property condition'],
  listType: ['list', 'list type', 'lead type', 'source list'],
  filingDate: ['filing date', 'case date', 'date filed', 'recording date'],
  caseNumber: ['case number', 'case #', 'case no'],
  consent: ['consent', 'sms consent', 'opted in'],
};

const norm = (s) => String(s).toLowerCase().replace(/[^a-z0-9%#]+/g, ' ').trim();

function mapColumns(headers) {
  const byNorm = new Map(headers.map((h) => [norm(h), h]));
  const map = {};
  for (const [field, aliases] of Object.entries(ALIASES)) {
    for (const a of aliases) {
      if (byNorm.has(norm(a))) { map[field] = byNorm.get(norm(a)); break; }
    }
  }
  return map;
}

const num = (v) => {
  if (v == null || v === '') return null;
  const n = Number(String(v).replace(/[$,%\s]/g, ''));
  return Number.isFinite(n) ? n : null;
};
const bool = (v) => /^(y|yes|true|1|x)$/i.test(String(v ?? '').trim());

function normalizePhone(p) {
  const d = String(p || '').replace(/\D/g, '');
  if (d.length === 10) return `+1${d}`;
  if (d.length === 11 && d.startsWith('1')) return `+${d}`;
  return null;
}

function normalizeAddress(a) {
  return String(a || '').toUpperCase()
    .replace(/[.,#]/g, ' ')
    .replace(/\bSTREET\b/g, 'ST').replace(/\bAVENUE\b/g, 'AVE').replace(/\bROAD\b/g, 'RD')
    .replace(/\bDRIVE\b/g, 'DR').replace(/\bLANE\b/g, 'LN').replace(/\bCOURT\b/g, 'CT')
    .replace(/\bBOULEVARD\b/g, 'BLVD').replace(/\bPLACE\b/g, 'PL')
    .replace(/\s+/g, ' ').trim();
}

function leadKey(lead) {
  return crypto.createHash('sha1')
    .update(`${normalizeAddress(lead.address)}|${String(lead.zip || '').slice(0, 5)}`)
    .digest('hex').slice(0, 12);
}

function normalizeRecord(raw, colMap, defaults = {}) {
  const get = (f) => (colMap[f] ? raw[colMap[f]] : undefined);
  const ownerName = get('ownerName') || [get('ownerFirst'), get('ownerLast')].filter(Boolean).join(' ');
  const phones = [normalizePhone(get('phone')), normalizePhone(get('phone2'))].filter(Boolean);
  const lead = {
    ownerName: ownerName || '',
    ownerFirst: get('ownerFirst') || (ownerName ? ownerName.split(/\s+/)[0] : ''),
    contactName: get('contactName') || '',
    contactMailing: get('contactMailing') || '',
    address: get('address') || '',
    city: get('city') || '',
    state: String(get('state') || defaults.state || '').toUpperCase().slice(0, 2),
    zip: String(get('zip') || '').slice(0, 5),
    mailingAddress: get('mailingAddress') || '',
    phones,
    email: get('email') || '',
    beds: num(get('beds')),
    baths: num(get('baths')),
    sqft: num(get('sqft')),
    yearBuilt: num(get('yearBuilt')),
    estValue: num(get('estValue')),
    assessedValue: num(get('assessedValue')),
    equityPct: num(get('equityPct')),
    mortgageBalance: num(get('mortgageBalance')),
    taxDelinquentYears: num(get('taxDelinquentYears')),
    taxOwed: num(get('taxOwed')),
    lastSaleDate: get('lastSaleDate') || '',
    vacant: bool(get('vacant')),
    absentee: bool(get('absentee')),
    condition: (get('condition') || '').toLowerCase(),
    listType: (get('listType') || defaults.listType || 'unknown').toLowerCase(),
    filingDate: get('filingDate') || '',
    caseNumber: get('caseNumber') || '',
    consent: bool(get('consent')),
  };
  if (lead.equityPct == null && lead.estValue && lead.mortgageBalance != null) {
    lead.equityPct = Math.round(((lead.estValue - lead.mortgageBalance) / lead.estValue) * 100);
  }
  if (!lead.absentee && lead.mailingAddress && lead.address) {
    lead.absentee = !normalizeAddress(lead.mailingAddress).startsWith(normalizeAddress(lead.address));
  }
  lead.id = leadKey(lead);
  return lead;
}

// Distress list weights. A lead on several lists ("stacked") scores higher.
const LIST_WEIGHTS = {
  probate: 30, inherited: 30, 'tax delinquent': 25, 'tax lien': 25, 'pre-foreclosure': 25,
  foreclosure: 25, 'code violation': 20, eviction: 18, divorce: 20, 'tired landlord': 15,
  vacant: 15, absentee: 8, 'high equity': 10, 'free and clear': 12, unknown: 0,
};

function scoreLead(lead) {
  const reasons = [];
  let score = 0;
  const lists = String(lead.listType).split(/[;|]/).map((s) => s.trim()).filter(Boolean);
  for (const l of lists) {
    const w = Object.entries(LIST_WEIGHTS).find(([k]) => l.includes(k));
    if (w && w[1]) { score += w[1]; reasons.push(`${w[0]} list (+${w[1]})`); }
  }
  if (lists.length > 1) { score += 10 * (lists.length - 1); reasons.push(`stacked on ${lists.length} lists`); }
  if (lead.equityPct != null) {
    if (lead.equityPct >= 50) { score += 20; reasons.push('50%+ equity'); }
    else if (lead.equityPct >= 30) { score += 10; reasons.push('30%+ equity'); }
    else if (lead.equityPct < 15) { score -= 20; reasons.push('under 15% equity (hard to wholesale)'); }
  }
  if (lead.vacant) { score += 12; reasons.push('vacant'); }
  if (lead.absentee) { score += 8; reasons.push('absentee owner'); }
  if (lead.taxDelinquentYears >= 2) { score += 10; reasons.push(`${lead.taxDelinquentYears} yrs tax delinquent`); }
  if (/poor|fair|distress|fixer/.test(lead.condition)) { score += 8; reasons.push(`condition: ${lead.condition}`); }
  if (lead.lastSaleDate) {
    const yrs = (Date.now() - Date.parse(lead.lastSaleDate)) / (365.25 * 864e5);
    if (yrs >= 15) { score += 8; reasons.push('owned 15+ years'); }
  }
  if (!lead.phones.length) reasons.push('no phone yet: needs skip trace');
  return { score: Math.max(0, Math.min(100, score)), reasons };
}

function importLeads(rows, { defaults = {}, existing = [] } = {}) {
  if (!rows.length) return { leads: [], duplicates: 0, skipped: 0, columns: {} };
  const colMap = mapColumns(Object.keys(rows[0]));
  const seen = new Map(existing.map((l) => [l.id, l]));
  const out = [];
  let duplicates = 0, skipped = 0;
  for (const raw of rows) {
    const lead = normalizeRecord(raw, colMap, defaults);
    if (!lead.address) { skipped++; continue; }
    if (seen.has(lead.id)) {
      // Merge: a property on a second list is a stronger lead.
      const prev = seen.get(lead.id);
      if (lead.listType !== 'unknown' && !prev.listType.includes(lead.listType)) {
        prev.listType = `${prev.listType};${lead.listType}`;
      }
      prev.phones = [...new Set([...prev.phones, ...lead.phones])];
      Object.assign(prev, scoreLead(prev));
      duplicates++;
      continue;
    }
    Object.assign(lead, scoreLead(lead), { stage: 'new', createdAt: new Date().toISOString(), history: [] });
    seen.set(lead.id, lead);
    out.push(lead);
  }
  return { leads: out, duplicates, skipped, columns: colMap };
}

module.exports = { importLeads, normalizePhone, normalizeAddress, scoreLead, mapColumns, LIST_WEIGHTS };
