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
