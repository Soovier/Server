# SXM

AI clipping + posting + campaign tracking that runs on your own PC.

**Finds** streamer content (Content Rewards campaign sources, Twitch, YouTube, any URL) →
**auto-clips** it into captioned 9:16 Shorts/Reels → puts them in a **review queue**
(or **auto-approves**) → **schedules posts** to Instagram Reels, your Facebook Page and
YouTube Shorts → helps you **submit to Content Rewards campaigns** inside the 30-minute
window → shows **analytics** per channel and combined: post times, views, watch time,
watch hours and revenue.

Setup checklist (API keys, OAuth apps): **[SETUP.md](SETUP.md)**.

**SXM Campaigns** (`sxm_campaigns/`) is your own campaign platform, a replacement for
Content Rewards built on the same code: brands fund campaigns, clippers submit,
views are verified, payouts are tracked, and SXM submits to it
automatically. See **[CAMPAIGNS.md](CAMPAIGNS.md)**.

## Quick start (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1   # once
.\start.bat                                              # opens http://localhost:8787
```

macOS/Linux: `./start.sh`. Keep the window open (or let the installer add it to
startup) and it runs on its own schedule.

## How it works

| Step | What runs | When |
|---|---|---|
| Find | Campaign source URLs, Twitch Helix (top clips + VODs), YouTube Data API uploads | `DISCOVER_CRON` (every 3 h) |
| Clip | The 12-stage clip engine below | right after Find |
| Review | Dashboard queue: watch, edit hook/caption/hashtags, approve/reject. Auto-approve toggle with a minimum score | whenever you want |
| Post | Instagram Reels + Facebook Page Reels (Meta Graph API resumable upload), YouTube Shorts (Data API resumable upload) to every channel you switched on, each in its own `POST_SLOTS` | checked every minute |
| Submit | Alert + "1-click submit" for campaign posts (copies link, opens campaign) | right after a campaign post goes live |
| Analytics | IG media insights, FB video insights, YouTube Analytics (views, minutes watched, avg view duration, estimated revenue) | `ANALYTICS_CRON` (every 6 h) |

### The clip engine (12 stages)

| # | Stage | How |
|---|---|---|
| 1 | Footage analysis + scene detection | FFmpeg scene-change scores on a 6 fps proxy, per-second loudness |
| 2 | Viral moment + reaction detection | Hype words in the Whisper transcript, sudden loudness jumps (screams, laughs) |
| 3 | Highlight scoring + selection | Windows scored on peaks, opening hook, talk, hype, action (cuts/sec), reactions; top non-overlapping picks |
| 4 | Smart clip extraction | Start on a word boundary or a clean scene cut, end on a sentence |
| 5 | AI reframing | Facecam detected with OpenCV (or `FACECAM_BOX`) → facecam on top, gameplay below; else blurred full frame; black bars trimmed |
| 6 | Hooks + captions | Claude ranks moments and writes hook, caption, hashtags (fallback without a key) |
| 7 | Subtitle sync | Word-level timings, 3 words at a time, active word highlighted |
| 8 | Motion + visual enhancement | Zoom punch-ins on reaction peaks, caption pop, hook fade-in |
| 9 | Audio optimisation | High-pass, compression, -14 LUFS, click-free fades |
| 10 | Quality control | Size, length, audio present, black screen, silence |
| 11 | Copyright risk | Loud-but-nobody-talking music signal + sung-lyric hints + Claude's read (GTA radio gets claimed) |
| 12 | Export | 1080x1920 H.264/AAC + thumbnail into Review |

Clips that fail QC or score above `auto_approve_max_copyright_risk` (0.5) wait in Review even with auto-approve on.
No n8n needed: the built-in scheduler runs the whole pipeline.

### Import queue

Already have the stream? Open **Import** and drag in as many videos as you like
(or paste links, or copy files into `data/inbox/<streamer>/`). Pick the streamer,
clips per video and format (Auto, Facecam + gameplay, Full frame, Center crop);
SXM clips them one after another right away with live progress, then they land
in Review with captions written, ready to copy or download.

### Several channels

Setup lists every connected channel. YouTube: press **Add a channel** once per
channel (Google's picker includes Brand Account channels). Facebook: one login
brings in every Page you manage plus linked Instagram accounts. Switch on the
ones SXM should post to; each gets every approved clip, and Analytics shows a
card per channel.

Commands:

```
python -m sxm                    # dashboard + scheduler
python -m sxm run pipeline       # find + clip + auto-approve once
python -m sxm run publish        # post anything that's due
python -m sxm run analytics      # refresh metrics
```

## Why FFmpeg instead of DaVinci Resolve or CapCut

The goal is unattended, scheduled editing. CapCut has no API, and DaVinci's
scripting needs the Studio app open with a GUI session, so neither can run
headless at 3 a.m. FFmpeg + Whisper are free, scriptable, and produce the
standard short-form look: 1080×1920, blurred-background or center-crop
reframe, big word-by-word captions with the active word highlighted, a hook
banner for the first 3.5 s, loudness normalized to -14 LUFS. You can still
open any clip in CapCut/DaVinci for a manual touch-up; files are in
`data/media/clips/`.

## Content Rewards and permission

Content Rewards has no public API and its terms say "We do not post on your
behalf" and require submitting within 30 minutes of posting. Driving their
logged-in site with a bot would risk tripping their fraud scoring and getting
the account banned, so the submit step is assisted (one paste + click), not
automated.

Their terms also limit footage use to the campaign's clip, and their guide notes
that a platform's clip button doesn't give you permission to repost for money.
So clips from campaign sources (or streamers you mark as having given
permission) can auto-approve; everything else is tagged "needs permission" and
waits for you.

## Layout

```
sxm/
  discovery.py      find sources
  clipper.py        download, signals, transcript, moment picking, AI pass
  editor.py         FFmpeg render + ASS captions
  review.py         queue, auto-approve, slot scheduling
  publishers/       meta.py (IG + FB), youtube.py, publish_due()
  campaigns.py      Content Rewards tracking, assisted submit, earnings
  analytics.py      metric sync + per-channel/combined rollups
  jobs.py           APScheduler jobs
  web.py            dashboard (FastAPI + Jinja)
  campaign_site.py talks to your SXM Campaigns site
