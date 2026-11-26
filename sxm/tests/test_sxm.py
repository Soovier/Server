import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from sxm import analytics, campaigns, clipper, review
from sxm.config import settings
from sxm.db import DB, set_db, utcnow
from sxm.editor import Word, build_ass, probe_duration, render_clip
from sxm.publishers import meta, publish_due, youtube


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    d = DB(":memory:")
    set_db(d)
    return d


def connect_all(db):
    """One switched-on channel per platform."""
    for pf, acct in (("instagram", "IGX"), ("facebook", "PGX"), ("youtube", "UCX")):
        db.save_channel(pf, acct, enabled=True, name=f"my {pf}", access_token="T")


def make_clip(db, campaign_id=None, streamer="xqc", score=5.0, video="x.mp4"):
    sid = db.insert("sources", {"platform": "manual", "external_id": f"e{utcnow()}{score}{streamer}{campaign_id}",
                                "url": "http://x", "streamer": streamer, "campaign_id": campaign_id,
                                "found_at": utcnow()})
    return db.insert("clips", {"source_id": sid, "campaign_id": campaign_id, "start_s": 0, "end_s": 30,
                               "score": score, "hook": "WAIT", "caption": "cap", "hashtags": "#shorts",
                               "video_path": video, "status": "pending_review", "created_at": utcnow()})


# ---------------- review / scheduling ----------------

def test_next_slots_are_future_and_unique(db):
    now = datetime(2026, 11, 26, 20, 0, tzinfo=timezone.utc)  # 3pm New York
    slots = review.next_slots(db, "instagram", 5, now=now)
    assert len(slots) == 5 and len(set(slots)) == 5
    assert all(s > now for s in slots)
    assert slots == sorted(slots)


def test_approve_schedules_each_platform(db):
    connect_all(db)
    cid = make_clip(db)
    ids = review.approve(db, cid)
    assert len(ids) == 3
    assert {p["platform"] for p in db.q("SELECT platform FROM posts")} == {"instagram", "facebook", "youtube"}
    assert db.one("SELECT status FROM clips WHERE id=?", (cid,))["status"] == "scheduled"
    # Second clip gets a different slot on the same platform
    cid2 = make_clip(db, streamer="b")
    review.approve(db, cid2)
    times = [p["scheduled_at"] for p in db.q("SELECT scheduled_at FROM posts WHERE platform='youtube'")]
    assert len(set(times)) == 2


def test_auto_approve_respects_toggle_and_permission(db):
    connect_all(db)
    camp = campaigns.add_campaign(db, name="C", url="https://contentrewards.com/c/x")
    ok = make_clip(db, campaign_id=camp)
    no_perm = make_clip(db, streamer="random")
    low = make_clip(db, campaign_id=camp, score=1, streamer="z")
    assert db.get_setting("auto_approve") == "1"  # on by default
    db.set_setting("auto_approve", "0")
    assert review.auto_approve_pending(db) == "auto-approve off"
    db.set_setting("auto_approve", "1")
    db.set_setting("auto_approve_min_score", "2")
    review.auto_approve_pending(db)
    st = {r["id"]: r["status"] for r in db.q("SELECT id,status FROM clips")}
    assert st[ok] == "scheduled" and st[no_perm] == "pending_review" and st[low] == "pending_review"
    db.set_setting("permitted_streamers", "Random")
    review.auto_approve_pending(db)
    assert db.one("SELECT status FROM clips WHERE id=?", (no_perm,))["status"] == "scheduled"


def test_reject_cancels_posts(db):
    connect_all(db)
    cid = make_clip(db)
    review.approve(db, cid)
    review.reject(db, cid)
    assert {p["status"] for p in db.q("SELECT status FROM posts")} == {"cancelled"}


# ---------------- publishing ----------------

