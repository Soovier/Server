// Deal math for a wholesale assignment.
//
//   Max allowable offer (MAO) = ARV x buyer percentage - repairs - your assignment fee
//
// Cash buyers in most markets pay about 70% of ARV minus repairs. Your fee comes out of the
// gap between what the seller accepts and what the buyer will pay.

const DEFAULTS = {
  buyerPct: 0.70,          // what cash buyers pay as a share of ARV, before repairs
  targetFee: 10000,        // assignment fee you aim for
  minFee: 5000,            // below this the deal is not worth doing
  // Repair cost per square foot by condition (rough rehab ranges; tune to your market).
  repairPerSqft: { turnkey: 5, light: 20, medium: 35, heavy: 55, gut: 80 },
  defaultCondition: 'medium', // probate houses are often dated and need work
};

function conditionBucket(c, yearBuilt) {
  const s = String(c || '').toLowerCase();
  if (/gut|teardown|fire|condemn/.test(s)) return 'gut';
  if (/poor|heavy|distress|fixer/.test(s)) return 'heavy';
  if (/fair|average|dated|medium/.test(s)) return 'medium';
  if (/good|light/.test(s)) return 'light';
  if (/excellent|turnkey|updated|remodel/.test(s)) return 'turnkey';
  if (yearBuilt && yearBuilt < 1960) return 'heavy';
  return DEFAULTS.defaultCondition;
}

function estimateRepairs({ sqft, condition, yearBuilt }, opts = {}) {
  const rates = { ...DEFAULTS.repairPerSqft, ...(opts.repairPerSqft || {}) };
  const bucket = conditionBucket(condition, yearBuilt);
  const area = sqft || 1400; // typical single-family when the list has no size
  return { bucket, repairs: Math.round((area * rates[bucket]) / 500) * 500, assumedSqft: !sqft };
}

// ARV from comps when you have them, else the list's estimated value.
function estimateArv(lead, comps = []) {
  const usable = comps.filter((c) => c.price > 0 && c.sqft > 0);
  if (usable.length >= 3 && lead.sqft) {
    const perSqft = usable.map((c) => c.price / c.sqft).sort((a, b) => a - b);
    const median = perSqft[Math.floor(perSqft.length / 2)];
    return { arv: Math.round((median * lead.sqft) / 1000) * 1000, source: `${usable.length} comps` };
  }
  if (lead.estValue) return { arv: lead.estValue, source: 'list estimate (verify with comps)' };
  if (lead.assessedValue) return { arv: Math.round(lead.assessedValue * 1.15), source: 'assessed value x 1.15 (rough)' };
  return { arv: null, source: 'no value data' };
}

function analyzeDeal(lead, { comps = [], askingPrice = null, ...opts } = {}) {
  const cfg = { ...DEFAULTS, ...opts };
  const { arv, source } = estimateArv(lead, comps);
  const { bucket, repairs, assumedSqft } = estimateRepairs(lead, cfg);
  if (!arv) return { ok: false, reason: 'No ARV: pull comps before making an offer', repairs, condition: bucket };

  const buyerPrice = Math.round(arv * cfg.buyerPct - repairs);     // what a cash buyer will pay
  const mao = buyerPrice - cfg.targetFee;                           // the most you offer the seller
  const openingOffer = Math.round((mao * 0.85) / 1000) * 1000;      // leave room to negotiate up
  const liens = (lead.mortgageBalance || 0) + (lead.taxOwed || 0);
  const notes = [];
  if (assumedSqft) notes.push('Square footage missing: repairs assume 1,400 sqft.');
  if (!source.endsWith('comps')) notes.push('ARV is an estimate: confirm with 3+ sold comps within 0.5 mi and 6 months.');
  if (liens > mao) notes.push(`Liens (~$${liens.toLocaleString('en-US')}) exceed your max offer: the estate may be underwater.`);

  let grade;
  let expectedFee = null;
  if (askingPrice != null) {
    expectedFee = buyerPrice - askingPrice;
    grade = expectedFee >= cfg.targetFee ? 'A' : expectedFee >= cfg.minFee ? 'B' : 'pass';
  } else {
    grade = mao > liens && mao > 0 ? 'offer' : 'pass';
  }

  return {
    ok: true, arv, arvSource: source, condition: bucket, repairs, buyerPrice, mao, openingOffer,
    targetFee: cfg.targetFee, askingPrice, expectedFee, liens, grade, notes,
  };
}

module.exports = { analyzeDeal, estimateRepairs, estimateArv, conditionBucket, DEFAULTS };
