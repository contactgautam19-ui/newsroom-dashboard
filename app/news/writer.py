"""AI article writer. Turns a story pack into a labeled draft in one of three
formats (web | broadcast | social). The editorial guardrail is absolute: the
model may only use facts present in the supplied story material, and every
draft is stored/labeled as requiring human review — the AI never publishes."""

from datetime import datetime, timezone

import anthropic
from anthropic import Anthropic

from app import db, settings_store
from app.news.pack import build_pack

MODEL_DEFAULT = "claude-opus-4-8"
MAX_TOKENS = 8000

_FORMAT_INSTRUCTIONS = {
    "web": (
        "FORMAT — DIGITAL WEB ARTICLE.\n"
        "Write: (1) three headline options, (2) a complete digital news article "
        "of 500-800 words with source attribution woven in."
    ),
    "broadcast": (
        "FORMAT — TV BROADCAST PACKAGE.\n"
        "Write a TV package script: ANCHOR INTRO (20-30 seconds), then VO "
        "paragraphs, with [BYTE: who + expected content] placeholders and "
        "[GFX: text] graphic cues, and a closing line. Broadcast register, "
        "short sentences."
    ),
    "social": (
        "FORMAT — SOCIAL COPY.\n"
        "Write: (1) an X thread of 5-7 numbered posts (each under 280 chars, "
        "first post is the hook), (2) an Instagram caption with line breaks and "
        "3-5 hashtags, (3) a Facebook post (longer, ends with an engagement "
        "question)."
    ),
}


def _build_system_prompt(settings: dict) -> str:
    """Stable voice profile first, per prompt-caching best practice."""
    channel = settings.get("channel_name") or "this news channel"
    voice = settings.get("voice_description") or "clear, accurate broadcast news style"
    samples = settings.get("sample_articles") or ""

    parts = [
        f"You are the AI article writer for {channel}, drafting copy for human "
        "editors to review. You never publish; a producer signs off on everything "
        "you write.",
        "",
        "CHANNEL VOICE:",
        voice,
    ]
    if samples.strip():
        parts += [
            "",
            "SAMPLE ARTICLES — imitate the voice, register and structure of these "
            "published examples; never copy their content:",
            samples,
        ]
    parts += [
        "",
        "HARD EDITORIAL RULES (non-negotiable):",
        "- Use ONLY facts present in the provided story material.",
        "- Never invent quotes, names, numbers, or sources.",
        "- Attribute claims to the listed sources.",
        "- If information is missing, write \"details awaited\" rather than "
        "inventing it.",
        "- Mark any uncertainty explicitly.",
        "- Output plain text. Do not use markdown header syntax like ###.",
    ]
    return "\n".join(parts)


def _build_user_prompt(pack: dict, fmt: str) -> str:
    sources = pack.get("sources") or []
    corroborating = ", ".join(sources) if sources else (pack.get("publisher") or "—")
    lines = [
        "STORY BUNDLE — draft only from what is below.",
        "",
        f"Headline: {pack.get('title', '')}",
        f"Lead publisher: {pack.get('publisher') or '—'}",
        f"Corroborating sources: {corroborating}",
        f"Category: {pack.get('category') or '—'} | Location: "
        f"{pack.get('location') or '—'} | Status: {pack.get('status') or '—'}",
        f"Score: {pack.get('score', '—')}/100 | Confidence: "
        f"{pack.get('confidence', '—')}%",
    ]
    if pack.get("url"):
        lines.append(f"Lead article URL: {pack['url']}")

    evidence = pack.get("evidence_lines") or []
    if evidence:
        lines += ["", "Evidence lines:"]
        lines += [f"- {e}" for e in evidence]

    tweets = pack.get("related_tweets") or []
    if tweets:
        lines += ["", "Related posts from monitored X handles:"]
        for t in tweets:
            handle = t.get("handle") or ""
            lines.append(f"- {handle}: {t.get('text', '')}")

    lines += ["", _FORMAT_INSTRUCTIONS[fmt]]
    return "\n".join(lines)


def _store_article(story_id: int, fmt: str, content: str, model: str,
                   created_at: str, input_tokens, output_tokens, error) -> int:
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO articles (story_id, format, content, model, created_at, "
            "input_tokens, output_tokens, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (story_id, fmt, content, model, created_at, input_tokens,
             output_tokens, error),
        )
        return cur.lastrowid


def _fail(story_id: int, fmt: str, model: str, message: str) -> dict:
    _store_article(story_id, fmt, "", model,
                   datetime.now(timezone.utc).isoformat(), None, None, message)
    return {"ok": False, "error": message}


def generate_article(story_id: int, fmt: str) -> dict:
    pack = build_pack(story_id)
    if pack is None:
        raise ValueError(f"Story {story_id} not found")

    settings = settings_store.get_public_settings()  # for channel/voice display
    channel_name = settings_store.get_setting("channel_name", "")
    voice_description = settings_store.get_setting("voice_description", "")
    sample_articles = settings_store.get_setting("sample_articles", "")
    model = settings_store.get_setting("writer_model", MODEL_DEFAULT) or MODEL_DEFAULT
    key = settings_store.get_setting("anthropic_api_key", "")

    if not key:
        from app.news.mock_writer import generate_mock
        content = generate_mock(pack, fmt)
        created_at = datetime.now(timezone.utc).isoformat()
        article_id = _store_article(story_id, fmt, content, "mock", created_at,
                                    None, None, None)
        return {
            "ok": True,
            "article": {
                "id": article_id,
                "story_id": story_id,
                "format": fmt,
                "content": content,
                "model": "mock",
                "created_at": created_at,
                "input_tokens": None,
                "output_tokens": None,
            },
        }

    system_prompt = _build_system_prompt({
        "channel_name": channel_name,
        "voice_description": voice_description,
        "sample_articles": sample_articles,
    })
    user_prompt = _build_user_prompt(pack, fmt)

    try:
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.AuthenticationError:
        return _fail(story_id, fmt, model,
                     "Anthropic API key invalid — check Ops → AI writer settings.")
    except anthropic.RateLimitError:
        return _fail(story_id, fmt, model,
                     "Rate limited by Anthropic — try again in a minute.")
    except anthropic.APIStatusError as e:
        return _fail(story_id, fmt, model,
                     f"Anthropic API error (status {e.status_code}).")
    except anthropic.APIConnectionError:
        return _fail(story_id, fmt, model, "Network error reaching Anthropic.")

    if resp.stop_reason == "refusal":
        return _fail(story_id, fmt, model,
                     "The model declined to draft this story. Review the source "
                     "material and try again.")

    text = "".join(b.text for b in resp.content if b.type == "text")
    input_tokens = resp.usage.input_tokens
    output_tokens = resp.usage.output_tokens
    created_at = datetime.now(timezone.utc).isoformat()

    article_id = _store_article(story_id, fmt, text, model, created_at,
                                input_tokens, output_tokens, None)
    return {
        "ok": True,
        "article": {
            "id": article_id,
            "story_id": story_id,
            "format": fmt,
            "content": text,
            "model": model,
            "created_at": created_at,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