def test_publish_due_posts_and_queues_submission(db, monkeypatch, tmp_path):
    connect_all(db)
    camp = campaigns.add_campaign(db, name="C", url="https://contentrewards.com/c/x", platforms="instagram")
    cid = make_clip(db, campaign_id=camp)
    review.approve(db, cid)
    db.run("UPDATE posts SET scheduled_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    from sxm import publishers
    monkeypatch.setitem(publishers.PUBLISHERS, "instagram", lambda d, c, ch=None: {"external_id": "ig1", "permalink": "https://ig/p/1"})
    monkeypatch.setitem(publishers.PUBLISHERS, "facebook", lambda d, c, ch=None: {"external_id": "fb1", "permalink": "https://fb/1"})

    def boom(d, c, ch=None):
        raise RuntimeError("quota")
    monkeypatch.setitem(publishers.PUBLISHERS, "youtube", boom)
    assert publish_due(db) == "published 2, failed 1"
    yt = db.one("SELECT * FROM posts WHERE platform='youtube'")
    assert yt["status"] == "scheduled" and yt["attempts"] == 1 and "quota" in yt["error"]
    pend = campaigns.pending_submissions(db)
    assert len(pend) == 1 and pend[0]["platform"] == "instagram" and not pend[0]["late"]
    campaigns.mark_submitted(db, pend[0]["id"])
    assert campaigns.pending_submissions(db) == []


def test_publish_due_respects_pause(db):
    db.set_setting("paused", "1")
    assert publish_due(db) == "paused"


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_instagram_resumable_flow(db, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    db.save_account("instagram", account_id="IG", access_token="TOK")
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.host, req.url.path))
        if req.url.path.endswith("/IG/media"):
            return httpx.Response(200, json={"id": "CONT"})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["Authorization"] == "OAuth TOK" and req.headers["file_size"] == "100"
            return httpx.Response(200, json={"success": True})
        if req.url.path.endswith("/CONT"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if req.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "MEDIA"})
        if req.url.path.endswith("/MEDIA"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/x"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    res = meta.publish_instagram(db, {"video_path": str(video), "caption": "hi", "hashtags": "#a"},
                                 http=mock_client(handler), poll_s=0)
    assert res == {"external_id": "MEDIA", "permalink": "https://instagram.com/reel/x"}
    assert any(h == "rupload.facebook.com" for _, h, _ in calls)


def test_facebook_reels_flow(db, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    db.save_account("facebook", account_id="PAGE", access_token="PT")
    phases = []

    def handler(req):
        if req.url.path.endswith("/PAGE/video_reels"):
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            phases.append(body["upload_phase"])
            if body["upload_phase"] == "start":
                return httpx.Response(200, json={"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/v24.0/V1"})
            return httpx.Response(200, json={"success": True})
        if req.url.host == "rupload.facebook.com":
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)

    res = meta.publish_facebook(db, {"video_path": str(video), "caption": "c"}, http=mock_client(handler))
    assert phases == ["start", "finish"] and res["external_id"] == "V1"


def test_youtube_upload_and_metrics(db, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 10)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.save_account("youtube", account_id="UC1", access_token="AT", refresh_token="RT", expires_at=future)

    def handler(req):
        if req.url.path == "/upload/youtube/v3/videos":
            meta_body = json.loads(req.content)
            assert "#shorts" in meta_body["snippet"]["title"].lower()
            return httpx.Response(200, headers={"Location": "https://upload.example/session"}, json={})
        if req.url.host == "upload.example":
            return httpx.Response(200, json={"id": "VID"})
        if req.url.path == "/youtube/v3/videos":
            return httpx.Response(200, json={"items": [{"id": "VID", "statistics": {"viewCount": "1500", "likeCount": "40"}}]})
        if req.url.path == "/v2/reports":
            if "estimatedRevenue" in req.url.params["metrics"]:
                return httpx.Response(403, json={"error": {"message": "Forbidden"}})
            return httpx.Response(200, json={
                "columnHeaders": [{"name": n} for n in ["video", "views", "estimatedMinutesWatched", "averageViewDuration", "shares"]],
                "rows": [["VID", 1200, 300, 14, 3]]})
        return httpx.Response(404)

    http = mock_client(handler)
    res = youtube.publish_youtube(db, {"video_path": str(video), "hook": "Insane play", "caption": "c", "hashtags": "#gaming"}, http=http)
    assert res["permalink"].endswith("/shorts/VID")
    m = youtube.youtube_metrics(db, ["VID"], datetime.now().date(), http=http)["VID"]
    assert m["views"] == 1500 and m["watch_time_s"] == 18000 and m["avg_view_s"] == 14 and m["revenue_usd"] == 0


def test_youtube_token_refresh(db):
    db.save_account("youtube", account_id="UC1", access_token="old", refresh_token="RT", expires_at="2000-01-01T00:00:00+00:00")
    http = mock_client(lambda req: httpx.Response(200, json={"access_token": "new", "expires_in": 3600}))
    assert youtube.ensure_fresh_token(db, http=http) == "new"
    assert db.get_account("youtube")["access_token"] == "new"


# ---------------- analytics ----------------

