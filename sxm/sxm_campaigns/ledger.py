"""Campaign money: funding, earnings, balances, payouts. All integer cents.

How money moves:
  brand pays $X  -> platform keeps PLATFORM_FEE_PERCENT, campaign.funded_cents += net
  approved post  -> earned_cents = min(views/1000 * rate, per-post cap), never more than
                    the campaign's unspent funding
  clipper        -> balance = sum(earned_cents on approved posts) - payouts (requested or paid)
"""
from __future__ import annotations

from .config import settings
from .db import DB, utcnow


class LedgerError(ValueError):
    pass


def fee_for(gross_cents: int) -> int:
    return int(round(gross_cents * settings.platform_fee_percent / 100))


def spent_cents(db: DB, campaign_id: int) -> int:
    r = db.one("SELECT COALESCE(SUM(earned_cents),0) s FROM submissions WHERE campaign_id=? AND status='approved'",
               (campaign_id,))
    return r["s"]


def remaining_cents(db: DB, campaign_id: int) -> int:
    c = db.one("SELECT funded_cents FROM campaigns WHERE id=?", (campaign_id,))
    return max((c["funded_cents"] if c else 0) - spent_cents(db, campaign_id), 0)


def record_funding(db: DB, campaign_id: int, gross_cents: int, method: str, reference: str | None = None,
                   paid: bool = True) -> int:
    """Create a funding row. Paid fundings credit the campaign immediately."""
    if gross_cents <= 0:
        raise LedgerError("Amount must be positive")
    fee = fee_for(gross_cents)
    fid = db.insert("fundings", {"campaign_id": campaign_id, "gross_cents": gross_cents, "fee_cents": fee,
                                 "net_cents": gross_cents - fee, "method": method, "reference": reference,
                                 "status": "paid" if paid else "pending", "created_at": utcnow()})
    if paid:
        _credit(db, campaign_id, gross_cents - fee)
    return fid


def mark_funding_paid(db: DB, reference: str) -> bool:
    """Idempotent: Stripe can deliver the same webhook more than once."""
    f = db.one("SELECT * FROM fundings WHERE reference=?", (reference,))
    if not f or f["status"] == "paid":
        return False
    db.update("fundings", f["id"], status="paid")
    _credit(db, f["campaign_id"], f["net_cents"])
    return True


def _credit(db: DB, campaign_id: int, net: int) -> None:
    db.run("UPDATE campaigns SET funded_cents = funded_cents + ?, "
           "status = CASE WHEN status='draft' THEN 'active' ELSE status END WHERE id=?", (net, campaign_id))


def target_earnings(campaign: dict, views: int) -> int:
    if views < (campaign["min_views"] or 0):
        return 0
    amt = views * campaign["rate_cents_per_1k"] // 1000
    if campaign["max_cents_per_post"]:
        amt = min(amt, campaign["max_cents_per_post"])
    return amt


def recalc_submission(db: DB, submission_id: int) -> int:
    """Bring a post's earnings up to date with its views. Returns cents added.
    Earnings only ever go up, and stop when the campaign runs out of money."""
    s = db.one("SELECT * FROM submissions WHERE id=?", (submission_id,))
    if not s or s["status"] != "approved":
        return 0
    c = db.one("SELECT * FROM campaigns WHERE id=?", (s["campaign_id"],))
    want = target_earnings(c, s["views"] or 0) - (s["earned_cents"] or 0)
    if want <= 0:
        return 0
    add = min(want, remaining_cents(db, c["id"]))
    if add > 0:
        db.update("submissions", submission_id, earned_cents=s["earned_cents"] + add)
    if remaining_cents(db, c["id"]) == 0 and c["status"] == "active":
        db.update("campaigns", c["id"], status="ended")
    return add


def balance_cents(db: DB, clipper_id: int) -> dict:
    earned = db.one("SELECT COALESCE(SUM(earned_cents),0) s FROM submissions WHERE clipper_id=? AND status='approved'",
                    (clipper_id,))["s"]
    out = db.one("SELECT COALESCE(SUM(amount_cents),0) s FROM payouts WHERE clipper_id=? AND status IN ('requested','paid')",
                 (clipper_id,))["s"]
    paid = db.one("SELECT COALESCE(SUM(amount_cents),0) s FROM payouts WHERE clipper_id=? AND status='paid'",
                  (clipper_id,))["s"]
    return {"earned": earned, "paid": paid, "pending_payout": out - paid, "available": earned - out}


def request_payout(db: DB, clipper_id: int, amount_cents: int | None = None, details: str = "") -> int:
    bal = balance_cents(db, clipper_id)["available"]
    amount = bal if amount_cents is None else amount_cents
    if amount < settings.min_payout_cents:
        raise LedgerError(f"Minimum payout is ${settings.min_payout_cents / 100:.2f}")
    if amount > bal:
        raise LedgerError("That's more than your available balance")
    u = db.one("SELECT stripe_account_id FROM users WHERE id=?", (clipper_id,))
    method = "stripe" if settings.stripe_on and u and u["stripe_account_id"] else "manual"
    return db.insert("payouts", {"clipper_id": clipper_id, "amount_cents": amount, "method": method,
                                 "details": details[:200], "created_at": utcnow()})


def platform_totals(db: DB) -> dict:
    f = db.one("SELECT COALESCE(SUM(gross_cents),0) g, COALESCE(SUM(fee_cents),0) fee, COALESCE(SUM(net_cents),0) n "
               "FROM fundings WHERE status='paid'")
    e = db.one("SELECT COALESCE(SUM(earned_cents),0) s, COALESCE(SUM(views),0) v FROM submissions WHERE status='approved'")
    p = db.one("SELECT COALESCE(SUM(amount_cents),0) s FROM payouts WHERE status='paid'")
    return {"gross": f["g"], "fees": f["fee"], "net_funded": f["n"], "earned": e["s"], "views": e["v"],
            "paid_out": p["s"], "owed": e["s"] - p["s"]}
