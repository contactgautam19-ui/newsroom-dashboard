"""Guest access: a visitor verifies their email with a one-time code and gets a
read-only session. The editor is emailed the moment someone enters, and every
visitor is recorded in guest_visitors (with the source they came from, e.g.
the portfolio link).

Sessions are stateless signed tokens in the nk_guest cookie, so they work on
Vercel without server-side session storage. Codes live in guest_otps.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from html import escape as html_escape

from app import config, db

log = logging.getLogger("newsroom.guest")

COOKIE = "nk_guest"
OTP_TTL_MIN = 10
OTP_MAX_ATTEMPTS = 5
PER_EMAIL_PER_HOUR = 3        # codes one address may request per hour
GLOBAL_PER_HOUR = 40          # codes the endpoint will send per hour in total
SESSION_MINUTES = 15          # a demo sitting; the cookie and token both expire
SESSIONS_PER_DAY = 2          # sittings one address may start in 24 hours

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class GuestError(ValueError):
    """User-facing problem (bad input, rate limit, wrong code)."""


# --------------------------------------------------------------------------
# signed session token
# --------------------------------------------------------------------------
def _secret() -> bytes:
    return (config.CRON_SECRET or config.PASSCODE or "dev-guest-secret").encode()


def make_token(email: str, name: str) -> str:
    exp = int(time.time()) + SESSION_MINUTES * 60
    name = name.replace("|", "/")
    payload = f"{exp}|{email.lower()}|{name}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    raw = f"{payload}|{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_token(token: str | None) -> dict | None:
    """Return {'email','name','exp'} for a valid, unexpired token; else None."""
    if not token:
        return None
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        payload, sig = raw.rsplit("|", 1)
        exp_s, email, name = payload.split("|", 2)
        want = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(want, sig):
            return None
        if int(exp_s) < time.time():
            return None
        return {"email": email, "name": name, "exp": int(exp_s)}
    except Exception:
        return None


# --------------------------------------------------------------------------
# demo quotas — a guest can exercise the paid features, but only a little
# --------------------------------------------------------------------------
# kind -> (per-guest allowance, what the guest is told when it runs out)
QUOTAS = {
    "x": (3, "Demo limit reached — each guest gets 3 live X pulls. "
             "The feed keeps showing the latest stored posts."),
    "ai": (25, "Demo limit reached — each guest gets 25 N-Pro requests. "
               "Ask Gautam for full access."),
}
GUEST_X_PULLS_PER_DAY = 12    # all guests combined, protects the monthly X budget


def _use_key(kind: str, email: str) -> str:
    return f"guest_use:{kind}:{email.lower()}"


def _count(key: str) -> int:
    from app import settings_store
    try:
        return int(settings_store.get_setting(key, "0") or 0)
    except (TypeError, ValueError):
        return 0


def remaining(email: str) -> dict:
    """What this guest has left, per quota kind."""
    return {kind: max(0, limit - _count(_use_key(kind, email)))
            for kind, (limit, _) in QUOTAS.items()}


def consume(email: str, kind: str) -> str | None:
    """Spend one unit of a guest quota. Returns None when allowed, otherwise the
    message to show the guest. Counts live in the settings table, so they hold
    across serverless invocations and across the guest's 48-hour session."""
    from app import settings_store
    limit, message = QUOTAS[kind]
    key = _use_key(kind, email)
    used = _count(key)
    if used >= limit:
        return message
    if kind == "x":
        day_key = f"guest_x_day:{_now().date().isoformat()}"
        today = _count(day_key)
        if today >= GUEST_X_PULLS_PER_DAY:
            return ("Live X pulls for demo guests are used up for today. "
                    "The feed keeps showing the latest stored posts.")
        settings_store.set_setting(day_key, str(today + 1))
    settings_store.set_setting(key, str(used + 1))
    return None


# --------------------------------------------------------------------------
# one-time codes
# --------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(email: str, code: str) -> str:
    return hashlib.sha256(f"{email.lower()}:{code}".encode()).hexdigest()