sxm_campaigns/            SXM Campaigns website: auth, campaigns, ledger, verify, Stripe, API
tests/              pytest (mocked APIs + real FFmpeg renders + full marketplace flows)
```

Data lives in `data/` (SQLite `sxm.db` + media). Tokens are stored there
locally and never leave your machine except to the platform they belong to.

## Tests

```
pip install -r requirements.txt pytest
python -m pytest -q
```

## Trending

The **Trending** tab shows three carousels you can swipe through: streamer videos blowing up right now (last 3 days), GTA (this week and all-time), and other games going viral (YouTube's Gaming chart plus Twitch's top games). "Right now" is ranked by views per hour. Cards that match one of your campaigns get a 💰 badge, and **✂ Clip this** sends the video straight to the Import queue. It refreshes every `TRENDING_REFRESH_HOURS` (default 6, about 500 of YouTube's 10,000 free daily quota units each time) using your connected channel or an optional `YOUTUBE_API_KEY`. Twitch shows up once `TWITCH_CLIENT_ID`/`SECRET` are set. Kick has no public API, so Kick links go in through Import.

## Inventory and Auto-post

The **Inventory** tab shows every video at every step: queued (with download % and time left), processing, processed, about to post, about to auto-post, and just posted. It refreshes by itself.

**Auto-post**: pick an account, how often (default every 4 hours), and where clips come from: your own ready-made clips (drop them in on the Inventory page) plus approved SXM clips, or AI clips from one of the Trending carousels. An account on Auto-post gets its clips only from the timer, one at a time, while clips last. Carousel clips still follow your auto-approve and permission rules before they post.

## Find clips for a campaign

Campaigns → **🔎 Find clips for a campaign**. Paste the campaign link and/or describe it. SXM reads the page (Claude pulls out the creator, rules, required tags and pay when `ANTHROPIC_API_KEY` is set), then finds videos: links on the campaign page first, the creator's top recent YouTube videos, YouTube searches, and their Twitch VODs and clips. You get at least 10 to pick from (top 10 pre-ticked), choose the format (Facecam + gameplay by default), how many clips (10 by default) and the clip length, and SXM saves the campaign and clips them at the front of the queue. Campaign footage counts as permitted, so auto-approve can post it.

## Clip length

Every place that makes clips (Import, campaign finder, Auto-post from Trending) has a clip length picker: presets from 15–30s up to 3 minutes, or type your own shortest/longest. Clips start and end on full sentences inside the range. What you pick on Import becomes your default.

## Analytics

The Analytics tab (it used to be called Stats) shows live numbers for every connected YouTube channel, not just the clips SXM posted. Every 3 hours SXM reads each channel's subscribers, total views and video count from the YouTube Data API, the daily views, watch time, subscribers, likes and revenue from the YouTube Analytics API, and the top videos. Press **Refresh** to pull them right away. YouTube's daily numbers run about 2 days behind. Pick 7D, 28D, 90D or 1Y, or filter to one channel. If a channel can't be read, its row shows why (usually reconnecting it on the Connect page fixes it).