def test_summary_totals_and_campaign_earnings(db):
    camp = campaigns.add_campaign(db, name="C", url="u", rate_per_1k=2.0, max_payout=50, platforms="instagram,youtube")
    cid = make_clip(db, campaign_id=camp)
    now = utcnow()
    for plat, views, wt, rev in [("instagram", 10000, 36000, 0), ("youtube", 5000, 7200, 3.5)]:
        pid = db.insert("posts", {"clip_id": cid, "platform": plat, "scheduled_at": now, "status": "posted",
                                  "external_id": plat, "posted_at": now, "permalink": "p"})
        db.insert("metrics", {"post_id": pid, "captured_at": now, "views": views, "watch_time_s": wt, "revenue_usd": rev})
        sid = db.insert("submissions", {"campaign_id": camp, "post_id": pid})
        campaigns.mark_submitted(db, sid)
    s = analytics.summary(db, 30)
    assert s["channels"]["instagram"]["views"] == 10000
    assert s["channels"]["instagram"]["watch_hours"] == 10.0
    assert s["combined"]["views"] == 15000 and s["combined"]["watch_hours"] == 12.0
    assert s["combined"]["revenue_usd"] == 3.5
    assert s["campaign_estimated"] == 30.0  # 15k views * $2/1k
    assert s["combined"]["total_income"] == 33.5
    assert s["by_hour"] and s["daily"]


# ---------------- clipping ----------------

def test_score_and_pick_moments():
    energy = [1.0] * 300
    for i in range(120, 130):
        energy[i] = 10.0
    words = [Word(121.0, 121.5, "NO WAY!"), Word(122, 122.4, "bro")]
    wins = clipper.score_windows(energy, words, 300, 30)
    moments = clipper.pick_moments(wins, 2)
    assert any(m.start <= 121 < m.end for m in moments)
    assert len(moments) == 2 and moments[0].end + 30 <= moments[1].start or moments[1].end + 30 <= moments[0].start


def test_ass_captions():
    ass = build_ass([Word(0, .4, "hello"), Word(.4, .8, "world"), Word(.8, 1.2, "{x}")], "big hook", 5)
    assert "BIG HOOK" in ass and "HELLO" in ass and "{x}" not in ass.split("[Events]")[1].replace("{\\c", "")


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg missing")
def test_render_real_video(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(src)], check=True)
    for mode in ("blur", "crop"):
        out = render_clip(src, tmp_path / f"out_{mode}.mp4", 2, 6, [Word(0.2, 0.8, "test"), Word(0.9, 1.5, "clip")],
                          hook="hook", mode=mode)
        assert 3.5 < probe_duration(out) < 4.6
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
        assert info.strip() == "1080,1920"
    assert len(clipper.audio_energy(src)) in (8, 9)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg missing")
def test_clip_source_end_to_end(db, tmp_path, monkeypatch):
    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=15:duration=150",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=150", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    sid = db.insert("sources", {"platform": "manual", "external_id": "a", "url": "u", "streamer": "Tester",
                                "title": "Big stream", "local_path": str(src), "status": "downloaded", "found_at": utcnow()})
    monkeypatch.setattr(clipper, "transcribe", lambda p: [])
    monkeypatch.setattr(settings, "clips_per_source", 2)
    ids = clipper.clip_source(db, sid)
    assert len(ids) == 2
    for c in db.q("SELECT * FROM clips"):
        assert c["status"] == "pending_review" and Path(c["video_path"]).exists()
        assert "#Tester" in c["hashtags"]


# ---------------- web ----------------

def test_web_pages_render(db):
    from fastapi.testclient import TestClient
    from sxm.web import app
    connect_all(db)
    camp = campaigns.add_campaign(db, name="Camp", url="https://contentrewards.com/c/x")
    make_clip(db, campaign_id=camp)
    c = TestClient(app)
    for path in ["/", "/review", "/schedule", "/campaigns", "/sources", "/setup", "/api/summary", "/stats?days=0&channel=youtube", "/stats", "/api/home", "/?days=30"]:
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:500])
    assert "SXM" in c.get("/").text
    r = c.post("/settings/auto-approve", data={"enabled": "1", "min_score": "3"}, follow_redirects=False)
    assert r.status_code == 303 and db.get_setting("auto_approve") == "1"
    clip = db.one("SELECT id FROM clips")["id"]
    c.post(f"/review/{clip}", data={"action": "approve", "hook": "NEW HOOK"})
    assert db.one("SELECT hook,status FROM clips WHERE id=?", (clip,)) == {"hook": "NEW HOOK", "status": "scheduled"}
    r = c.post("/campaigns", data={"name": "X", "url": "https://contentrewards.com/c/y", "platforms": ["youtube"],
                                   "source_urls": "https://twitch.tv/videos/1"})
    assert r.status_code == 200
    assert db.one("SELECT platforms FROM campaigns WHERE name='X'")["platforms"] == "youtube"
    assert c.get("/media/clips/../sxm.db").status_code == 404