def _clean(name: str, email: str, org: str, source: str) -> tuple[str, str, str, str]:
    name = re.sub(r"\s+", " ", (name or "")).strip()[:80]
    email = (email or "").strip().lower()[:160]
    org = re.sub(r"\s+", " ", (org or "")).strip()[:120]
    source = re.sub(r"[^a-z0-9_-]", "", (source or "").lower())[:40] or "direct"
    if len(name) < 2:
        raise GuestError("Please enter your name.")
    if not _EMAIL_RE.match(email):
        raise GuestError("That email address doesn't look right.")
    return name, email, org, source


def request_code(name: str, email: str, org: str, source: str, user_agent: str) -> dict:
    """Store a code for this email and send it. Returns {'ok': True}; in local
    dev (email disabled, not serverless) also returns the code so the flow can
    be exercised without an inbox."""
    name, email, org, source = _clean(name, email, org, source)
    now = _now()
    hour_ago = (now - timedelta(hours=1)).isoformat()

    with db.connect() as con:
        mine = con.execute(
            "SELECT COUNT(*) c FROM guest_otps WHERE email = ? AND created_at > ?",
            (email, hour_ago)).fetchone()["c"]
        if mine >= PER_EMAIL_PER_HOUR:
            raise GuestError("Too many codes requested for this address. Try again in an hour.")
        # a 15-minute sitting means little if it can be restarted at will
        day_ago = (now - timedelta(hours=24)).isoformat()
        sittings = con.execute(
            "SELECT COUNT(*) c FROM guest_otps WHERE email = ? AND used = 1 AND created_at > ?",
            (email, day_ago)).fetchone()["c"]
        if sittings >= SESSIONS_PER_DAY:
            raise GuestError("You've used today's demo sessions for this address. "
                             "Contact Gautam for extended access.")
        total = con.execute(
            "SELECT COUNT(*) c FROM guest_otps WHERE created_at > ?", (hour_ago,)).fetchone()["c"]
        if total >= GLOBAL_PER_HOUR:
            raise GuestError("Guest access is busy right now. Please try again later.")

        code = f"{secrets.randbelow(10 ** 6):06d}"
        con.execute(
            "INSERT INTO guest_otps (email, code_hash, created_at, expires_at) VALUES (?,?,?,?)",
            (email, _code_hash(email, code), now.isoformat(),
             (now + timedelta(minutes=OTP_TTL_MIN)).isoformat()))
        con.execute(
            "INSERT OR IGNORE INTO guest_visitors (email, name, org, source, first_seen, last_seen, user_agent) "
            "VALUES (?,?,?,?,?,?,?)",
            (email, name, org, source, now.isoformat(), now.isoformat(), (user_agent or "")[:300]))
        con.execute(
            "UPDATE guest_visitors SET name = ?, org = ?, source = ?, last_seen = ?, user_agent = ? WHERE email = ?",
            (name, org, source, now.isoformat(), (user_agent or "")[:300], email))

    from app import briefing
    html = _code_email(name, code)
    err = briefing.send_mail([email], "Your Echo demo access code", html)
    if err is None:
        return {"ok": True}
    if not config.EMAIL_ENABLED and not config.IS_SERVERLESS:
        log.warning("guest code for %s (email disabled, local dev): %s", email, code)
        return {"ok": True, "dev_code": code}
    log.error("guest code email to %s failed: %s", email, err)
    raise GuestError("Couldn't send the code just now. Please try again in a minute.")


