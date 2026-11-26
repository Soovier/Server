# Krypt Marketo: account setup checklist

Everything below is done by you, once, in your own browser. You never give
anyone a password: each platform gives you an app ID and secret that go in
your `.env` file, and you log in through the platform's own consent screen
from the **Setup** page. Plan on about 45 minutes in total.

The redirect URLs below assume the default `KRYPT_BASE_URL=http://localhost:8787`.

---

## 0. Install (5 min)

- [ ] Download this folder to your PC (e.g. `Documents\krypt-marketo`).
- [ ] Open PowerShell in that folder and run
      `powershell -ExecutionPolicy Bypass -File .\install.ps1`
      This installs FFmpeg and Python if needed, the Python packages, creates
      `.env`, and can add Krypt Marketo to Windows startup.
- [ ] Open `.env` in Notepad. You'll fill it in during the steps below.

## 1. Instagram + Facebook Page (Meta Graph API, ~15 min)

Prerequisites:
- [ ] Your Instagram is a **Business** or **Creator** account
      (Instagram app → Settings → Account type and tools → Switch to professional account).
- [ ] That Instagram account is **linked to your Facebook Page**
      (Facebook Page → Settings → Linked accounts → Instagram → Connect).

Create the app:
- [ ] Go to <https://developers.facebook.com/apps> → **Create app**.
- [ ] Use case: **Other** → app type **Business** → name it "Krypt Marketo".
- [ ] In the app dashboard, add the product **Facebook Login for Business**
      (or **Facebook Login**) and **Instagram** (the "API setup with Facebook login" option).
- [ ] Facebook Login → Settings → **Valid OAuth Redirect URIs**:
      `http://localhost:8787/oauth/meta/callback` → Save.
- [ ] App settings → Basic → copy **App ID** and **App Secret** into `.env`:
      ```
      META_APP_ID=...
      META_APP_SECRET=...
      ```
- [ ] Leave the app in **Development** mode. Because you're the app's admin, it
      can use every permission on your own Page and Instagram without Meta's App
      Review. App Review is only needed if other people will log in.

Connect:
- [ ] Start Krypt Marketo, open **Setup → Instagram → Connect**, log in with
      Facebook, and tick your Page and your Instagram account when asked. Grant
      every permission listed. Both Instagram and Facebook show "connected".

Permissions requested: `pages_show_list, pages_read_engagement, pages_manage_posts,
read_insights, business_management, instagram_basic, instagram_content_publish,
instagram_manage_insights`.

## 2. YouTube Shorts (Google Cloud, ~15 min)

- [ ] Go to <https://console.cloud.google.com> → create a project "Krypt Marketo".
- [ ] APIs & Services → Library → enable **YouTube Data API v3** and **YouTube Analytics API**.
- [ ] Google Auth Platform (OAuth consent screen) → **External** → app name
      "Krypt Marketo", your email as support + developer contact.
- [ ] Audience → **Publish app** (move to "In production"). If you leave it in
      "Testing", Google expires your login every 7 days. You'll see a "Google
      hasn't verified this app" screen when connecting; click **Advanced → Go to
      Krypt Marketo**. That's expected for a personal app.
- [ ] Clients → **Create client** → type **Web application** → Authorized redirect URI:
      `http://localhost:8787/oauth/google/callback` → copy the ID and secret into `.env`:
      ```
      GOOGLE_CLIENT_ID=...
      GOOGLE_CLIENT_SECRET=...
      ```
- [ ] Setup page → **YouTube → Connect** → pick the channel you post Shorts on.

**Important, YouTube upload audit:** Google locks videos uploaded by new,
unaudited API projects to **private**. To post publicly, submit the
[YouTube API Services audit form](https://support.google.com/youtube/contact/yt_api_form)
for your project (say it's a personal tool that uploads Shorts to your own
channel). Approval usually takes a few days to a few weeks. Until then Shorts
upload as private, and you can make them public in YouTube Studio. The same
form lets you ask for more quota: the default 10,000 units/day covers only a
handful of uploads a day.

Revenue: `estimatedRevenue` only returns data if the channel is in the YouTube
Partner Program. Otherwise revenue shows $0 and views/watch time still work.

## 3. Twitch streamer discovery (~5 min, optional but recommended)

- [ ] <https://dev.twitch.tv/console/apps> → **Register Your Application**:
      OAuth Redirect URL `http://localhost`, category **Application Integration**,
      client type **Confidential**.
- [ ] Copy the Client ID, click **New Secret**, put both in `.env`:
      ```
      TWITCH_CLIENT_ID=...
      TWITCH_CLIENT_SECRET=...
      TWITCH_STREAMERS=streamer1,streamer2
      ```

YouTube source channels (long streams/videos to clip) only need channel IDs:
`YOUTUBE_SOURCE_CHANNEL_IDS=UCxxxx,UCyyyy` (uses your YouTube login from step 2).

Kick and anything else: paste the video URL on the **Sources** page.

## 4. Content Rewards (no keys, ~5 min)

Content Rewards has **no API**, and its terms say they don't post on your behalf
and that you must submit a post's link **within 30 minutes** of publishing it.
So Krypt Marketo does everything up to the final click:

- [ ] On contentrewards.com, connect your Instagram and YouTube accounts in your
      profile (they verify you own them).
- [ ] Join a campaign. In Krypt Marketo → **Campaigns → Add a campaign**, paste
      the campaign page URL, the source footage links from the brief, the $ per
      1K views, required hashtags/mentions, and the brief's rules.
- [ ] Krypt Marketo auto-clips that footage, posts on schedule, then alerts you.
      Press **1-click submit**: it copies the post link and opens the campaign
      page, so you paste and submit.

## 5. Optional extras

- [ ] **AI moment picking + captions:** an Anthropic API key from
      <https://console.anthropic.com> → `ANTHROPIC_API_KEY=`. Without it, clips are
      picked by audio energy + transcript signals and get template captions.
- [ ] **Phone alerts:** install the free **ntfy** app, subscribe to a random
      topic name, and set `NOTIFY_WEBHOOK_URL=https://ntfy.sh/<that-topic>`
      (or paste a Discord webhook URL).
- [ ] **Dashboard password:** `KRYPT_DASHBOARD_PASSWORD=` if others use your PC.
- [ ] **Watermark:** `BRAND_HANDLE=@yourhandle`.

## 6. Go live

- [ ] Restart Krypt Marketo (close the window, run `start.bat`).
- [ ] Setup page shows all three accounts connected and no missing keys.
- [ ] Schedule page → **Find + clip now** to test. Clips appear in **Review**.
- [ ] Approve a few, watch them post at the next slot, and check **Analytics**
      after the next analytics refresh (every 6 hours by default).
- [ ] When you trust the picks: **Review → Auto-approve** on, with a minimum score.

## Things that stay manual (by design)

- Final submit on Content Rewards (no API; their terms).
- Clips from streamers who aren't in a campaign and haven't given you
  permission. They're tagged "needs permission" and never auto-approve; add
  the streamer under Sources → "Streamers who've given you permission" once
  they have.
- Facebook and Instagram payouts. Meta doesn't expose per-Reel earnings in the
  API, so those show $0 and Content Rewards payouts are tracked on the Campaigns page.
