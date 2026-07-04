"""Hourly editorial brief: an email-safe HTML rundown built for a quick
Gmail-inbox editorial decision — every section maps back to the guardrail
fields already computed by the ranking + rival-monitor + X-desk pipelines.
Saved to the briefings table (and disk) and emailed via Gmail SMTP when
EMAIL_ENABLED."""

import logging
import smtplib
import ssl
from datetime import datetime, timezone

from jinja2 import Environment, select_autoescape

from app import config, db, settings_store
from app.news.ingest import get_rundown
from app.x.signals import top_signals

log = logging.getLogger("newsroom.briefing")

_env = Environment(autoescape=select_autoescape(["html"]))

# Same "why" phrase mapping used on the Story Desk board (static/js/story_desk.js
# WHY), reused here so the email and dashboard never disagree.
_WHY = {
    "breaking": lambda b: "Very fresh development"
        if any("published" in (e or "") for e in b.get("evidence") or []) else "Breaking development",
    "political": lambda b: "Power centre involved",
    "emotion": lambda b: "Strong emotional pull",
    "celebrity": lambda b: "High-profile names",
    "economy": lambda b: "Money impact for viewers",
    "safety": lambda b: "Public-safety relevance",
    "visual": lambda b: "Strong visuals available",
    "novelty": lambda b: "Unusual, unexpected angle",
    "trend": lambda b: "Trending on X right now",
}

STATUS_COLORS = {
    "breaking": "#D92D20",
    "developing": "#F79009",
    "verified": "#079455",
}


def why_line(story: dict) -> str:
    parts = []
    positives = sorted(
        (b for b in story.get("breakdown") or [] if b.get("points", 0) > 0),
        key=lambda b: b["points"], reverse=True,
    )[:3]
    for b in positives:
        fn = _WHY.get(b["variable"])
        parts.append(fn(b) if fn else b["variable"])
    if story.get("rival_coverage"):
        parts.insert(0, f"Rivals airing this now ({', '.join(story['rival_coverage'])})")
    return "  ·  ".join(parts)


def publish_call(story: dict) -> dict:
    """Derive a publish-call decision from existing guardrail fields
    (confidence, sources, needs_review, status) — never invents new signal."""
    confidence = story.get("confidence") or 0
    sources = story.get("sources") or []
    n_sources = len(sources)
    needs_review = bool(story.get("needs_review"))
    status = story.get("status")

    ready = confidence >= 70 and n_sources >= 2 and not needs_review
    if ready:
        return {
            "label": "READY",
            "tone": "ready",
            "detail": f"verified, {n_sources} sources",
        }

    if n_sources < 2:
        detail = "single source — needs a second confirmation"
        if status == "breaking":
            detail = "breaking claim not yet corroborated (two-source rule)"
    elif needs_review or confidence < 70:
        detail = f"confidence {confidence}% — verify before air"
    else:
        detail = "review before publishing"

    return {"label": "VERIFY FIRST", "tone": "verify", "detail": detail}


