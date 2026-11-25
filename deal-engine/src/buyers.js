// Cash buyer list and matching. Import a CSV with columns like:
//   name, phone, email, zips (space or ; separated), max_price, min_beds, notes
const { normalizePhone } = require('./leads');

function importBuyers(rows) {
  return rows.map((r) => {
    const get = (...keys) => {
      for (const k of Object.keys(r)) if (keys.includes(k.toLowerCase().trim())) return r[k];
      return '';
    };
    return {
      name: get('name', 'buyer', 'company'),
      phone: normalizePhone(get('phone', 'mobile')),
      email: get('email'),
      zips: String(get('zips', 'zip codes', 'areas', 'zip')).split(/[\s;,]+/).filter(Boolean),
      maxPrice: Number(String(get('max_price', 'max price', 'budget')).replace(/[$,]/g, '')) || null,
      minBeds: Number(get('min_beds', 'min beds')) || null,
      notes: get('notes'),
    };
  }).filter((b) => b.name);
}

// Buyers who buy in the zip and can afford the buyer price, best fit first.
function matchBuyers(lead, analysis, buyers) {
  return buyers
    .filter((b) => !b.zips.length || b.zips.includes(lead.zip))
    .filter((b) => !b.maxPrice || !analysis.buyerPrice || b.maxPrice >= analysis.buyerPrice)
    .filter((b) => !b.minBeds || !lead.beds || lead.beds >= b.minBeds)
    .map((b) => ({ ...b, fit: (b.zips.includes(lead.zip) ? 2 : 0) + (b.maxPrice ? 1 : 0) }))
    .sort((a, b) => b.fit - a.fit);
}

module.exports = { importBuyers, matchBuyers };
