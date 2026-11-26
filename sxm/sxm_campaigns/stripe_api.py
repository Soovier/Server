"""Minimal Stripe client over HTTPS (no SDK): Checkout for brand funding,
Connect Express for clipper payouts, and webhook signature checks."""
from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from .config import settings

API = "https://api.stripe.com/v1"


class StripeError(RuntimeError):
    pass


def _post(path: str, data: dict, http: httpx.Client | None = None, idem: str | None = None) -> dict:
    http = http or httpx.Client(timeout=30)
    headers = {"Idempotency-Key": idem} if idem else {}
    r = http.post(f"{API}/{path}", data=data, auth=(settings.stripe_secret_key, ""), headers=headers)
    body = r.json()
    if r.status_code >= 400:
        raise StripeError((body.get("error") or {}).get("message", r.text[:300]))
    return body


def checkout_for_campaign(campaign: dict, gross_cents: int, http=None) -> dict:
    return _post("checkout/sessions", {
        "mode": "payment",
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": gross_cents,
        "line_items[0][price_data][product_data][name]": f"Campaign budget: {campaign['title'][:80]}",
        "metadata[campaign_id]": campaign["id"],
        "success_url": f"{settings.base_url}/campaigns/{campaign['id']}?funded=1",
        "cancel_url": f"{settings.base_url}/campaigns/{campaign['id']}",
    }, http)


def create_connect_account(email: str, http=None) -> str:
    return _post("accounts", {"type": "express", "email": email, "capabilities[transfers][requested]": "true"},
                 http)["id"]


def onboarding_link(account_id: str, http=None) -> str:
    return _post("account_links", {"account": account_id, "type": "account_onboarding",
                                   "refresh_url": f"{settings.base_url}/payouts/stripe/start",
                                   "return_url": f"{settings.base_url}/payouts"}, http)["url"]


def transfer(account_id: str, amount_cents: int, payout_id: int, http=None) -> str:
    return _post("transfers", {"amount": amount_cents, "currency": "usd", "destination": account_id,
                               "metadata[payout_id]": payout_id}, http, idem=f"payout-{payout_id}")["id"]


def verify_webhook(payload: bytes, sig_header: str, secret: str | None = None, tolerance: int = 300) -> bool:
    secret = secret or settings.stripe_webhook_secret
    if not secret or not sig_header:
        return False
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    sigs = [v for k, v in (p.split("=", 1) for p in sig_header.split(",") if "=" in p) if k == "v1"]
    try:
        ts = int(parts.get("t", "0"))
    except ValueError:
        return False
    if abs(time.time() - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)
