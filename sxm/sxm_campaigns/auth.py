"""Accounts, password hashing, sessions and API tokens. Standard library only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from .config import settings
from .db import DB, utcnow

ROLES = ("brand", "clipper")


class AuthError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except Exception:
        return False


def signup(db: DB, email: str, name: str, password: str, role: str) -> int:
    email = email.strip().lower()
    if role not in ROLES:
        raise AuthError("Pick brand or clipper")
    if "@" not in email or len(password) < 8:
        raise AuthError("Use a real email and a password of 8+ characters")
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise AuthError("That email already has an account")
    if settings.admin_email and email == settings.admin_email:
        role = "admin"
    return db.insert("users", {"email": email, "name": name.strip()[:80] or email.split("@")[0],
                               "password_hash": hash_password(password), "role": role, "created_at": utcnow()})


def login(db: DB, email: str, password: str) -> dict:
    u = db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    if not u or not check_password(password, u["password_hash"]):
        raise AuthError("Wrong email or password")
    if u["banned"]:
        raise AuthError("This account is suspended")
    return u


# ---------------- session cookie ----------------

def _sign(msg: str) -> str:
    return hmac.new(settings.secret_key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(user_id: int, days: int = 30) -> str:
    exp = int(time.time()) + days * 86400
    msg = f"{user_id}.{exp}"
    return f"{msg}.{_sign(msg)}"


def read_session(value: str | None) -> int | None:
    if not value:
        return None
    try:
        uid, exp, sig = value.split(".")
        if not hmac.compare_digest(sig, _sign(f"{uid}.{exp}")) or int(exp) < time.time():
            return None
        return int(uid)
    except ValueError:
        return None


def csrf_token(user_id: int) -> str:
    return _sign(f"csrf.{user_id}")[:32]


# ---------------- API tokens ----------------

def new_api_token(db: DB, user_id: int) -> str:
    token = "sxm_" + secrets.token_urlsafe(32)
    db.update("users", user_id, api_token_hash=hashlib.sha256(token.encode()).hexdigest())
    return token


def user_for_token(db: DB, token: str) -> dict | None:
    if not token:
        return None
    u = db.one("SELECT * FROM users WHERE api_token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    return u if u and not u["banned"] else None
