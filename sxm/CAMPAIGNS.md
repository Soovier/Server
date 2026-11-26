# SXM Campaigns: your own campaign platform

SXM Campaigns is your replacement for Content Rewards. Brands and streamers
fund campaigns, clippers post clips and submit them, views are checked, and
clippers get paid from the campaign budget. You take a platform fee on every
dollar a brand puts in (`PLATFORM_FEE_PERCENT`, 10% by default).

It's built on SXM: same code base, look and logo. SXM
submits to it automatically, so the 30-minute "paste the link" step is gone.

## How it works

| Who | Does |
|---|---|
| Brand / streamer | Signs up, creates a campaign (rate per 1K views, per-clip cap, minimum views, budget, allowed footage, rules, platforms), funds it by card, approves or rejects clips |
| Clipper | Signs up, verifies their accounts with a code in their bio, submits post links (or lets SXM do it), cashes out |
| You (admin) | See fees earned and money owed, approve payouts, verify Facebook/TikTok accounts, record off-site payments, ban cheaters |

**Views:** YouTube views are checked every hour through the YouTube API, and
the video must be on the clipper's verified channel. Instagram, Facebook and
TikTok don't show view counts to anyone but the account owner. For those, the
clipper (or SXM, from their own insights) reports views with a
screenshot, and the brand confirms the number before it pays.

**Money:** earnings = views ÷ 1,000 × rate, capped per clip, and never more
than what the brand has funded. Earnings only go up. When the budget is used
up, the campaign ends on its own.

## Setup checklist

### 1. Run it on your PC first (5 min)

```powershell
copy .env.campaigns.example .env      # or add the values to your existing .env
.venv\Scripts\python.exe -m sxm_campaigns  # http://localhost:8800
```

- [ ] Set `ADMIN_EMAIL` to your email, then sign up with it. That account is the admin.
- [ ] Set `SECRET_KEY` to a long random string.
- [ ] Create a test brand account in another browser, make a campaign, and fund
      it with **Record manual payment** from your admin account.

### 2. YouTube view checks (5 min, free)

- [ ] In the Google Cloud project from SETUP.md: APIs & Services → Credentials →
      **Create credentials → API key**. Restrict it to **YouTube Data API v3**.
- [ ] `YOUTUBE_API_KEY=...`

### 3. Put it online (15 min, about $7–20/month)

Other people can only use it once it's on the internet.

- [ ] Easiest: [Render](https://render.com) → New → **Blueprint** → pick the
      `Soovier/Server` repo. It reads `sxm/render.yaml`, builds
      `Dockerfile.campaigns`, and adds a 1 GB disk for the database and screenshots.
- [ ] Fill in the env vars it asks for: `BASE_URL` (the https address Render
      gives you, or your own domain), `ADMIN_EMAIL`, `YOUTUBE_API_KEY`.
- [ ] Optional: point a domain such as `campaigns.yourdomain.com` at it in
      Render → Settings → Custom domains, then update `BASE_URL`.
- [ ] Railway, Fly.io or any Docker host also work: build `Dockerfile.campaigns`
      and mount a volume at `/data`.

### 4. Take card payments and pay clippers (Stripe, ~20 min + verification)

- [ ] Create a [Stripe](https://dashboard.stripe.com/register) account and finish
      identity/business verification.
- [ ] Turn on **Connect** (Settings → Connect) and choose **Express** accounts.
      This lets clippers link a bank account for payouts.
- [ ] Developers → API keys → copy the **secret key** → `STRIPE_SECRET_KEY=`.
- [ ] Developers → Webhooks → **Add endpoint**:
      `https://<your site>/stripe/webhook`, event `checkout.session.completed`
      → copy the signing secret → `STRIPE_WEBHOOK_SECRET=`.
- [ ] Test with Stripe's test keys (`sk_test_...`) and card `4242 4242 4242 4242`
      before switching to live keys.

Without Stripe the site still works: brands pay you by any method, you press
**Record manual payment**, and you pay clippers yourself and mark payouts paid.

### 5. Connect SXM (2 min)

- [ ] On your SXM Campaigns site, sign up (or use your admin account) as the
      clipper that posts your clips, verify your YouTube and Instagram, then
      Profile → **Create API token**.
- [ ] In SXM's `.env`:
      ```
      CAMPAIGNS_SITE_URL=https://<your site>
      CAMPAIGNS_SITE_TOKEN=sxm_...
      ```
- [ ] Restart SXM. Live campaigns now sync in every run, their
      footage is clipped automatically, and every campaign post is submitted
      the moment it goes live. Instagram/Facebook views are sent after each
      analytics refresh.

### 6. Optional: automatic Instagram bio checks

- [ ] `META_IG_USER_ID` = your Instagram business account id (shown on
      SXM's Setup page) and `META_ACCESS_TOKEN` = a long-lived token
      with `instagram_basic`. Without these, you verify Instagram accounts by
      hand from the admin dashboard.

## Before you open it to the public

These are business steps the software can't do for you:

- **Terms of service and privacy policy.** You're holding brands' money and
  paying people, so get proper terms (a lawyer, or a reviewed template). Make
  brands confirm they own or have rights to the footage they list.
- **Taxes.** In the US, collect a W-9 from clippers and file 1099s for anyone
  paid $600+ in a year. Stripe Connect can collect tax info and file 1099s for you.
- **Fraud.** Watch for bought views: sudden spikes, posts with views but no
  likes, brand-new accounts. You can ban users from the admin dashboard, and
  brands only pay out on views they've confirmed or that the YouTube API reports.
- **Getting brands.** The site makes money only when campaigns are funded. Start
  with the streamers you already clip for.
