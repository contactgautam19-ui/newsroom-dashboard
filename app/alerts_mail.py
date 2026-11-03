"""Breaking-news alert emails.

The fast lane (app/news/fastlane.py) writes every candidate over its on-screen
bar to `fast_signals`, which the Alerts page reads. That is enough while someone
is looking at the dashboard. This module is for when nobody is: it takes the few
signals that are genuinely worth interrupting someone for and mails them.

Three things keep it from becoming noise an editor learns to ignore:

  * a much higher bar than the on-screen alert (MAIL_FLOOR vs fastlane's 45),
    plus a corroboration minimum — one outlet with an alarming verb is a rumour,
    not a story;
  * one email per signal, ever, recorded on the signal row itself, so a story
    that keeps climbing across scans does not keep mailing;
  * one email per *event*, not per row. A signal key is a headline, and the
    same killing reaches the table as five outlets' five headlines over an
    hour; without this, each one is an unmailed row that leads its own email;
  * a gap between emails, so two scans ninety seconds apart batch into one
    message instead of two. A genuinely enormous story (OVERRIDE_URGENCY) skips
    that gap, because the whole point is latency.

Recipients are the brief recipients plus any demo guest who asked for alerts at
the login screen. Guest copies carry a signed one-click unsubscribe.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, select_autoescape

from app import config, db, settings_store

log = logging.getLogger("newsroom.alerts_mail")

_env = Environment(autoescape=select_autoescape(["html"]))

IST = timezone(timedelta(hours=5, minutes=30))

# Urgency needed to put something in somebody's inbox. fastlane raises an
# on-screen alert at 45 and pulls a story onto the board at 62; this is
# deliberately well above both. For calibration: the Conrad Hotel
# electrocution story scored 85, a chopper crash 99, and a routine
# defence-procurement roundup never clears 50.
MAIL_FLOOR = 72
# Corroboration floor. The arithmetic in fastlane.urgency() lets a single
# outlet reach the high 60s on wording and recency alone; that is exactly the
# case where waiting one more scan costs nothing and being wrong costs trust.
MIN_OUTLETS = 2
# How fresh a signal must be to be worth an email. Older than this and the
# editor either already knows or no longer needs waking.
MAX_AGE_MIN = 90

MIN_GAP_SECONDS = 600       # batching window between emails
OVERRIDE_URGENCY = 88       # big enough to ignore the gap
# Hard ceiling, whatever the wires do. At 3 this was the binding constraint
# rather than a safety net: on 2026-11-03 the queue above MAIL_FLOOR never ran
# dry, so the cap alone set the volume at 22 emails in a shift, backfilling
# with whatever ranked next. Indian wire volume means the floor, not the cap,
# is the real lever -- see the floor/cap table in the commit message.
MAX_PER_HOUR = 2
ALSO_CROSSING = 3           # secondary stories listed under the lead
MAX_GUEST_FANOUT = 25       # one SMTP send each; a scan must not stall on this
# A signal already mailed may be mailed again only if it has climbed this far
# since. A story that arrives as one outlet's line and becomes the day's lead
# is worth a second email; one that drifts up three points is not.
ESCALATION = 12
# How long an event stays mailed. Long enough that a story cannot come back
# round under another outlet's wording later in the same shift, short enough
# that tomorrow's genuine development is not swallowed by yesterday's email.
EVENT_MEMORY_HOURS = 8
EVENT_MEMORY_ROWS = 200

_LAST_MAIL_KEY = "alert_mail_last"
_HOUR_KEY = "alert_mail_hour"
# The off switch. Editors and the hourly brief share one recipient list, so
# there was no way to stop the breaking alerts without stopping the brief too.
# This is checked in the database on every scan, which means switching it off
# takes effect on the next scan rather than on the next deploy.
_ENABLED_KEY = "alert_mail_enabled"
_OFF = {"0", "off", "false", "no", "none"}

# Content that is published on a schedule rather than because something
# happened. The urgency score cannot see the difference: a market wrap carried
# by fifteen outlets within ten minutes of the close collects full marks for
# corroboration and recency, and "Sensex Zooms 600 Points" tested at 77. On the
# Alerts page that is harmless clutter. In an inbox it is the thing that teaches
# an editor the alerts are not worth opening, so it is barred from the mail
# path only -- these stories still appear on screen and still rank normally.
#
# Formats that are never an event, whatever words appear inside them. These get
# no escape hatch, because a digest is a list of other people's stories: "HT
# Morning Brief Nov 3: Indian tourist killed in Nepal" mailed at urgency 99
# when the hatch read "killed" from a story the brief was only summarising.
_NEVER_RE = re.compile(
    r"\b((?:morning|evening|afternoon|night|daily|weekly) (?:brief|briefing|"
    r"digest|news|roundup|round-up|wrap|bulletin)|"
    r"news (?:wrap|roundup|round-up|bulletin|digest|briefing)|"
    r"top (?:headlines|stories|news)|headlines (?:today|of the day)|"
    r"daily (?:quiz|brief|digest|roundup)|word of the day|wordle|"
    r"horoscope|rashifal|panchang|zodiac|numerology|tarot|"
    r"live score|scorecard|playing xi|fantasy (?:tips|xi)|dream11|"
    r"recipe|skincare|weight loss)\b", re.I)

_ROUTINE_RE = re.compile(
    r"\b(sensex|nifty|stock market|share prices?|closing bell|market (?:wrap|close|"
    r"today|outlook)|bse|opening bell|rupee (?:vs|against)|"
    # commodity moves arrive as "Silver prices crash" and "gold drops Rs 2,000",
    # so the plural and the bare verb both have to be here
    r"(?:gold|silver|bullion) (?:rate|price)s?(?: (?:crash(?:es)?|drops?|"
    r"slumps?|surges?|jumps?|climbs?|rises?|falls?|gains?|slips?))?|"
    r"(?:gold|silver) (?:crash(?:es)?|drops?|slumps?|surges?|jumps?|climbs?|"
    r"rises?|falls?)|petrol (?:and |)diesel prices?|fuel prices?|"
    # results season: an earnings line is a calendar entry, not an event
    r"q[1-4] results|quarterly (?:results|earnings)|net profit|earnings call|"
    r"revenue (?:rises|falls|up|down)|"
    r"retention deadline|mini-?auction|player (?:retention|auction)|"
    r"(?:ipl|bcci)[^.]{0,40}(?:trade deal|retention|auction)|"
    # a fixture headline -- "Biratnagar Kings vs Janakpur Bolts" -- is a
    # scheduled match. The escape hatch still rescues "India vs Pakistan talks
    # collapse", because that one says something happened.
    r"match (?:preview|prediction|report)|pitch report| vs |"
    r"box office (?:collection|day \d)|ott release|weather forecast)\b", re.I)


# --------------------------------------------------------------------------
# unsubscribe tokens
# --------------------------------------------------------------------------
def _secret() -> bytes:
    return (config.CRON_SECRET or config.PASSCODE or "dev-alert-secret").encode()


def unsub_token(email: str) -> str:
    return hmac.new(_secret(), f"unsub:{email.lower()}".encode(),
                    hashlib.sha256).hexdigest()[:24]


def unsub_link(email: str) -> str:
    return (f"{config.APP_URL}/api/alerts/unsubscribe"
            f"?e={urllib.parse.quote(email)}&t={unsub_token(email)}")


def unsubscribe(email: str, token: str) -> bool:
    """Turn a guest's alerts off. Returns False when the link is not genuine."""
    email = (email or "").strip().lower()
    if not email or not hmac.compare_digest(unsub_token(email), (token or "")):
        return False
    with db.connect() as con:
        con.execute("UPDATE guest_visitors SET alerts = 0 WHERE email = ?", (email,))
    log.info("alerts unsubscribed: %s", email)
    return True


