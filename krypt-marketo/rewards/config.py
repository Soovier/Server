"""Settings from environment variables (.env locally, host env vars in production)."""
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


@dataclass
class Settings:
    site_name: str = field(default_factory=lambda: _env("SITE_NAME", "Krypt Rewards"))
    base_url: str = field(default_factory=lambda: _env("BASE_URL", "http://localhost:8800").rstrip("/"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(ROOT / "data"))))
    secret_key: str = field(default_factory=lambda: _env("SECRET_KEY", "dev-only-change-me"))
    admin_email: str = field(default_factory=lambda: _env("ADMIN_EMAIL").lower())
    platform_fee_percent: float = field(default_factory=lambda: float(_env("PLATFORM_FEE_PERCENT", "10")))
    min_payout_cents: int = field(default_factory=lambda: int(float(_env("MIN_PAYOUT_USD", "10")) * 100))
    submit_window_hours: int = field(default_factory=lambda: int(_env("SUBMIT_WINDOW_HOURS", "24")))

    # View verification
    youtube_api_key: str = field(default_factory=lambda: _env("YOUTUBE_API_KEY"))
    meta_ig_user_id: str = field(default_factory=lambda: _env("META_IG_USER_ID"))
    meta_access_token: str = field(default_factory=lambda: _env("META_ACCESS_TOKEN"))
    meta_graph_version: str = field(default_factory=lambda: _env("META_GRAPH_VERSION", "v24.0"))
    views_refresh_minutes: int = field(default_factory=lambda: int(_env("VIEWS_REFRESH_MINUTES", "60")))

    # Stripe (optional; without it funding and payouts are recorded by hand)
    stripe_secret_key: str = field(default_factory=lambda: _env("STRIPE_SECRET_KEY"))
    stripe_webhook_secret: str = field(default_factory=lambda: _env("STRIPE_WEBHOOK_SECRET"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rewards.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def stripe_on(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


settings = Settings()