def test_multiple_channels_each_get_a_post(db):
    connect_all(db)
    second = db.save_channel("youtube", "UC2", enabled=True, name="second channel", access_token="T2")
    off = db.save_channel("youtube", "UC3", name="not chosen", access_token="T3")  # connected but switched off
    cid = make_clip(db)
    review.approve(db, cid)
    yt = db.q("SELECT channel_id FROM posts WHERE platform='youtube'")
    assert {r["channel_id"] for r in yt} == {db.one("SELECT id FROM channels WHERE account_id='UCX'")["id"], second}
    assert off not in {r["channel_id"] for r in yt}


def test_approved_clip_waits_until_a_channel_is_on(db):
    cid = make_clip(db)
    assert review.approve(db, cid) == []
    assert db.one("SELECT status FROM clips WHERE id=?", (cid,))["status"] == "approved"
    db.save_channel("youtube", "UC9", enabled=True, name="yt", access_token="T")
    review.schedule_waiting(db)
    assert db.one("SELECT status FROM clips WHERE id=?", (cid,))["status"] == "scheduled"


def test_google_login_adds_channel(db, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "sec")

    def handler(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600})
        return httpx.Response(200, json={"items": [{"id": "UCbrand", "snippet": {"title": "Brand Ch", "customUrl": "@brand"},
                                                    "statistics": {"subscriberCount": "12"}}]})
    youtube.finish_login(db, "code", http=mock_client(handler))
    youtube.finish_login(db, "code", http=mock_client(handler))  # reconnect does not duplicate
    chans = db.channels("youtube")
    assert len(chans) == 1 and chans[0]["name"] == "Brand Ch" and chans[0]["enabled"] == 1
    assert chans[0]["extra"]["subscribers"] == 12


def test_meta_login_lists_every_page_for_choosing(db, monkeypatch):
    monkeypatch.setattr(settings, "meta_app_id", "a")
    monkeypatch.setattr(settings, "meta_app_secret", "b")

    def handler(req):
        if req.url.path.endswith("oauth/access_token"):
            return httpx.Response(200, json={"access_token": "U"})
        return httpx.Response(200, json={"data": [
            {"id": "P1", "name": "Page One", "access_token": "PT1",
             "instagram_business_account": {"id": "IG1", "username": "one"}},
            {"id": "P2", "name": "Page Two", "access_token": "PT2"}]})
    res = meta.finish_login(db, "code", http=mock_client(handler))
    assert res["needs_choice"] and len(db.channels("facebook")) == 2 and len(db.channels("instagram")) == 1
    assert not db.channels(enabled_only=True)  # you pick which ones on Setup


