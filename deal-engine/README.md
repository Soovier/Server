# Deal engine: probate wholesaling on autopilot

Finds probate houses, prices them, drafts every letter, text and contract, books calls, and tracks
follow-ups. You do three things: **talk to sellers, tap Approve, and sign.**

Nothing is sent and nothing costs money until you approve it. Texts and AI calls only leave when
`DEAL_ENGINE_LIVE=1` **and** you approved that batch on the dashboard.

## How it works

| Step | Who | What happens |
| --- | --- | --- |
| 1. Import list | automatic | `cli.js import` reads any probate CSV (county clerk, PropStream, DealMachine), maps columns, merges duplicates |
| 2. Score | automatic | Ranks leads: probate +30, stacked lists, equity, vacancy, absentee heirs, tax delinquency |
| 3. Price | automatic | ARV, repair estimate, buyer price (70% ARV - repairs), your max offer and opening offer |
| 4. Draft outreach | automatic | Sympathy letter to the executor, follow-up texts/emails on a 3/7/14/30/60/90/180-day cadence |
| 5. Compliance | automatic | DNC scrub, opt-outs, 8 AM-9 PM local (stricter in FL/OK/TX), state daily caps, AI calls only with consent |
| 6. Approve | **you, one tap** | Dashboard shows each batch with a preview and what got blocked |
| 7. Seller calls in | automatic | ElevenLabs agent answers, gathers facts, books you ([elevenlabs-agent.md](elevenlabs-agent.md)) |
| 8. Talk | **you** | Call list sorted by score, with ARV, repairs, opening and max offer and a script |
| 9. Contract | automatic draft, **you sign** | Purchase agreement and assignment pre-filled; you sign in your e-sign app |
| 10. Cash buyer | automatic | Matches your buyers list by zip and budget |

## Setup

```bash
# from the repo root
npm install
export DEAL_ENGINE_TOKEN=$(openssl rand -hex 24)   # dashboard password
export DEAL_MY_NAME="Stephen" DEAL_COMPANY="Your Company LLC" DEAL_MY_PHONE="+1..." \
       DEAL_BOOKING_LINK="https://calendar.app.google/..." DEAL_TITLE_COMPANY="..." DEAL_BUYER_ENTITY="Your Company LLC"

node deal-engine/cli.js import path/to/probate-list.csv --list probate
node deal-engine/cli.js dnc path/to/dnc-download.txt       # from telemarketing.donotcall.gov
node deal-engine/cli.js buyers path/to/cash-buyers.csv
node deal-engine/cli.js run                                 # analyze + draft, sends nothing
npm run deals                                               # dashboard on http://127.0.0.1:3002/?token=...
```

Try it with the sample: `node deal-engine/cli.js import deal-engine/samples/probate-sample.csv`.

## Going live (only after you confirm)

| Variable | For |
| --- | --- |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | Follow-up texts (register 10DLC first) |
| `TWILIO_WEBHOOK_URL` | Public URL of `/webhooks/twilio/sms`, for STOP handling and replies |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `ELEVENLABS_PHONE_NUMBER_ID` | AI callbacks to sellers who asked for one |
| `DEAL_ENGINE_LIVE=1` | Lets approved batches actually send |

## Why calls, texts and contracts are not fully automatic

- **TCPA**: AI or prerecorded calls to cell phones need prior consent; fines are $500 to $1,500 per call or
  text. The FCC confirmed in 2024 that AI voices count. The engine blocks AI calls to anyone who has not
  contacted you first.
- **State law**: Florida, Oklahoma, Maryland, Texas and others add hours and daily caps. Some states require
  a license or disclosures to wholesale. Check yours.
- **Contracts** bind you to buy a house. The engine drafts; only you sign. Have an attorney approve the
  templates in `templates/` for your state. Probate sales may need court approval.

This is a tool, not legal advice.

## Tests

`npm run deals:test`