BRIEF_TEMPLATE = _env.from_string("""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F2F4F7;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F2F4F7;padding:20px 0;">
<tr><td align="center">
<table role="presentation" width="620" cellpadding="0" cellspacing="0" style="max-width:620px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;">

<!-- Header -->
<tr><td style="background:#0B1526;padding:20px 24px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td style="font-size:16px;font-weight:bold;color:#ffffff;">
      <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#E11D2E;margin-right:8px;"></span>
      Newsroom hourly rundown
    </td></tr>
    <tr><td style="font-size:12px;color:#9AA5B1;padding-top:6px;">
      {{ generated_at }} IST &middot; for editorial decision — nothing publishes without your sign-off
    </td></tr>
  </table>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:16px;">
    <tr>
      <td align="center" style="padding:6px 4px;">
        <div style="font-size:20px;font-weight:bold;color:#F04438;">{{ counts.breaking }}</div>
        <div style="font-size:10.5px;color:#9AA5B1;">breaking</div>
      </td>
      <td align="center" style="padding:6px 4px;">
        <div style="font-size:20px;font-weight:bold;color:#FDB022;">{{ counts.verify }}</div>
        <div style="font-size:10.5px;color:#9AA5B1;">need verification</div>
      </td>
      <td align="center" style="padding:6px 4px;">
        <div style="font-size:20px;font-weight:bold;color:#F97066;">{{ counts.rival }}</div>
        <div style="font-size:10.5px;color:#9AA5B1;">on air at rivals</div>
      </td>
      <td align="center" style="padding:6px 4px;">
        <div style="font-size:20px;font-weight:bold;color:#53B1FD;">{{ counts.trending }}</div>
        <div style="font-size:10.5px;color:#9AA5B1;">trending on X</div>
      </td>
    </tr>
  </table>
</td></tr>

<!-- Top stories -->
<tr><td style="padding:22px 24px 4px;">
  <p style="font-size:11px;font-weight:bold;letter-spacing:1px;color:#667085;margin:0 0 12px;">
    TOP STORIES &mdash; RANKED BY THE 100-POINT FRAMEWORK
  </p>
</td></tr>

{% for s in stories %}
<tr><td style="padding:0 24px 14px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#F9FAFB;border-radius:8px;border-left:4px solid {{ status_colors.get(s.status, '#667085') }};">
    <tr><td style="padding:14px 16px;">

      <!-- chip row -->
      <div style="font-size:11.5px;color:#475467;margin-bottom:8px;">
        <span style="background:#EEF1F5;color:#344054;font-weight:bold;border-radius:20px;padding:2px 9px;margin-right:5px;display:inline-block;">
          #{{ loop.index }} &middot; {{ s.score }}/100 &middot; {{ s.status | upper }}
        </span>
        {% if s.rival_coverage %}
        <span style="background:#FEF3F2;color:#B42318;font-weight:bold;border-radius:20px;padding:2px 9px;margin-right:5px;display:inline-block;">
          &#128250; On air: {{ s.rival_coverage | join(', ') }}
        </span>
        {% endif %}
        {% if s.trend_boost and s.trend_boost > 0 %}
        <span style="background:#EFF8FF;color:#175CD3;font-weight:bold;border-radius:20px;padding:2px 9px;display:inline-block;">
          &#8599; trending on X
        </span>
        {% endif %}
      </div>

      <!-- headline -->
      <div style="font-size:15px;font-weight:bold;color:#101828;margin-bottom:6px;line-height:1.35;">
        {% if s.url %}<a href="{{ s.url }}" target="_blank" style="color:#101828;text-decoration:none;">{{ s.title }}</a>{% else %}{{ s.title }}{% endif %}
      </div>

      <!-- meta line -->
      <div style="font-size:12px;color:#667085;margin-bottom:8px;">
        {{ s.publisher }}{% if s.sources and s.sources|length > 1 %} + {{ s.sources|length - 1 }} corroborating outlet{{ 's' if s.sources|length - 1 > 1 else '' }}{% endif %}
        &middot; {{ s.category }}
        {% if s.discovered_via %} &middot; found via trending keyword "{{ s.discovered_via }}"{% endif %}
      </div>

      <!-- why line -->
      {% if s.why %}
      <div style="font-size:12px;color:#475467;margin-bottom:10px;">
        <span style="font-weight:bold;color:#344054;">Why:</span> {{ s.why }}
      </div>
      {% endif %}

      <!-- publish call pill -->
      {% if s.call.tone == 'ready' %}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:#ECFDF3;color:#085D3A;font-size:12.5px;font-weight:bold;border-radius:6px;padding:8px 12px;">
          &#10003; Publish call: READY &mdash; {{ s.call.detail }}
        </td></tr>
      </table>
      {% else %}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:#FFFAEB;color:#7A3707;font-size:12.5px;font-weight:bold;border-radius:6px;padding:8px 12px;">
          &#9680; Publish call: VERIFY FIRST &mdash; {{ s.call.detail }}
        </td></tr>
      </table>
      {% endif %}

    </td></tr>
  </table>
</td></tr>
{% else %}
<tr><td style="padding:0 24px 14px;">
  <p style="font-size:13px;color:#667085;">No active stories this cycle.</p>
</td></tr>
{% endfor %}

<!-- X desk -->
{% if signals %}
<tr><td style="padding:10px 24px 4px;">
  <p style="font-size:11px;font-weight:bold;letter-spacing:1px;color:#667085;margin:16px 0 12px;">
    FROM THE X DESK &mdash; TOP SIGNALS THIS HOUR
  </p>
</td></tr>
{% for sig in signals %}
<tr><td style="padding:0 24px 10px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F9FAFB;border-radius:8px;">
    <tr><td style="padding:12px 16px;">
      <div style="font-size:13px;color:#101828;margin-bottom:4px;">
        <span style="font-weight:bold;">{{ sig.display_name }} @{{ sig.handle }}</span> &mdash; {{ sig.summary }}
      </div>
      <div style="font-size:11.5px;color:#98A2B3;">
        {{ sig.reasons | join(' · ') }}{% if sig.linked_story %} · matches board story{% endif %}
      </div>
    </td></tr>
  </table>
</td></tr>
{% endfor %}
{% endif %}

<!-- CTA -->
<tr><td style="padding:18px 24px 22px;" align="center">
  <table role="presentation" cellpadding="0" cellspacing="0">
    <tr><td style="background:#0B1526;border-radius:8px;">
      <a href="{{ app_url }}" target="_blank" style="display:inline-block;padding:12px 26px;font-size:13.5px;font-weight:bold;color:#ffffff;text-decoration:none;">
        Open the dashboard &rarr;
      </a>
    </td></tr>
  </table>
</td></tr>

<!-- Footer -->
<tr><td style="padding:16px 24px 24px;border-top:1px solid #EAECF0;" align="center">
  <p style="font-size:11px;color:#98A2B3;margin:0;line-height:1.5;">
    Compiled from the 50-source RSS matrix &middot; 6 rival live channels &middot; monitored X handles &middot; live search trends.<br>
    Advisory only — rankings are evidence-based; final rundown control stays with human editors.
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>
""")