def test_old_database_migrates_to_channels(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE accounts (platform TEXT PRIMARY KEY, account_id TEXT, name TEXT, access_token TEXT,
            refresh_token TEXT, expires_at TEXT, extra TEXT DEFAULT '{}', connected_at TEXT);
        INSERT INTO accounts VALUES ('youtube','UCold','Old Ch','AT','RT',NULL,'{}','2026-01-01');
        CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER NOT NULL, platform TEXT NOT NULL,
            scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled', external_id TEXT, permalink TEXT,
            posted_at TEXT, error TEXT, attempts INTEGER DEFAULT 0, UNIQUE(clip_id, platform));
        INSERT INTO posts(clip_id, platform, scheduled_at, status) VALUES (1,'youtube','2026-01-01','posted');
    """)
    con.commit()
    con.close()
    d = DB(path)
    ch = d.channels("youtube")
    assert ch and ch[0]["name"] == "Old Ch" and ch[0]["enabled"] == 1
    assert d.one("SELECT channel_id FROM posts")["channel_id"] == ch[0]["id"]
    fk = d.q("PRAGMA foreign_key_list(metrics)")
    assert fk[0]["table"] == "posts"


# ---------------- clip engine stages ----------------

def test_scene_cuts_and_action_scoring(tmp_path):
    from sxm import analysis
    energy = [1.0] * 200
    calm = clipper.score_windows(energy, [], 200, 30)
    busy = clipper.score_windows(energy, [], 200, 30, cuts=[float(t) for t in range(100, 130)])
    best = max(busy, key=lambda w: w[2])
    assert 70 <= best[0] <= 100 and best[2] > max(c[2] for c in calm)
    assert analysis.snap_to_cut(50.0, [10.0, 49.2, 60.0]) == 49.2
    assert analysis.snap_to_cut(50.0, [30.0]) == 50.0
    z = [0.0] * 20
    z[10] = 3.0
    assert analysis.reaction_bursts(z) == [10]


def test_copyright_risk_signal():
    from sxm import analysis
    z = [1.0] * 40  # loud the whole time
    talk = [Word(float(i), i + 0.9, "word") for i in range(0, 40)]
    assert analysis.music_ratio(z, talk, 0, 40) == 0.0
    assert analysis.music_ratio(z, [], 0, 40) == 1.0          # loud and nobody talking -> music
    assert analysis.copyright_risk(1.0, "") == 1.0
    assert analysis.copyright_risk(0.1, "just talking") == 0.0
    assert analysis.copyright_risk(0.1, "", ai_risk=0.9) == 0.45


def test_facecam_box_parsing():
    from sxm import analysis
    b = analysis.parse_box("0.72,0.04,0.26,0.26")
    assert b and b.ffmpeg_crop().startswith("crop=iw*0.2600:ih*0.2600")
    assert analysis.parse_box("") is None and analysis.parse_box("2,0,1,1") is None


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg missing")
def test_stack_layout_zoom_and_qc(tmp_path):
    from sxm import analysis, qc
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-shortest", "-c:v", "libx264",
                    "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    out = render_clip(src, tmp_path / "stack.mp4", 1, 7, [Word(0.2, 0.8, "go")], hook="hook", mode="stack",
                      facecam=analysis.Box(0.7, 0.05, 0.25, 0.25), punches=[2.0])
    res = qc.check_clip(out, 6)
    assert res.status == "pass", res.text
    risky = qc.check_clip(out, 6, copyright_risk=0.9)
    assert risky.status == "warn" and "copyright" in risky.text
    assert qc.check_clip(tmp_path / "missing.mp4", 6).status == "fail"


def test_auto_approve_holds_failed_qc_and_risky_clips(db):
    connect_all(db)
    camp = campaigns.add_campaign(db, name="C", url="https://x")
    good = make_clip(db, campaign_id=camp)
    bad_qc = make_clip(db, campaign_id=camp, streamer="q")
    risky = make_clip(db, campaign_id=camp, streamer="r")
    db.update("clips", bad_qc, qc_status="fail", qc_notes="no audio")
    db.update("clips", risky, copyright_risk=0.8)
    review.auto_approve_pending(db)
    st = {r["id"]: r["status"] for r in db.q("SELECT id,status FROM clips")}
    assert st[good] == "scheduled" and st[bad_qc] == "pending_review" and st[risky] == "pending_review"


def test_paste_text_and_downloads(db, tmp_path):
    from fastapi.testclient import TestClient
    from sxm.web import app
    cid = make_clip(db)
    db.update("clips", cid, hook="He did not see that", caption="Insane getaway", hashtags="#gta6 #shorts #gta6")
    clip = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    paste = review.paste_text(clip)
    assert paste["caption"] == "Insane getaway\n\n#gta6 #shorts"
    assert paste["title"].endswith("#shorts") and len(paste["title"]) <= 100
    (settings.media_dir / "clips" / f"clip_{cid}.mp4").write_bytes(b"0" * 100)
    with TestClient(app) as c:
        page = c.get("/downloads")
        assert page.status_code == 200 and "Insane getaway" in page.text and f"/download/{cid}" in page.text
        r = c.get(f"/download/{cid}")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        assert "sxm-" in r.headers["content-disposition"] and "he-did-not-see-that" in r.headers["content-disposition"]
        assert c.get("/downloads?show=new").text.count(f"/download/{cid}") == 0
        assert c.get("/download/9999").status_code == 404
    assert db.one("SELECT downloaded_at FROM clips WHERE id=?", (cid,))["downloaded_at"]


def test_import_queue_files_links_and_inbox(db, tmp_path, monkeypatch):
    from sxm import importer
    monkeypatch.setattr(importer, "wake", lambda: None)
    f = tmp_path / "xqc stream.mp4"
    f.write_bytes(b"0" * 50)
    sid = importer.add_file(db, f, streamer="xqc", layout="stack", clips=5)
    assert importer.add_file(db, f) is None  # same file twice is ignored
    src = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    assert src["status"] == "downloaded" and src["layout"] == "stack" and src["clips_wanted"] == 5 and src["priority"] > 0
    ids = importer.add_links(db, "https://www.twitch.tv/videos/1 and\nhttps://youtu.be/abc).", streamer="kai")
    assert len(ids) == 2 and db.one("SELECT url FROM sources WHERE id=?", (ids[1],))["url"] == "https://youtu.be/abc"
    sub = importer.inbox_dir() / "jynxzi"
    sub.mkdir(parents=True, exist_ok=True)
    v = sub / "vod.mkv"
    v.write_bytes(b"1" * 10)
    import os, time as _t
    os.utime(v, (_t.time() - 60, _t.time() - 60))
    assert importer.scan_inbox(db) == 1
    assert db.one("SELECT streamer FROM sources WHERE local_path=?", (str(v.resolve()),))["streamer"] == "jynxzi"
    q = importer.queue(db)
    assert [r["state"] for r in q] == ["waiting"] * 4 and q[0]["position"] == 1
    importer.remove(db, ids[0])
    assert db.one("SELECT status FROM sources WHERE id=?", (ids[0],))["status"] == "skipped"
    importer.retry(db, ids[0])
    assert db.one("SELECT status FROM sources WHERE id=?", (ids[0],))["status"] == "new"


def test_import_upload_endpoint(db, monkeypatch):
    from fastapi.testclient import TestClient
    from sxm import importer
    from sxm.web import app
    monkeypatch.setattr(importer, "wake", lambda: None)
    with TestClient(app) as c:
        r = c.post("/import/upload", params={"name": "../my VOD!!.mp4", "streamer": "xqc", "layout": "blur", "clips": 4},
                   content=b"x" * 1000)
        assert r.status_code == 200 and r.json()["file"] == "my VOD.mp4"
        assert (importer.uploads_dir() / "my VOD.mp4").stat().st_size == 1000
        page = c.get("/import")
        assert page.status_code == 200 and "my VOD" in page.text and "#1 in line" in page.text
        assert c.get("/api/queue").json()[0]["layout"] == "blur"
        r = c.post("/import/links", data={"links": "https://youtu.be/zzz", "streamer": "", "layout": "auto", "clips": 2},
                   follow_redirects=False)
        assert r.status_code == 303


# ---------------- trending ----------------

def _yt_video(vid, title, views, hours_ago, channel="Streamer", secs=3600, live=True, hd=True):
    pub = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    v = {"id": vid, "snippet": {"title": title, "channelTitle": channel, "publishedAt": pub,
                               "thumbnails": {"high": {"url": f"https://i.ytimg.com/{vid}.jpg"}}},
         "statistics": {"viewCount": str(views)},
         "contentDetails": {"duration": f"PT{secs}S", "definition": "hd" if hd else "sd"}}
    if live:
        v["liveStreamingDetails"] = {"actualStartTime": pub}
    return v


def trending_http(calls):
    vids = {
        "s1": _yt_video("s1", "INSANE stream moment", 200_000, 50, "Kai"),
        "s2": _yt_video("s2", "full stream rage", 120_000, 4, "Ishow"),          # fewer views but way faster
        "s3": _yt_video("s3", "short thing", 999_999, 2, "Tiny", secs=40),        # a Short: dropped
        "s4": _yt_video("s4", "potato quality", 500_000, 3, "Old", hd=False),      # SD: dropped
        "g1": _yt_video("g1", "GTA 6 trailer reaction", 3_000_000, 30, "Rp", live=False),
        "g2": _yt_video("g2", "GTA RP funniest moments", 90_000_000, 20000, "Legend", live=False),
        "c1": _yt_video("c1", "Fortnite new season", 800_000, 10, "Ninja", live=False),
        "c2": _yt_video("c2", "GTA chart video", 5_000_000, 10, "Gta guy", live=False),  # GTA: not in 'other games'
    }

    def handler(req: httpx.Request):
        calls.append(str(req.url))
        p = req.url.params
        if req.url.path.endswith("/search"):
            q, after = p["q"], p.get("publishedAfter")
            if "stream highlights" in q:
                ids = ["s1", "s2", "s3", "s4"]
            elif "GTA" in q:
                ids = ["g1"] if after else ["g2", "g1"]
            else:
                ids = ["c1"]
            return httpx.Response(200, json={"items": [{"id": {"videoId": i}} for i in ids]})
        if req.url.path.endswith("/videos"):
            if p.get("chart") == "mostPopular":
                return httpx.Response(200, json={"items": [vids["c1"], vids["c2"]]})
            return httpx.Response(200, json={"items": [vids[i] for i in p["id"].split(",")]})
        if "id.twitch.tv" in str(req.url):
            return httpx.Response(200, json={"access_token": "tw"})
        if req.url.path.endswith("/games/top"):
            return httpx.Response(200, json={"data": [{"id": "509658", "name": "Just Chatting"}, {"id": "32982", "name": "GTA V"},
                                                      {"id": "33214", "name": "Fortnite"}]})
        if req.url.path.endswith("/clips"):
            gid = p.get("game_id")
            pub = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return httpx.Response(200, json={"data": [{"id": f"tc{gid}{x}", "url": f"https://clips.twitch.tv/tc{gid}{x}",
                                                       "title": f"clip {gid}", "broadcaster_name": f"bc{gid}{x}",
                                                       "thumbnail_url": "", "created_at": pub, "view_count": 40_000 - i,
                                                       "duration": 30} for i, x in enumerate(("", "b"))]})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_trending_rows_rank_by_velocity_and_filter(db, monkeypatch):
    from sxm import trending
    monkeypatch.setattr(settings, "youtube_api_key", "KEY")
    monkeypatch.setattr(settings, "twitch_client_id", "id")
    monkeypatch.setattr(settings, "twitch_client_secret", "sec")
    calls = []
    data = trending.build(db, trending_http(calls))
    rows = data["rows"]
    yt_streams = [c["id"] for c in rows["streamers"]["now"] if c["platform"] == "youtube"]
    assert yt_streams == ["s2", "s1"]                     # 30k/hr beats 4k/hr; Short and SD gone
    assert any(c["platform"] == "twitch" for c in rows["streamers"]["now"])
    assert [c["id"] for c in rows["gta"]["all"]] == ["g2"]  # g1 already shown in "now"
    assert rows["gta"]["now"][0]["id"] == "g1"
    assert "tc32982" in [c["id"] for c in rows["gta"]["now"]]
    games_now = [c["id"] for c in rows["games"]["now"]]
    assert "c1" in games_now and "c2" not in games_now and "tc33214b" in games_now
    assert not any(x.startswith("tc509658") for x in games_now)                   # Just Chatting isn't a game
    assert all("key=KEY" in u for u in calls if "googleapis" in u)
    # ~5 searches at 100 units each: stays well under the daily quota at a 6h refresh
    assert sum("/search" in u for u in calls) <= 5


def test_trending_cache_view_and_clip_this(db, monkeypatch):
    from sxm import trending
    monkeypatch.setattr(settings, "youtube_api_key", "KEY")
    monkeypatch.setattr(settings, "twitch_client_id", "")
    db.insert("campaigns", {"name": "Fortnite Season Push", "url": "https://x", "rate_per_1k": 1.5,
                            "created_at": utcnow()})
    db.set_setting("permitted_streamers", "ninja")
    calls = []
    data = trending.refresh(db, trending_http(calls))
    assert trending.cached(db)["fetched_at"] == data["fetched_at"]
    n = len(calls)
    trending.refresh(db, trending_http(calls))           # fresh cache: no new API calls
    assert len(calls) == n
    v = trending.view(db, trending.cached(db))
    games = next(s for s in v["sections"] if s["key"] == "games")
    card = next(c for c in games["tabs"][0]["cards"] if c["id"] == "c1")
    assert card["campaign"] == "Fortnite Season Push" and card["permitted"]
    assert card["views_s"] == "800K" and card["vph_s"] == "80K"
    sid = trending.queue_clip(db, card["url"], card["title"], card["channel"])
    src = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    assert src["priority"] == 10 and src["streamer"] == "Ninja" and src["title"] == "Fortnite new season"
    assert trending.queue_clip(db, card["url"]) is None    # already queued
    assert trending.view(db, trending.cached(db))["sections"][2]["tabs"][0]["cards"][0]["queued"] in (True, False)
    with pytest.raises(ValueError):
        trending.queue_clip(db, "https://evil.example/video")


def test_trending_without_youtube_explains(db, monkeypatch):
    from sxm import trending
    monkeypatch.setattr(settings, "youtube_api_key", "")
    monkeypatch.setattr(settings, "twitch_client_id", "")
    data = trending.build(db, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert any("Connect a YouTube channel" in n for n in data["notes"])
    assert trending.short_num(1_250_000) == "1.2M" and trending.short_num(999) == "999"


def test_trending_page_renders(db, monkeypatch):
    from fastapi.testclient import TestClient
    from sxm import trending
    from sxm.web import app
    monkeypatch.setattr(settings, "youtube_api_key", "KEY")
    monkeypatch.setattr(settings, "twitch_client_id", "")
    trending.refresh(db, trending_http([]))
    c = TestClient(app)
    r = c.get("/trending")
    assert r.status_code == 200
    assert "Streamer videos blowing up now" in r.text and "Clip this" in r.text and "full stream rage" in r.text
    r = c.post("/trending/clip", data={"url": "https://www.youtube.com/watch?v=s2", "title": "t", "streamer": "Ishow"})
    assert r.json()["ok"]
    assert "✓ In queue" in c.get("/trending").text


# ---------------- auto-post + inventory ----------------

def test_autopost_every_n_hours_while_clips_last(db, tmp_path):
    from sxm import autopost
    connect_all(db)
    yt = db.one("SELECT id FROM channels WHERE platform='youtube'")["id"]
    vids = []
    for i in range(3):
        f = tmp_path / f"v{i}.mp4"
        f.write_bytes(b"x")
        cid = make_clip(db, score=i, video=str(f))
        db.update("clips", cid, status="approved")
        vids.append(cid)
    db.insert("posts", {"clip_id": vids[0], "platform": "youtube", "channel_id": yt,
                        "scheduled_at": "2099-01-01T00:00:00+00:00"})  # a normal-slot post: moves to the timer
    autopost.save_rule(db, yt, every_hours=4, source="library")
    assert db.one("SELECT status FROM posts WHERE clip_id=? AND auto=0", (vids[0],))["status"] == "cancelled"
    first = db.one("SELECT * FROM posts WHERE channel_id=? AND auto=1 AND status='scheduled'", (yt,))
    assert first["clip_id"] == vids[2]                    # best score first
    autopost.run(db)                                      # one lined up at a time
    assert db.one("SELECT COUNT(*) n FROM posts WHERE auto=1 AND status='scheduled'")["n"] == 1
    # it posts -> the next one is 4 hours after it
    posted_at = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
    db.update("posts", first["id"], status="posted", posted_at=posted_at.isoformat())
    autopost.run(db, now=posted_at + timedelta(minutes=5))
    nxt = db.one("SELECT * FROM posts WHERE auto=1 AND status='scheduled'")
    assert datetime.fromisoformat(nxt["scheduled_at"]) == posted_at + timedelta(hours=4)
    # this account is off the normal slots; others still get them
    assert yt not in {c["id"] for c in review.target_channels(db)}
    s = autopost.summary(db)[0]
    assert s["ready"] == 1 and s["next"] and "hours" in s["lasts"]
    autopost.set_enabled(db, s["id"], False)
    assert db.one("SELECT COUNT(*) n FROM posts WHERE auto=1 AND status='scheduled'")["n"] == 0
    assert yt in {c["id"] for c in review.target_channels(db)}


def test_autopost_from_trending_carousel_feeds_the_clipper(db, monkeypatch):
    from sxm import autopost, trending
    connect_all(db)
    yt = db.one("SELECT id FROM channels WHERE platform='youtube'")["id"]
    db.set_setting(trending.CACHE_KEY, json.dumps({"fetched_at": "2099-01-01T00:00:00Z", "rows": {"gta": {"now": [
        {"platform": "youtube", "id": "g1", "url": "https://www.youtube.com/watch?v=g1", "title": "GTA 6 madness",
         "channel": "Rp"}], "all": []}}}))
    autopost.save_rule(db, yt, source="trending:gta")
    rid = db.one("SELECT id FROM autopost")["id"]
    src = db.one("SELECT * FROM sources WHERE autopost_id=?", (rid,))
    assert src and src["url"].endswith("g1") and src["priority"] == 10
    autopost.run(db)                                      # already making one: doesn't pile on
    assert db.one("SELECT COUNT(*) n FROM sources WHERE autopost_id=?", (rid,))["n"] == 1


def test_premade_clip_and_inventory_board(db, tmp_path):
    from fastapi.testclient import TestClient
    from sxm import autopost, inventory
    from sxm.web import app
    f = tmp_path / "my_edit.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=108x192:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-shortest", str(f)], check=True)
    cid = autopost.add_premade(db, f)
    clip = db.one("SELECT * FROM clips WHERE id=?", (cid,))
    assert clip["status"] == "library" and Path(clip["video_path"]).exists() and clip["hook"] == "my edit"
    assert abs(clip["end_s"] - 2) < 0.3
    review.schedule_waiting(db)                           # ready-made clips wait for Auto-post
    assert not db.q("SELECT * FROM posts WHERE clip_id=?", (cid,))
    db.insert("sources", {"platform": "manual", "external_id": "q", "url": "https://x/v", "status": "downloading",
                          "progress": "Downloading 42% · 3m 10s left", "priority": 10, "found_at": utcnow()})
    b = inventory.board(db)
    q = b["cols"][0]["items"][0]
    assert q["pct"] == 42 and "3m 10s left" in q["line"]
    assert b["counts"]["processed"] == 1
    r = TestClient(app).get("/inventory")
    assert r.status_code == 200 and "SXM Inventory" in r.text and "About to auto-post" in r.text


def test_download_progress_text():
    seen = []
    clipper._report(lambda p, e: seen.append((p, e)), " 42.5%", "190")
    clipper._report(lambda p, e: seen.append((p, e)), "NA", "NA")
    assert seen == [(42.5, 190), (None, None)]
    assert clipper.eta_text(190) == "3m 10s left" and clipper.eta_text(4000) == "1h 06m left"
