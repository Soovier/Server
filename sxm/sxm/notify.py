"""Phone/desktop alerts. Set NOTIFY_WEBHOOK_URL to a Discord webhook or an
ntfy.sh topic URL (free, install the ntfy app on your phone)."""
from __future__ import annotations

import os

import httpx


def notify(message: str, link: str = "") -> None:
    url = os.environ.get("NOTIFY_WEBHOOK_URL", "").strip()
    text = f"[SXM] {message}" + (f"\n{link}" if link else "")
    print(text)
    if not url:
        return
    try:
        if "discord.com/api/webhooks" in url:
            httpx.post(url, json={"content": text[:1900]}, timeout=10)
        else:  # ntfy.sh or any endpoint that takes a plain-text body
            httpx.post(url, content=text.encode(), headers={"Title": "SXM"}, timeout=10)
    except Exception:
        pass
