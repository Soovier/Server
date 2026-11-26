"""Settings loaded from the environment (.env). Secrets never live in code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _list(name: str) -> list[str]:
    return [x.strip() for x in _env(name).split(",") if x.strip()]


@dataclass
class Settings:
    # App
    base_url: str = field(default_factory=lambda: _env("SXM_BASE_URL", "http://localhost:8787"))
    data_dir: Path = field(default_factory=lambda: Path(_env("SXM_DATA_DIR", str(ROOT / "data"))))
    timezone: str = field(default_factory=lambda: _env("SXM_TIMEZONE", "America/New_York"))
    dashboard_password: str = field(default_factory=lambda: _env("SXM_DASHBOARD_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("SXM_SESSION_SECRET", "change-me"))

    # Discovery
    twitch_client_id: str = field(default_factory=lambda: _env("TWITCH_CLIENT_ID"))
    twitch_client_secret: str = field(default_factory=lambda: _env("TWITCH_CLIENT_SECRET"))
    twitch_streamers: list[str] = field(default_factory=lambda: _list("TWITCH_STREAMERS"))
    youtube_source_channels: list[str] = field(default_factory=lambda: _list("YOUTUBE_SOURCE_CHANNEL_IDS"))
    kick_streamers: list[str] = field(default_factory=lambda: _list("KICK_STREAMERS"))

    # Clipping
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "small"))
    clips_per_source: int = field(default_factory=lambda: int(_env("CLIPS_PER_SOURCE", "3")))
    clip_min_seconds: int = field(default_factory=lambda: int(_env("CLIP_MIN_SECONDS", "20")))
    clip_max_seconds: int = field(default_factory=lambda: int(_env("CLIP_MAX_SECONDS", "59")))
    brand_handle: str = field(default_factory=lambda: _env("BRAND_HANDLE"))
    reframe_mode: str = field(default_factory=lambda: _env("REFRAME_MODE", "blur"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))

    # Meta (Instagram + Facebook Page)
    meta_app_id: str = field(default_factory=lambda: _env("META_APP_ID"))
    meta_app_secret: str = field(default_factory=lambda: _env("META_APP_SECRET"))
    meta_graph_version: str = field(default_factory=lambda: _env("META_GRAPH_VERSION", "v24.0"))

    # Google (YouTube Data + Analytics)
    google_client_id: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _env("GOOGLE_CLIENT_SECRET"))

    # SXM Campaigns (your own campaign platform)
    campaigns_site_url: str = field(default_factory=lambda: _env("CAMPAIGNS_SITE_URL").rstrip("/"))
    campaigns_site_token: str = field(default_factory=lambda: _env("CAMPAIGNS_SITE_TOKEN"))

    # Schedule (cron-style, in SXM_TIMEZONE)
    discover_cron: str = field(default_factory=lambda: _env("DISCOVER_CRON", "0 */3 * * *"))
    analytics_cron: str = field(default_factory=lambda: _env("ANALYTICS_CRON", "30 */6 * * *"))
    post_slots: list[str] = field(default_factory=lambda: _list("POST_SLOTS") or ["11:00", "15:00", "19:00", "21:30"])

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sxm.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.media_dir, self.media_dir / "sources", self.media_dir / "clips"):
            d.mkdir(parents=True, exist_ok=True)

    def missing(self) -> dict[str, list[str]]:
        """Which integrations still need keys. Shown on the Setup page."""
        out: dict[str, list[str]] = {}
        checks = {
            "Twitch discovery": ["twitch_client_id", "twitch_client_secret"],
            "Instagram + Facebook": ["meta_app_id", "meta_app_secret"],
            "YouTube": ["google_client_id", "google_client_secret"],
        }
        for name, keys in checks.items():
            miss = [k.upper() for k in keys if not getattr(self, k)]
            if miss:
                out[name] = miss
        return out


settings = Settings()