def build_brief() -> tuple[str, str]:
    stories = get_rundown(limit=6)
    signals = top_signals()
    now = datetime.now()

    calls = [publish_call(s) for s in stories]
    for s, c in zip(stories, calls):
        s["call"] = c
        s["why"] = why_line(s)

    n_breaking = sum(1 for s in stories if s.get("status") == "breaking")
    n_verify = sum(1 for c in calls if c["tone"] == "verify")
    n_ready = sum(1 for c in calls if c["tone"] == "ready")
    n_rival = sum(1 for s in stories if s.get("rival_coverage"))
    n_trending = sum(1 for s in stories if (s.get("trend_boost") or 0) > 0)

    counts = {
        "breaking": n_breaking, "verify": n_verify,
        "rival": n_rival, "trending": n_trending,
    }

    time_str = f"{now:%d %b %I:%M}".replace(" 0", " ")
    subject = (
        f"Newsroom rundown — {len(stories)} stories, {n_breaking} breaking, "
        f"{n_ready} ready to publish — {time_str}"
    )

    html = BRIEF_TEMPLATE.render(
        stories=stories,
        signals=signals,
        counts=counts,
        generated_at=f"{now:%d %b %Y, %I:%M %p}".replace(" 0", " "),
        status_colors=STATUS_COLORS,
        app_url=config.APP_URL,
    )
    return subject, html


def send_email(subject: str, html: str) -> str | None:
    """Returns an error string, or None on success."""
    if not config.EMAIL_ENABLED:
        return "email disabled (EMAIL_ENABLED=false)"
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        return "missing GMAIL_ADDRESS / GMAIL_APP_PASSWORD"

    recipients = settings_store.get_recipients()
    if not recipients:
        return "no brief recipients configured"

    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.GMAIL_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465,
                              context=ssl.create_default_context()) as server:
            server.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_ADDRESS, recipients, msg.as_string())
        return None
    except Exception as exc:
        log.error("brief email failed: %s", exc)
        return str(exc)


def generate_and_send() -> dict:
    subject, html = build_brief()
    error = send_email(subject, html)
    now_iso = datetime.now(timezone.utc).isoformat()
    with db.connect() as con:
        con.execute(
            "INSERT INTO briefings (created_at, subject, html, emailed, email_error) "
            "VALUES (?,?,?,?,?)",
            (now_iso, subject, html, int(error is None), error),
        )
    config.BRIEF_OUT_DIR.mkdir(exist_ok=True)
    out = config.BRIEF_OUT_DIR / f"brief_{datetime.now():%Y%m%d_%H%M}.html"
    out.write_text(html, encoding="utf-8")
    return {"subject": subject, "emailed": error is None, "error": error,
            "saved_to": str(out)}
