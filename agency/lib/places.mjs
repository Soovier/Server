// Lead finder: Google Places API (New) text search.
// Docs: https://developers.google.com/maps/documentation/places/web-service/text-search
import { config, requireKey } from "./config.mjs";

const FIELDS = [
  "places.id",
  "places.displayName",
  "places.formattedAddress",
  "places.nationalPhoneNumber",
  "places.websiteUri",
  "places.rating",
  "places.userRatingCount",
  "places.businessStatus",
  "places.regularOpeningHours.weekdayDescriptions",
  "places.regularOpeningHours.periods",
  "places.primaryType",
  "nextPageToken",
].join(",");

// Names that usually mean a franchise or national chain: they buy software centrally, so skip them.
const CHAIN_PATTERNS = [
  /one hour heating/i, /service experts/i, /aire serv/i, /mr\.? rooter/i, /roto-?rooter/i,
  /benjamin franklin plumbing/i, /home depot/i, /lowe'?s/i, /sears/i, /ars\b/i, /rescue rooter/i,
];

export function isLikelyChain(name = "") {
  return CHAIN_PATTERNS.some((re) => re.test(name));
}

export function normalizePlace(p) {
  return {
    placeId: p.id,
    name: p.displayName?.text || "",
    address: p.formattedAddress || "",
    phone: p.nationalPhoneNumber || "",
    website: p.websiteUri || "",
    rating: p.rating ?? null,
    reviewCount: p.userRatingCount ?? 0,
    status: p.businessStatus || "",
    hoursText: p.regularOpeningHours?.weekdayDescriptions || [],
    hoursPeriods: p.regularOpeningHours?.periods || [],
    type: p.primaryType || "",
  };
}

export async function findLeads({ trade, city, max = 20, fetchImpl = fetch }) {
  const key = requireKey("GOOGLE_PLACES_API_KEY", config.placesApiKey);
  const leads = [];
  let pageToken;
  do {
    const body = { textQuery: `${trade} in ${city}`, pageSize: Math.min(20, max - leads.length) };
    if (pageToken) body.pageToken = pageToken;
    const res = await fetchImpl("https://places.googleapis.com/v1/places:searchText", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELDS },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`Places API ${res.status}: ${await res.text()}`);
    const data = await res.json();
    for (const p of data.places || []) {
      const lead = normalizePlace(p);
      if (lead.status && lead.status !== "OPERATIONAL") continue;
      if (isLikelyChain(lead.name)) continue;
      leads.push({ ...lead, trade, city });
    }
    pageToken = data.nextPageToken;
  } while (pageToken && leads.length < max);
  return leads.slice(0, max);
}
