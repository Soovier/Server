# AI receptionist agency: demo machine

Week 1 of the automation plan. It finds local service businesses, audits how much revenue they likely lose to missed calls, builds each one a demo AI phone receptionist in ElevenLabs, and writes a one-page proposal with a "talk to your AI receptionist" link.

Nothing here contacts a business. Outreach (week 2) is a separate step that waits for a daily human approval.

## Setup

Node 20 or newer, no dependencies.

```bash
cd agency
cp .env.example .env   # fill in keys and your agency details, then export them
npm test
```

| Variable | What it is for |
| --- | --- |
| `GOOGLE_PLACES_API_KEY` | Lead search (Places API, New) |
| `ELEVENLABS_API_KEY` | Creating receptionist agents |
| `ELEVENLABS_VOICE_ID` | Receptionist voice |
| `AGENCY_*` | Your name, email, phone, mailing address and booking link on proposals |
| `PRICE_SETUP`, `PRICE_MONTHLY` | Pricing shown on proposals |

## Use

```bash
# 1. Find leads (skips closed businesses and known franchises)
node cli.mjs find-leads --trade hvac --city "Los Angeles, CA" --max 20

# 2. Audit them, write proposals, and rank them (add --create-agents to build demo receptionists)
node cli.mjs run --leads out/leads-hvac-los-angeles-ca.json --limit 10 --create-agents
```

Output goes to `out/` (git-ignored): `out/<business>/proposal.html`, `out/<business>/record.json`, and `out/pipeline.csv` ranked best lead first.

## How the audit works

The estimate is deliberately conservative and every assumption is printed on the proposal:

```
lost revenue / month = calls per day x 30 x miss rate x share that are new jobs x booking rate x average job value
```

- Calls per day comes from Google review volume (6, 12, 20 or 30).
- Miss rate starts at 20% (10% if they already answer 24/7), +25% with no after-hours answering, +5% with no online booking, capped at 50%.
- The AI is assumed to recover 70% of that.
- Shops that already answer 24/7, use chat or online booking, or have 1,500+ reviews (likely a call center) rank last.

## Files

| File | Job |
| --- | --- |
| `lib/places.mjs` | Lead search and chain filtering |
| `lib/site.mjs` | Reads a website: emails, phones, booking and chat tools, 24/7 claims, service lines |
| `lib/audit.mjs` | Missed-call revenue estimate and lead score |
| `lib/prompt.mjs` | Receptionist instructions (AI and recording disclosure, gas-leak safety rule) |
| `lib/elevenlabs.mjs` | Creates the agent through the ElevenLabs API |
| `lib/proposal.mjs` | The one-page proposal |
| `lib/pipeline.mjs` | Runs one lead through all of the above |
| `cli.mjs` | Command line entry point |
