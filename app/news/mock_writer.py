"""Mock article writer — produces clearly-labeled template drafts assembled
ONLY from real story-pack data, with no AI call. Used whenever no Anthropic
API key is configured so the pick -> story pack -> "Write article" flow still
works end to end. Never invents facts: every sentence traces back to a pack
field (title, sources, evidence_lines, related_tweets, format_suggestions)."""

import re

MOCK_PREFIX = (
    "[MOCK DRAFT — generated from story-pack data without AI. Add an "
    "Anthropic API key in Ops → AI writer settings for real channel-voice "
    "drafts.]\n\n"
)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _sources_line(pack: dict) -> str:
    sources = pack.get("sources") or []
    if sources:
        return "Sources: " + ", ".join(sources)
    return "Sources: " + (pack.get("publisher") or "—")


def _lead_publisher(pack: dict) -> str:
    sources = pack.get("sources") or []
    return sources[0] if sources else (pack.get("publisher") or "our newsroom")


def _hashtag_words(title: str) -> list[str]:
    words = re.findall(r"[A-Za-z']+", title or "")
    out = []
    for w in words:
        if w.isdigit():
            continue
        if re.match(r"^\d", w):
            continue
        if len(w) < 4:
            continue
        cleaned = re.sub(r"[^A-Za-z]", "", w)
        if not cleaned:
            continue
        out.append(cleaned[0].upper() + cleaned[1:])
        if len(out) >= 3:
            break
    return out


def _web_draft(pack: dict) -> str:
    title = pack.get("title", "")
    location = pack.get("location") or ""
    publisher = _lead_publisher(pack)
    facts = pack.get("evidence_lines") or []
    sources = pack.get("sources") or []
    tweets = pack.get("related_tweets") or []

    headlines = [f"1. {title}"]
    if location and location.lower() not in title.lower():
        headlines.append(f"2. «{location}»: {title}")
    else:
        headlines.append(f"2. {title}")
    headlines.append(f"3. «{title}» — what we know so far")

    lines = ["HEADLINE OPTIONS:"]
    lines += headlines
    lines += ["", "ARTICLE:"]
    lines.append(f"{title}, according to {publisher}.")
    lines.append("")
    lines.append("What we know so far:")
    if facts:
        lines += [f"- {f}" for f in facts]
    else:
        lines.append("- Details awaited.")
    lines.append("")
    if sources:
        names = ", ".join(sources)
        lines.append(f"Corroboration: {len(sources)} source(s) — {names}.")
    else:
        lines.append(f"Corroboration: reported by {publisher}.")
    if tweets:
        lines.append("")
        lines.append("On the ground:")
        for t in tweets:
            handle = t.get("handle") or ""
            text = t.get("text") or ""
            lines.append(f'{handle}: "{text}"')
    lines.append("")
    lines.append("Details awaited. This is a developing story.")
    return "\n".join(lines)


def _broadcast_draft(pack: dict) -> str:
    title = pack.get("title", "")
    publisher = _lead_publisher(pack)
    facts = pack.get("evidence_lines") or []
    top2 = facts[:2]
    rest = facts[2:]

    lines = ["ANCHOR INTRO (20s):"]
    lines.append(f"{title}.")
    lines.append(f"That's according to {publisher} — here's what we know.")
    lines.append("")
    lines.append("VO 1:")
    if top2:
        lines += top2
    else:
        lines.append("Details awaited.")
    lines.append("")
    lines.append("[BYTE: reporter/official — awaited]")
    lines.append("")
    lines.append("VO 2:")
    if rest:
        lines += rest
    else:
        lines.append("Details awaited.")
    lines.append("")
    lines.append(f"[GFX: {_truncate(title, 60)}]")
    lines.append("")
    lines.append("CLOSING:")
    lines.append("We will bring you more as this develops.")
    return "\n".join(lines)


def _social_draft(pack: dict) -> str:
    title = pack.get("title", "")
    facts = pack.get("evidence_lines") or []
    suggestions = pack.get("format_suggestions") or []

    lines = ["X THREAD:"]
    lines.append(f"1. {_truncate(title, 250)}")
    n = 2
    for f in facts[:3]:
        lines.append(f"{n}. {_truncate(f, 250)}")
        n += 1
    lines.append(f"{n}. More updates to follow. Stay with us.")

    lines.append("")
    lines.append("INSTAGRAM:")
    lines.append(title)
    for f in facts[:2]:
        lines.append(f"- {f}")
    hashtags = _hashtag_words(title)
    if hashtags:
        lines.append(" ".join(f"#{h}" for h in hashtags))

    lines.append("")
    lines.append("FACEBOOK:")
    lines.append(title)
    if facts:
        lines.append(" ".join(facts))
    engagement = next(
        (s.get("because") for s in suggestions if "engagement" in (s.get("format") or "").lower()
         or "question" in (s.get("format") or "").lower()),
        None,
    )
    lines.append("What do you think — follow for updates.")
    return "\n".join(lines)


_BUILDERS = {"web": _web_draft, "broadcast": _broadcast_draft, "social": _social_draft}


def generate_mock(pack: dict, fmt: str) -> str:
    builder = _BUILDERS.get(fmt, _web_draft)
    body = builder(pack)
    return MOCK_PREFIX + body