def verify_code(email: str, code: str, user_agent: str) -> dict:
    """Check the code; on success mark the visitor verified and notify the editor.
    Returns the visitor row as a dict."""
    email = (email or "").strip().lower()
    code = re.sub(r"\D", "", code or "")
    if not _EMAIL_RE.match(email) or len(code) != 6:
        raise GuestError("Enter the 6-digit code from your email.")
    now = _now()
    with db.connect() as con:
        row = con.execute(
            "SELECT id, code_hash, expires_at, attempts, used FROM guest_otps "
            "WHERE email = ? ORDER BY id DESC LIMIT 1", (email,)).fetchone()
        if row is None or row["used"]:
            raise GuestError("No active code for this address. Request a new one.")
        if row["expires_at"] < now.isoformat():
            raise GuestError("That code has expired. Request a new one.")
        if row["attempts"] >= OTP_MAX_ATTEMPTS:
            raise GuestError("Too many attempts. Request a new code.")
        if not hmac.compare_digest(row["code_hash"], _code_hash(email, code)):
            con.execute("UPDATE guest_otps SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
            raise GuestError("Wrong code. Check the email and try again.")
        con.execute("UPDATE guest_otps SET used = 1 WHERE id = ?", (row["id"],))
        con.execute(
            "UPDATE guest_visitors SET verified = 1, visits = visits + 1, last_seen = ?, user_agent = ? "
            "WHERE email = ?", (now.isoformat(), (user_agent or "")[:300], email))
        visitor = con.execute(
            "SELECT email, name, org, source, first_seen, last_seen, visits FROM guest_visitors WHERE email = ?",
            (email,)).fetchone()
    visitor = dict(visitor) if visitor else {"email": email, "name": "", "org": "", "source": "", "visits": 1}
    _notify_editor(visitor)
    return visitor


def list_visitors(limit: int = 100) -> list[dict]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT email, name, org, source, first_seen, last_seen, visits, verified "
            "FROM guest_visitors ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# emails
# --------------------------------------------------------------------------
def _code_email(name: str, code: str) -> str:
    name = html_escape(name)
    return f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:480px;margin:0 auto;padding:28px 24px;color:#111">
  <p style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#888;margin:0 0 14px">Echo · guest access</p>
  <p style="font-size:16px;margin:0 0 18px">Hi {name}, here is your one-time code:</p>
  <p style="font-size:34px;font-weight:700;letter-spacing:.18em;margin:0 0 18px;font-family:Consolas,Menlo,monospace">{code}</p>
  <p style="font-size:14px;color:#555;margin:0 0 8px">It expires in {OTP_TTL_MIN} minutes. Your demo session lasts {SESSION_MINUTES} minutes from the moment you enter.</p>
  <p style="font-size:13px;color:#888;margin:18px 0 0">If you didn't request this, ignore the email — nothing happens without the code.</p>
</div>"""


def _notify_editor(v: dict) -> None:
    from app import briefing, settings_store
    try:
        to = settings_store.get_recipients() or ([config.GMAIL_ADDRESS] if config.GMAIL_ADDRESS else [])
        if not to:
            return
        when = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %Y, %I:%M %p IST")
        org = f" ({v.get('org')})" if v.get("org") else ""
        subject = f"Guest entered Echo: {v.get('name') or v.get('email')}{org}"
        v = {k: html_escape(str(val)) if isinstance(val, str) else val for k, val in v.items()}
        org = html_escape(org)
        html = f"""<div style="font-family:-apple-system,Segoe UI,Arial,sans-serif;max-width:520px;margin:0 auto;padding:24px;color:#111">
  <p style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#888;margin:0 0 12px">Echo · guest entered</p>
  <p style="font-size:18px;margin:0 0 14px"><strong>{v.get('name') or v.get('email')}</strong>{org} just opened the dashboard.</p>
  <table style="font-size:14px;border-collapse:collapse">
    <tr><td style="color:#888;padding:3px 14px 3px 0">Email</td><td>{v.get('email')}</td></tr>
    <tr><td style="color:#888;padding:3px 14px 3px 0">Came from</td><td>{v.get('source') or 'direct'}</td></tr>
    <tr><td style="color:#888;padding:3px 14px 3px 0">Visits</td><td>{v.get('visits', 1)}</td></tr>
    <tr><td style="color:#888;padding:3px 14px 3px 0">When</td><td>{when}</td></tr>
  </table>
  <p style="font-size:13px;color:#888;margin:18px 0 0">Demo sessions last 15 minutes with metered X and N-Pro use. The full list is on the Ops desk.</p>
</div>"""
        err = briefing.send_mail(to, subject, html)
        if err:
            log.warning("guest notification failed: %s", err)
    except Exception as exc:  # never let a notification break the login
        log.warning("guest notification error: %s", exc)