def enabled() -> bool:
    """Whether breaking alerts may be emailed at all. On unless switched off."""
    raw = (settings_store.get_setting(_ENABLED_KEY, "1") or "1").strip().lower()
    return raw not in _OFF


def set_enabled(on: bool) -> None:
    settings_store.set_setting(_ENABLED_KEY, "1" if on else "0")
    log.info("breaking alert mail %s", "enabled" if on else "disabled")


def set_guest_alerts(email: str, on: bool) -> None:
    now = datetime.now(timezone.utc).isoformat() if on else None
    with db.connect() as con:
        con.execute("UPDATE guest_visitors SET alerts = ?, alerts_at = ? WHERE email = ?",
                    (int(bool(on)), now, (email or "").strip().lower()))


# --------------------------------------------------------------------------
# recipients
# --------------------------------------------------------------------------
def guest_subscribers(limit: int = MAX_GUEST_FANOUT) -> list[str]:
    with db.connect() as con:
        rows = con.execute(
            "SELECT email FROM guest_visitors WHERE alerts = 1 "
            "ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
    return [r["email"] for r in rows if r["email"]]


# --------------------------------------------------------------------------
# throttles
# --------------------------------------------------------------------------
def _hour_count(now: datetime) -> tuple[str, int]:
    stamp = now.strftime("%Y-%m-%dT%H")
    raw = settings_store.get_setting(_HOUR_KEY, "") or ""
    key, _, count = raw.partition("|")
    if key != stamp:
        return stamp, 0
    try:
        return stamp, int(count)
    except ValueError:
        return stamp, 0


def _gap_ok(now: datetime, lead_urgency: int) -> tuple[bool, str]:
    if lead_urgency >= OVERRIDE_URGENCY:
        return True, ""
    last = settings_store.get_setting(_LAST_MAIL_KEY, "") or ""
    if not last:
        return True, ""
    try:
        age = (now - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return True, ""
    if age < MIN_GAP_SECONDS:
        return False, f"within the {MIN_GAP_SECONDS // 60}-minute batching window"
    return True, ""


# --------------------------------------------------------------------------
# the email
# --------------------------------------------------------------------------
ALERT_TEMPLATE = _env.from_string("""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F2F4F7;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;padding:20px 0;">
<tr><td align="center">
<table role="presentation" width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;">

<!-- Header: this is an interruption, and it should look like one -->
<tr><td style="background:#8A0F18;padding:16px 24px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="font-size:12px;font-weight:bold;letter-spacing:1.4px;color:#ffffff;">
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#FFD7D7;margin-right:8px;"></span>
        ECHO &middot; BREAKING WATCH
      </td>
      <td align="right" style="font-size:12px;color:#FFC9CC;">{{ generated_at }} IST</td>
    </tr>
  </table>
</td></tr>

<!-- The lead -->
<tr><td style="padding:24px 24px 6px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td style="vertical-align:top;">
        <div style="font-size:11px;font-weight:bold;letter-spacing:1px;color:#B42318;margin-bottom:8px;">
          {{ lead.outlets }} OUTLET{{ 'S' if lead.outlets != 1 else '' }} CARRYING IT
          &nbsp;&middot;&nbsp; FILED {{ lead.filed }}
        </div>
      </td>
      <td align="right" style="vertical-align:top;width:76px;">
        <div style="background:#FEF3F2;border-radius:8px;padding:6px 0;text-align:center;">
          <div style="font-size:22px;font-weight:bold;color:#B42318;line-height:1;">{{ lead.urgency }}</div>
          <div style="font-size:9.5px;letter-spacing:.5px;color:#B42318;padding-top:3px;">URGENCY</div>
        </div>
      </td>
    </tr>
  </table>

  <div style="font-size:21px;font-weight:bold;color:#101828;line-height:1.3;margin:2px 0 10px;">
    {% if lead.url %}<a href="{{ lead.url }}" target="_blank" style="color:#101828;text-decoration:none;">{{ lead.title }}</a>{% else %}{{ lead.title }}{% endif %}
  </div>
  <div style="font-size:12.5px;color:#667085;margin-bottom:16px;">{{ lead.publisher }}</div>
</td></tr>

<!-- Why: every point traceable. An alert you cannot interrogate is an alert
     you learn to ignore. -->
<tr><td style="padding:0 24px 4px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;border-radius:8px;border-left:4px solid #B42318;">
    <tr><td style="padding:14px 16px;">
      <div style="font-size:11px;font-weight:bold;letter-spacing:1px;color:#667085;margin-bottom:9px;">WHY ECHO FLAGGED THIS</div>
      {% for reason in lead.reasons %}
      <div style="font-size:13px;color:#344054;padding:2px 0;">&bull;&nbsp; {{ reason }}</div>
      {% endfor %}
    </td></tr>
  </table>
</td></tr>

<!-- Where it stands -->
<tr><td style="padding:14px 24px 4px;">
  {% if lead.on_board %}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="background:#ECFDF3;color:#085D3A;font-size:12.5px;font-weight:bold;border-radius:6px;padding:9px 13px;">
      &#10003; Already on the Story Desk{% if lead.promoted %} &mdash; the breaking watch put it there{% endif %}
    </td></tr>
  </table>
  {% else %}
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="background:#FFFAEB;color:#7A3707;font-size:12.5px;font-weight:bold;border-radius:6px;padding:9px 13px;">
      &#9680; Not on the board yet &mdash; spotted on the wires, not scored
    </td></tr>
  </table>
  {% endif %}
</td></tr>

<!-- Also crossing -->
{% if others %}
<tr><td style="padding:18px 24px 2px;">
  <p style="font-size:11px;font-weight:bold;letter-spacing:1px;color:#667085;margin:0 0 10px;">ALSO CROSSING NOW</p>
</td></tr>
{% for s in others %}
<tr><td style="padding:0 24px 8px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;border-radius:8px;">
    <tr><td style="padding:11px 14px;">
      <div style="font-size:13.5px;color:#101828;line-height:1.35;margin-bottom:4px;">
        {% if s.url %}<a href="{{ s.url }}" target="_blank" style="color:#101828;text-decoration:none;">{{ s.title }}</a>{% else %}{{ s.title }}{% endif %}
      </div>
      <div style="font-size:11.5px;color:#98A2B3;">
        urgency {{ s.urgency }} &middot; {{ s.outlets }} outlet{{ 's' if s.outlets != 1 else '' }} &middot; {{ s.publisher }}
      </div>
    </td></tr>
  </table>
</td></tr>
{% endfor %}
{% endif %}

<!-- CTA -->
<tr><td style="padding:20px 24px 24px;" align="center">
  <table role="presentation" cellpadding="0" cellspacing="0">
    <tr><td style="background:#0B1526;border-radius:8px;">
      <a href="{{ app_url }}#alerts" target="_blank" style="display:inline-block;padding:12px 26px;font-size:13.5px;font-weight:bold;color:#ffffff;text-decoration:none;">
        Open the Alerts desk &rarr;
      </a>
    </td></tr>
  </table>
</td></tr>

<!-- Footer -->
<tr><td style="padding:16px 24px 24px;border-top:1px solid #EAECF0;" align="center">
  <p style="font-size:11px;color:#98A2B3;margin:0;line-height:1.6;">
    Raised by the breaking watch, which scans the 50-source matrix and live search trends every few minutes.<br>
    Advisory only &mdash; nothing is verified or published without a human editor.
    {% if unsub %}<br><a href="{{ unsub }}" style="color:#98A2B3;">Stop these alerts</a>{% endif %}
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>
""")


def _filed(published_at: str | None, now: datetime) -> str:
    """'14 minutes ago' reads better in an alert than a timestamp."""
    if not published_at:
        return "just now"
    try:
        age = (now - datetime.fromisoformat(published_at)).total_seconds() / 60
    except (ValueError, TypeError):
        return "just now"
    if age < 2:
        return "moments ago"
    if age < 60:
        return f"{int(age)} min ago"
    hours = age / 60
    return f"{hours:.0f} hr ago" if hours < 24 else "earlier"


def _prepare(row: dict, now: datetime) -> dict:
    try:
        reasons = json.loads(row.get("reasons") or "[]")
    except (ValueError, TypeError):
        reasons = []
    return {
        "title": row.get("title") or "",
        "url": row.get("url") or "",
        "publisher": row.get("publisher") or "unattributed",
        "urgency": int(row.get("urgency") or 0),
        "outlets": int(row.get("outlets") or 1),
        "reasons": [str(r) for r in reasons][:6],
        "on_board": bool(row.get("on_board")),
        "promoted": bool(row.get("promoted")),
        "filed": _filed(row.get("published_at"), now),
    }


def build(lead: dict, others: list[dict], now: datetime,
          unsub: str | None = None) -> tuple[str, str]:
    ist = now.astimezone(IST)
    extra = f" (+{len(others)} more)" if others else ""
    subject = f"Echo breaking: {lead['title'][:110]}{extra}"
    html = ALERT_TEMPLATE.render(
        lead=lead, others=others, unsub=unsub,
        generated_at=f"{ist:%d %b, %I:%M %p}".replace(" 0", " "),
        app_url=config.APP_URL,
    )
    return subject, html


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def _is_routine(title: str) -> bool:
    """Scheduled content rather than an event — unless the headline says an
    event happened.

    The second half matters: "Sensex" alone is a market report, but
    "Sensex-listed firm chairman arrested in fraud case" is a story, and a
    keyword list without this escape hatch would silently swallow it. The
    incident vocabulary is fastlane's, so the two stay in step.

    The escape hatch reads only what is left once the scheduled-content
    phrasing is removed, because markets borrow the vocabulary of disasters:
    "Silver prices crash" matched fastlane's word for a plane coming down and
    mailed at urgency 100. "Sensex-listed firm chairman arrested" keeps its
    incident word, because that word is not part of the market phrasing.
    """
    title = title or ""
    if _NEVER_RE.search(title):
        return True
    if not _ROUTINE_RE.search(title):
        return False
    from app.news.fastlane import _HARD_RE, _SOFT_RE
    rest = _ROUTINE_RE.sub(" ", title)
    return not (_HARD_RE.search(rest) or _SOFT_RE.search(rest))


def _mailed_events(now: datetime) -> list[tuple[set, int]]:
    """Event signatures already mailed this shift, with the score they went out
    at, so the same story cannot lead a second email under another outlet's
    wording."""
    from app.news.ingest import _sig_tokens
    since = (now - timedelta(hours=EVENT_MEMORY_HOURS)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT title, alerted_urgency FROM fast_signals "
            "WHERE alerted = 1 AND alerted_at >= ? "
            "ORDER BY alerted_at DESC LIMIT ?",
            (since, EVENT_MEMORY_ROWS)).fetchall()
    out: list[tuple[set, int]] = []
    for row in rows:
        tokens = _sig_tokens(row["title"] or "")
        if len(tokens) >= 2:
            out.append((tokens, int(row["alerted_urgency"] or 0)))
    return out


def _shortlist(rows: list[dict],
               mailed: list[tuple[set, int]] | None = None) -> list[dict]:
    """Narrow scored signals to the ones worth an inbox: drop scheduled
    content, then drop repeats of an event -- whether the repeat is in this
    same batch or was already mailed earlier.

    The clusterer is title-based, so one death reaches the table as "dies at
    57", "dies at 59" and "RIP" from three outlets. Within one email that reads
    as though the system cannot tell them apart. Across emails it is worse: each
    variant is its own unmailed row, so one killing off Oman sent five separate
    alerts in a morning. An already-mailed event therefore comes back only if
    it has climbed ESCALATION beyond the score it went out at, which is a
    developing story rather than another outlet filing the same one.
    """
    from app.news.ingest import _sig_tokens, _same_event
    kept: list[dict] = []
    signatures: list[set] = []
    for row in rows:
        title = row.get("title") or ""
        if _is_routine(title):
            continue
        tokens = _sig_tokens(title)
        if len(tokens) >= 2:
            if any(_same_event(tokens, seen) for seen in signatures):
                continue
            urgency = int(row.get("urgency") or 0)
            if any(_same_event(tokens, seen) and urgency < was + ESCALATION
                   for seen, was in (mailed or [])):
                continue
        signatures.append(tokens)
        kept.append(row)
    return kept


def candidates(now: datetime | None = None) -> list[dict]:
    """Signals that clear every bar and have not already been mailed at this
    strength, strongest first and narrowed to one row per real event."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(minutes=MAX_AGE_MIN)).isoformat()
    with db.connect() as con:
        rows = con.execute(
            "SELECT * FROM fast_signals WHERE urgency >= ? AND outlets >= ? "
            "AND first_seen >= ? AND (alerted = 0 OR urgency >= alerted_urgency + ?) "
            "ORDER BY urgency DESC, first_seen DESC LIMIT ?",
            (MAIL_FLOOR, MIN_OUTLETS, since, ESCALATION,
             (ALSO_CROSSING + 1) * 8)).fetchall()
    return _shortlist(db.rows_to_dicts(rows),
                      _mailed_events(now))[:ALSO_CROSSING + 1]


def dispatch(now: datetime | None = None) -> dict:
    """Mail the breaking signals worth interrupting someone for.

    Called at the end of every fast scan. Returns a small report rather than
    raising, so a mail problem can never cost us the scan that found the story.
    """
    now = now or datetime.now(timezone.utc)
    if not enabled():
        return {"sent": False, "reason": "breaking alerts are switched off"}
    rows = candidates(now)
    if not rows:
        return {"sent": False, "reason": "nothing over the bar"}

    lead_row, *other_rows = rows
    lead = _prepare(lead_row, now)

    ok, why = _gap_ok(now, lead["urgency"])
    if not ok:
        return {"sent": False, "reason": why, "waiting_on": lead["title"][:80]}

    stamp, count = _hour_count(now)
    if count >= MAX_PER_HOUR:
        return {"sent": False, "reason": f"hourly cap of {MAX_PER_HOUR} reached"}

    editors = settings_store.get_recipients()
    guests = [e for e in guest_subscribers() if e not in editors]
    if not editors and not guests:
        return {"sent": False, "reason": "no recipients"}

    others = [_prepare(r, now) for r in other_rows]
    from app import briefing

    errors, delivered = [], 0
    if editors:
        subject, html = build(lead, others, now)
        err = briefing.send_mail(editors, subject, html)
        if err:
            errors.append(err)
        else:
            delivered += len(editors)
    for email in guests:
        subject, html = build(lead, others, now, unsub=unsub_link(email))
        err = briefing.send_mail([email], subject, html)
        if err:
            errors.append(f"{email}: {err}")
        else:
            delivered += 1

    if not delivered:
        # Nothing went out, so nothing is marked. The next scan retries, and
        # the hourly cap is not spent on a send that never happened.
        log.warning("breaking alert not delivered: %s", "; ".join(errors[:2]))
        return {"sent": False, "reason": "send failed", "errors": errors[:3]}

    sent_at = now.isoformat()
    with db.connect() as con:
        for row in rows:
            con.execute(
                "UPDATE fast_signals SET alerted = 1, alerted_at = ?, "
                "alerted_urgency = ? WHERE signal_key = ?",
                (sent_at, int(row.get("urgency") or 0), row["signal_key"]))
    settings_store.set_setting(_LAST_MAIL_KEY, sent_at)
    settings_store.set_setting(_HOUR_KEY, f"{stamp}|{count + 1}")

    log.info("breaking alert sent to %s inbox(es): %r (urgency %s, +%s more)",
             delivered, lead["title"][:60], lead["urgency"], len(others))
    return {"sent": True, "at": sent_at, "recipients": delivered,
            "lead": lead["title"], "urgency": lead["urgency"],
            "also": len(others), "errors": errors[:3]}
