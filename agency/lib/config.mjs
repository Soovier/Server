// Central configuration, read from environment variables.
const env = process.env;

export const config = {
  elevenLabsApiKey: env.ELEVENLABS_API_KEY || "",
  placesApiKey: env.GOOGLE_PLACES_API_KEY || "",
  voiceId: env.ELEVENLABS_VOICE_ID || "",
  agency: {
    name: env.AGENCY_NAME || "Your Agency",
    owner: env.AGENCY_OWNER_NAME || "",
    email: env.AGENCY_EMAIL || "",
    phone: env.AGENCY_PHONE || "",
    address: env.AGENCY_MAILING_ADDRESS || "",
    bookingUrl: env.AGENCY_BOOKING_URL || "",
  },
  pricing: {
    setup: Number(env.PRICE_SETUP || 997),
    monthly: Number(env.PRICE_MONTHLY || 497),
  },
};

export function requireKey(name, value) {
  if (!value) throw new Error(`Missing ${name}. Set it in the environment (see .env.example).`);
  return value;
}
