"""Clip editor: re-cut, reframe and re-caption any clip, by hand or by telling the AI what you want."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from pathlib import Path

from .analysis import Box
from .config import settings
from .db import DB, utcnow
from .editor import ASPECTS, Word, ffmpeg_bin, probe_duration, render_clip, thumbnail

log = logging.getLogger("sxm.clip_edit")
LAYOUTS = ("stack", "blur", "crop")
_busy: set[int] = set()
_lock = threading.Lock()


def _box(v) -> Box | None:
    try:
        x, y, w, h = (max(0.0, min(1.0, float(a))) for a in v)
        if w < 0.03 or h < 0.03:
            return None
        return Box(x, y, min(w, 1 - x), min(h, 1 - y))
    except (TypeError, ValueError):
        return None


def media_of(db: DB, clip: dict) -> tuple[Path | None, float]:
    """The video this clip can be re-cut from, and where it starts in the original stream."""
    if clip.get("media_path") and Path(clip["media_path"]).exists():
        return Path(clip["media_path"]), float(clip.get("media_offset") or 0)
    src = db.one("SELECT local_path FROM sources WHERE id=?", (clip["source_id"],))
    if src and src.get("local_path") and Path(src["local_path"]).exists():
        return Path(src["local_path"]), 0.0
    return None, 0.0


LONG_MEDIA = 400  # seconds: past this, the editor works on a window around the clip


def view_of(start: float, end: float, dur: float) -> tuple[float, float]:
    """The part of the source the timeline shows."""
    if dur <= LONG_MEDIA:
        return 0.0, dur
    return max(0.0, start - 90), min(dur, end + 90)


def words_of(db: DB, clip: dict, media: Path | None, around: tuple[float, float] | None = None) -> list[dict]:
    """Timed words in media time (cached after the first transcription). For a long source only
    the part around the clip is transcribed, so opening the editor stays quick."""
    if clip.get("words_json"):
        try:
            return json.loads(clip["words_json"])
        except ValueError:
            pass
    if not media:
        return []
    from .clipper import transcribe
    a = 0.0
    tmp = None
    try:
        if around:
            a, b = around
            tmp = settings.media_dir / "clips" / f"words_{clip['id']}.wav"
            subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i",
                            str(media), "-vn", "-ac", "1", "-ar", "16000", str(tmp)], capture_output=True, timeout=300)
        ws = [{"s": round(w.start + a, 2), "e": round(w.end + a, 2), "t": w.text}
              for w in transcribe(tmp if tmp and tmp.exists() else media)]
    except Exception as e:
        log.info("transcribe %s: %s", media, e)
        ws = []
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)
    if ws:
        db.update("clips", clip["id"], words_json=json.dumps(ws))
    return ws


def state(db: DB, clip_id: int) -> dict | None:
    """Everything the editor page needs."""
    c = db.one("SELECT c.*, s.title src_title, s.streamer, s.url src_url FROM clips c JOIN sources s ON s.id=c.source_id"
               " WHERE c.id=?", (clip_id,))
    if not c:
        return None
    media, off = media_of(db, c)
    try:
        ed = json.loads(c.get("edit_json") or "{}")
    except ValueError:
        ed = {}
    start = float(ed.get("start", (c["start_s"] or 0) - off))
    end = float(ed.get("end", (c["end_s"] or 0) - off))
    dur = probe_duration(media) if media else 0.0
    va, vb = view_of(start, end, dur)
    words = words_of(db, c, media, (va, vb) if dur > LONG_MEDIA else None) if media else []
    clip_words = [w for w in words if start <= w["s"] < end]
    return {
        "id": c["id"], "status": c["status"], "hook": c.get("hook") or "", "caption": c.get("caption") or "",
        "hashtags": c.get("hashtags") or "", "score": c.get("score") or 0, "favorite": bool(c.get("favorite")),
        "streamer": c.get("streamer") or "", "src_title": c.get("src_title") or "", "src_url": c.get("src_url") or "",
        "has_media": bool(media), "media_duration": round(dur, 2), "media_offset": off,
        "start": round(start, 2), "end": round(end, 2), "view": [round(va, 2), round(vb, 2)],
        "layout": ed.get("layout") or c.get("layout") or "blur", "aspect": ed.get("aspect", "9:16"),
        "facecam": ed.get("facecam"), "game": ed.get("game"), "captions": ed.get("captions", True),
        "fps": ed.get("fps", 30), "height": ed.get("height", 1080),
        "words": words, "transcript": " ".join(w["t"].strip() for w in clip_words) or (c.get("transcript") or ""),
        "busy": clip_id in _busy, "error": c.get("review_note") if c["status"] == "failed" else "",
        "updated": c.get("downloaded_at") or c.get("created_at"),
        "history": ed.get("history", [])[-12:],
    }


def clean(st: dict, new: dict) -> dict:
    """Merge requested changes into the current edit, keeping them sane."""
    out = dict(st)
    dur = st.get("media_duration") or 1e9
    for k in ("start", "end"):
        if k in new and new[k] is not None:
            try:
                out[k] = max(0.0, min(dur, float(new[k])))
            except (TypeError, ValueError):
                pass
    if out["end"] - out["start"] < 1:
        out["end"] = min(dur, out["start"] + 1)
    if out["end"] - out["start"] > 180:
        out["end"] = out["start"] + 180
    if new.get("layout") in LAYOUTS:
        out["layout"] = new["layout"]
    if new.get("aspect") in ASPECTS:
        out["aspect"] = new["aspect"]
    for k in ("facecam", "game"):
        if k in new:
            b = _box(new[k]) if new[k] else None
            out[k] = [round(b.x, 4), round(b.y, 4), round(b.w, 4), round(b.h, 4)] if b else None
    for k in ("hook", "caption", "hashtags"):
        if isinstance(new.get(k), str):
            out[k] = new[k].strip()[:300 if k != "hook" else 80]
    if "captions" in new:
        out["captions"] = bool(new["captions"]) and new["captions"] not in ("0", "false", "off")
    if str(new.get("fps", "")) in ("30", "60"):
        out["fps"] = int(new["fps"])
    if str(new.get("height", "")) in ("720", "1080"):
        out["height"] = int(new["height"])
    return out


def save(db: DB, clip_id: int, new: dict, note: str = "", background: bool = True) -> dict:
    """Apply changes and re-render the clip."""
    st = state(db, clip_id)
    if not st:
        raise ValueError("Clip not found")
    if not st["has_media"]:
        raise ValueError("The original video for this clip isn't on this computer any more, so it can't be re-cut.")
    st = clean(st, new)
    hist = st.get("history", [])
    if note:
        hist = (hist + [{"at": utcnow(), "note": note[:300]}])[-20:]
    edit = {k: st[k] for k in ("start", "end", "layout", "aspect", "facecam", "game", "captions", "fps", "height")}
    edit["history"] = hist
    db.update("clips", clip_id, hook=st["hook"], caption=st["caption"], hashtags=st["hashtags"],
              edit_json=json.dumps(edit), start_s=st["media_offset"] + st["start"],
              end_s=st["media_offset"] + st["end"], layout=st["layout"],
              transcript=" ".join(w["t"].strip() for w in st["words"] if st["start"] <= w["s"] < st["end"]))
    if background:
        threading.Thread(target=_render, args=(clip_id,), daemon=True).start()
    else:
        _render(clip_id)
    return st


def _render(clip_id: int) -> None:
    from .db import get_db
    db = get_db()
    with _lock:
        if clip_id in _busy:
            return
        _busy.add(clip_id)
    try:
        st = state(db, clip_id)
        c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
        media, _ = media_of(db, c)
        words = [Word(w["s"] - st["start"], w["e"] - st["start"], w["t"]) for w in st["words"]
                 if st["start"] <= w["s"] < st["end"]]
        cam, game = _box(st["facecam"]) if st["facecam"] else None, _box(st["game"]) if st["game"] else None
        mode = st["layout"]
        if mode == "stack" and not cam:
            from . import analysis
            cam = analysis.detect_facecam(media, st["start"], st["end"])
            mode = "stack" if cam else "blur"
        out = settings.media_dir / "clips" / f"clip_{clip_id}.mp4"
        tmp = out.with_name(f"clip_{clip_id}.new.mp4")
        render_clip(media, tmp, st["start"], st["end"], words, st["hook"], mode=mode, brand=settings.brand_handle,
                    facecam=cam, game=game, aspect=st["aspect"], fps=st["fps"], height=st["height"],
                    captions=st["captions"])
        tmp.replace(out)
        tmp.with_suffix(".ass").unlink(missing_ok=True)
        thumb = thumbnail(out, out.with_suffix(".jpg"))
        db.update("clips", clip_id, video_path=str(out), thumb_path=str(thumb), downloaded_at=utcnow(),
                  status="pending_review" if c["status"] in ("failed", "rendering") else c["status"],
                  review_note=None)
    except Exception as e:
        log.warning("re-render %s: %s", clip_id, e)
        db.update("clips", clip_id, review_note=f"Re-render failed: {str(e)[:400]}")
    finally:
        _busy.discard(clip_id)


# ---------------- Edit with AI ----------------

def _rule_edit(st: dict, ask: str) -> tuple[dict, str]:
    """Simple understanding of common asks when there's no Claude key."""
    a = ask.lower()
    ch: dict = {}
    said = []
    m = re.search(r"(\d+)\s*(s|sec|second)", a)
    n = float(m.group(1)) if m else 5.0
    if "shorter" in a or "trim" in a or "cut down" in a:
        ch["end"] = max(st["start"] + 5, st["end"] - n)
        said.append(f"cut {n:g}s off the end")
    if "longer" in a or "extend" in a or "more context" in a:
        ch["start"] = max(0, st["start"] - n / 2)
        ch["end"] = st["end"] + n / 2
        said.append(f"added {n:g}s around it")
    if "start later" in a or "skip the intro" in a or "get to the point" in a:
        ch["start"] = min(st["end"] - 5, st["start"] + n)
        said.append(f"starts {n:g}s later")
    if "no caption" in a or "remove caption" in a or "captions off" in a:
        ch["captions"] = False
        said.append("captions off")
    elif "caption" in a and ("add" in a or "on" in a):
        ch["captions"] = True
        said.append("captions on")
    for word, lay in (("facecam", "stack"), ("full frame", "blur"), ("blur", "blur"), ("crop", "crop"),
                      ("zoom", "crop")):
        if word in a:
            ch["layout"] = lay
            said.append(f"layout: {lay}")
            break
    for asp in ASPECTS:
        if asp in a:
            ch["aspect"] = asp
            said.append(asp)
    m = re.search(r"(?:hook|title)\s*(?:to|:)?\s*[\"“](.+?)[\"”]", ask, re.I)
    if m:
        ch["hook"] = m.group(1)
        said.append("new hook")
    return ch, ("Done: " + ", ".join(said)) if said else \
        "I couldn't work that out without an AI key. Try: shorter by 5 seconds, longer, no captions, full frame, facecam."


