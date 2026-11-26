import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from krypt import analytics, campaigns, clipper, review
from krypt.config import settings
from krypt.db import DB, set_db, utcnow
from krypt.editor import Word, build_ass, probe_duration, render_clip
from krypt.publishers import meta, publish_due, youtube


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    settings.ensure_dirs()
    d = DB(":memory:")
    set_db(d)
    return d


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
    camp = campaigns.add_campaign(db, name="C", url="https://contentrewards.com/c/x")
    ok = make_clip(db, campaign_id=camp)
    no_perm = make_clip(db, streamer="random")
    low = make_clip(db, campaign_id=camp, score=1, streamer="z")
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
    cid = make_clip(db)
    review.approve(db, cid)
    review.reject(db, cid)
    assert {p["status"] for p in db.q("SELECT status FROM posts")} == {"cancelled"}


# ---------------- publishing ----------------

def test_publish_due_posts_and_queues_submission(db, monkeypatch, tmp_path):
    camp = campaigns.add_campaign(db, name="C", url="https://contentrewards.com/c/x", platforms="instagram")
    cid = make_clip(db, campaign_id=camp)
    review.approve(db, cid)
    db.run("UPDATE posts SET scheduled_at=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    from krypt import publishers
    monkeypatch.setitem(publishers.PUBLISHERS, "instagram", lambda d, c: {"external_id": "ig1", "permalink": "https://ig/p/1"})
    monkeypatch.setitem(publishers.PUBLISHERS, "facebook", lambda d, c: {"external_id": "fb1", "permalink": "https://fb/1"})

    def boom(d, c):
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
    assert youtube.ensure_fresh_token(db, http) == "new"
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
    from krypt.web import app
    camp = campaigns.add_campaign(db, name="Camp", url="https://contentrewards.com/c/x")
    make_clip(db, campaign_id=camp)
    c = TestClient(app)
    for path in ["/", "/review", "/schedule", "/campaigns", "/sources", "/setup", "/api/summary", "/?days=0&channel=youtube"]:
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:500])
    assert "Krypt" in c.get("/").text
    r = c.post("/settings/auto-approve", data={"enabled": "1", "min_score": "3"}, follow_redirects=False)
    assert r.status_code == 303 and db.get_setting("auto_approve") == "1"
    clip = db.one("SELECT id FROM clips")["id"]
    c.post(f"/review/{clip}", data={"action": "approve", "hook": "NEW HOOK"})
    assert db.one("SELECT hook,status FROM clips WHERE id=?", (clip,)) == {"hook": "NEW HOOK", "status": "scheduled"}
    r = c.post("/campaigns", data={"name": "X", "url": "https://contentrewards.com/c/y", "platforms": ["youtube"],
                                   "source_urls": "https://twitch.tv/videos/1"})
    assert r.status_code == 200
    assert db.one("SELECT platforms FROM campaigns WHERE name='X'")["platforms"] == "youtube"
    assert c.get("/media/clips/../krypt.db").status_code == 404