def ai_edit(db: DB, clip_id: int, ask: str, http=None) -> dict:
    """Tell it what you want ("start right before the jump scare, punchier hook") and it re-cuts the clip."""
    st = state(db, clip_id)
    if not st:
        raise ValueError("Clip not found")
    ask = (ask or "").strip()[:600]
    if not ask:
        raise ValueError("Type what you want changed.")
    near = [w for w in st["words"] if st["start"] - 60 <= w["s"] < st["end"] + 60]
    if settings.anthropic_api_key:
        lines, cur, t0 = [], [], None
        for w in near:
            t0 = w["s"] if t0 is None else t0
            cur.append(w["t"].strip())
            if w["e"] - t0 >= 4:
                lines.append(f"[{t0:.1f}] {' '.join(cur)}")
                cur, t0 = [], None
        if cur:
            lines.append(f"[{t0:.1f}] {' '.join(cur)}")
        prompt = (
            "You edit short vertical clips cut from a livestream. Apply the user's request to the clip settings.\n"
            f"Video length available: 0-{st['media_duration']:.1f}s. Current clip: start={st['start']:.1f}, "
            f"end={st['end']:.1f} ({st['end'] - st['start']:.1f}s).\n"
            f"layout={st['layout']} (stack = facecam on top + gameplay below, blur = full frame on blurred background, "
            f"crop = zoomed crop), aspect={st['aspect']} (9:16, 16:9, 4:5, 1:1), captions={st['captions']}.\n"
            f"hook (on-screen title, max 7 words)={st['hook']!r}\ncaption (post text)={st['caption']!r}\n"
            f"hashtags={st['hashtags']!r}\nStreamer: {st['streamer']!r}. Stream: {st['src_title']!r}.\n\n"
            "Timed transcript around the clip (seconds):\n" + "\n".join(lines)[:14000] + "\n\n"
            f"User request: {ask!r}\n\n"
            "Reply with ONLY a JSON object. Include only the keys you change, from: start, end (seconds, numbers), "
            "layout, aspect, captions (bool), hook, caption, hashtags. Always include reply: one short sentence "
            "telling the user what you changed. Keep clips between 8 and 90 seconds unless asked otherwise; start on "
            "a clean sentence and end right after the payoff."
        )
        try:
            from .campaign_finder import ask_claude
            txt = ask_claude(prompt, max_tokens=600, http=http)
            ch = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
            reply = str(ch.pop("reply", "Done."))[:300]
        except Exception as e:
            log.info("ai edit: %s", e)
            ch, reply = _rule_edit(st, ask)
    else:
        ch, reply = _rule_edit(st, ask)
    if ch:
        save(db, clip_id, ch, note=f"AI: {ask} → {reply}")
    return {"reply": reply, "changes": ch}


def filmstrip(db: DB, clip_id: int, frames: int = 24) -> Path | None:
    """A strip of frames across the part of the source the timeline shows."""
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    if not c:
        return None
    media, off = media_of(db, c)
    if not media:
        return None
    dur = probe_duration(media) or 1
    try:
        ed = json.loads(c.get("edit_json") or "{}")
    except ValueError:
        ed = {}
    va, vb = view_of(float(ed.get("start", (c["start_s"] or 0) - off)), float(ed.get("end", (c["end_s"] or 0) - off)), dur)
    out = settings.media_dir / "clips" / f"strip_{clip_id}_{int(va)}_{int(vb)}.jpg"
    if out.exists() and out.stat().st_mtime >= media.stat().st_mtime:
        return out
    for old in out.parent.glob(f"strip_{clip_id}_*.jpg"):
        old.unlink(missing_ok=True)
    fps = frames / max(1.0, vb - va)
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{va:.2f}", "-to", f"{vb:.2f}", "-i", str(media),
                    "-vf", f"fps={fps:.5f},scale=160:-2,tile={frames}x1", "-frames:v", "1", "-q:v", "6", str(out)],
                   capture_output=True, timeout=300)
    return out if out.exists() else None
