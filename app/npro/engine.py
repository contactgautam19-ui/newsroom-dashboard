"""N-Pro generation engine.

Turns a story + retrieved reporting + a chosen format recipe into a
broadcast-ready script. Uses Claude when an API key is configured (Ops → AI
writer settings); otherwise falls back to grounded template output clearly
labelled as a mock, so every flow works without a key. Retrieval, summary and
the intelligence panel are keyless.
"""

import json
import re
from collections import defaultdict

from app import settings_store
from app.npro import recipes

MODEL_DEFAULT = "claude-opus-4-8"
MAX_TOKENS = 6000

SYSTEM = (
    "You are N-Pro, an AI news-production assistant for a television newsroom. "
    "You specialise ONLY in news production — turning breaking news into "
    "broadcast-ready scripts. Politely decline anything unrelated to news "
    "production.\n\n"
    "HARD RULES (non-negotiable):\n"
    "- Use ONLY facts present in the supplied reporting. Never fabricate quotes, "
    "names, numbers or events.\n"
    "- Distinguish verified information from developing reports; label anything "
    "unconfirmed as (UNVERIFIED).\n"
    "- Write in broadcast-friendly language optimised for live delivery: short "
    "sentences, easy to read aloud, clarity over sensationalism.\n"
    "- Never publish; a human producer reviews everything you write.\n"
    "- Suggest stronger editorial angles when useful."
)


def _key() -> str:
    return settings_store.get_setting("anthropic_api_key", "") or ""


def has_key() -> bool:
    return bool(_key())


def _call(system: str, user: str, max_tokens: int = MAX_TOKENS) -> str | None:
    """Call Claude; return text, or None on any failure / missing key."""
    key = _key()
    if not key:
        return None
    try:
        import anthropic
        from anthropic import Anthropic
        model = settings_store.get_setting("writer_model", MODEL_DEFAULT) or MODEL_DEFAULT
        client = Anthropic(api_key=key)
        resp = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:  # any SDK/network error -> caller falls back to mock
        return None


# ── context assembly ───────────────────────────────────────────────────────

def context_block(story: dict | None, retrieved: list[dict]) -> str:
    lines = ["REPORTING AVAILABLE — draft only from what is below.", ""]
    if story:
        lines.append(f"Lead story: {story.get('title', '')}")
        if story.get("publisher"):
            lines.append(f"Lead publisher: {story['publisher']}")
        srcs = story.get("sources") or []
        if srcs:
            lines.append(f"Corroborating outlets: {', '.join(srcs)}")
        for e in (story.get("evidence_lines") or [])[:6]:
            lines.append(f"- {e}")
        lines.append("")
    if retrieved:
        lines.append("Recent reporting from multiple publishers:")
        for a in retrieved[:12]:
            pub = a.get("publisher") or ""
            lines.append(f"- [{pub}] {a.get('title', '')}"
                         + (f" — {a['summary']}" if a.get("summary") else ""))
    return "\n".join(lines)


def _fill(instruction: str, params: dict) -> str:
    safe = defaultdict(lambda: "not specified")
    for k, v in (params or {}).items():
        if isinstance(v, list):
            if v and isinstance(v[0], dict):  # guests
                safe[k] = "\n".join(
                    f"- {g.get('Guest name','')} ({g.get('Designation','')}"
                    f"{', ' + g.get('Affiliation (optional)','') if g.get('Affiliation (optional)') else ''})"
                    f" — {g.get('Area of expertise','')}" for g in v)
            else:
                safe[k] = ", ".join(str(x) for x in v) or "not specified"
        else:
            safe[k] = v if (v is not None and str(v).strip()) else "not specified"
    try:
        return instruction.format_map(safe)
    except Exception:
        return instruction


# ── summary ────────────────────────────────────────────────────────────────

def summarize(topic: str, retrieved: list[dict]) -> str:
    if not retrieved:
        return (f"I couldn't pull fresh reporting on “{topic}” right now. "
                "You can still choose a format and I'll draft from what the desk has.")
    user = (f"In 3-4 tight sentences, summarise the current state of this story for a "
            f"TV producer. Topic: {topic}.\n\n{context_block(None, retrieved)}")
    out = _call(SYSTEM, user, max_tokens=600)
    if out:
        return out
    # heuristic: lead summary + corroboration
    lead = retrieved[0]
    pubs = ", ".join(sorted({a["publisher"] for a in retrieved[:6] if a.get("publisher")}))
    base = lead.get("summary") or lead.get("title") or ""
    return (f"{base}\n\nReported by {pubs or 'multiple outlets'}. "
            "Pick a format below and I'll build the script.")


# ── generation ─────────────────────────────────────────────────────────────

def generate(story: dict | None, format_id: str, params: dict,
             retrieved: list[dict]) -> dict:
    recipe = recipes.RECIPES.get(format_id)
    if not recipe:
        return {"ok": False, "error": f"Unknown format {format_id}"}
    instruction = _fill(recipe["instruction"], params)
    user = (f"{context_block(story, retrieved)}\n\n=== TASK ===\n{instruction}\n\n"
            f"{recipes.COMMON_RULES}")
    text = _call(SYSTEM, user)
    if text:
        return {"ok": True, "script": text, "model": "claude", "format": format_id}
    return {"ok": True, "script": _mock_script(recipe, story, params, retrieved),
            "model": "mock", "format": format_id}


def smart_action(action_id: str, content: str, story: dict | None,
                 retrieved: list[dict]) -> dict:
    action = recipes.SMART_ACTIONS.get(action_id)
    if not action:
        return {"ok": False, "error": "unknown action"}
    _, instruction = action
    user = (f"{context_block(story, retrieved)}\n\nCURRENT SCRIPT:\n{content}\n\n"
            f"=== TASK ===\n{instruction}\n\n{recipes.COMMON_RULES}")
    text = _call(SYSTEM, user)
    if text:
        return {"ok": True, "result": text, "model": "claude"}
    return {"ok": True, "model": "mock",
            "result": f"[MOCK — add an API key in Ops for AI output]\n\n"
                      f"{action[0]} would transform the script above. "
                      "Configure the Anthropic key to enable live rewrites."}


# ── intelligence panel ─────────────────────────────────────────────────────

_NUM_RE = re.compile(r"\b(?:Rs\.?|₹|\$)?\s?\d[\d,]*(?:\.\d+)?\s?(?:crore|lakh|million|"
                     r"billion|per cent|percent|%|dead|killed|injured|km|years?)?\b")


def intelligence(topic: str, story: dict | None, retrieved: list[dict]) -> dict:
    if has_key():
        user = (f"Extract a newsroom intelligence panel for this story as STRICT JSON "
                f"with keys: timeline (list of 'date — event' strings), people, "
                f"organizations, locations, related_stories, quick_facts, numbers, "
                f"suggested_graphics, suggested_visuals, key_quotes, "
                f"verification_checklist (list of check items). Only use the supplied "
                f"reporting. Topic: {topic}.\n\n{context_block(story, retrieved)}\n\n"
                f"Return ONLY the JSON object.")
        out = _call(SYSTEM, user, max_tokens=2000)
        if out:
            try:
                data = json.loads(out[out.find("{"): out.rfind("}") + 1])
                return {"source": "ai", **_normalise_intel(data)}
            except (json.JSONDecodeError, ValueError):
                pass
    return {"source": "heuristic", **_heuristic_intel(topic, story, retrieved)}


def _normalise_intel(d: dict) -> dict:
    keys = ["timeline", "people", "organizations", "locations", "related_stories",
            "quick_facts", "numbers", "suggested_graphics", "suggested_visuals",
            "key_quotes", "verification_checklist"]
    out = {}
    for k in keys:
        v = d.get(k, [])
        if isinstance(v, list):
            out[k] = [str(x) for x in v][:12]
        elif v:
            out[k] = [str(v)]
        else:
            out[k] = []
    return out


def _heuristic_intel(topic: str, story: dict | None, retrieved: list[dict]) -> dict:
    text = " ".join(a.get("title", "") + " " + a.get("summary", "") for a in retrieved)
    numbers = []
    for m in _NUM_RE.findall(text):
        s = m.strip()
        if s and any(c.isdigit() for c in s) and s not in numbers:
            numbers.append(s)
    related = [f"{a['publisher']}: {a['title']}" for a in retrieved[:8]
               if a.get("title")]
    quick = [a["summary"] for a in retrieved[:5] if a.get("summary")]
    locations = sorted({w for w in re.findall(r"\b[A-Z][a-z]+\b", text)
                        if w in _COMMON_PLACES})
    return {
        "timeline": [], "people": [], "organizations": [],
        "locations": locations[:8],
        "related_stories": related,
        "quick_facts": quick[:5],
        "numbers": numbers[:10],
        "suggested_graphics": [],
        "suggested_visuals": [],
        "key_quotes": [],
        "verification_checklist": [
            "Confirm with at least two independent outlets",
            "Verify any casualty / financial figures with an official source",
            "Check for an official statement before airing",
            "Distinguish confirmed facts from developing reports on air",
        ],
    }


_COMMON_PLACES = {
    "India", "Delhi", "Mumbai", "Ahmedabad", "Bengaluru", "Kolkata", "Chennai",
    "Pune", "Kashmir", "Ayodhya", "Maharashtra", "Gujarat", "Punjab", "China",
    "Pakistan", "Ukraine", "Russia", "Israel", "Iran", "Gaza", "Washington",
    "Indonesia", "Jakarta", "Tehran", "Kyiv", "London", "Bombay",
}


# ── grounded mock (no API key) ─────────────────────────────────────────────

def _mock_script(recipe: dict, story: dict | None, params: dict,
                 retrieved: list[dict]) -> str:
    title = (story or {}).get("title") or (retrieved[0]["title"] if retrieved else "This story")
    facts = [a.get("summary") or a.get("title") for a in retrieved[:3] if a]
    pubs = ", ".join(sorted({a["publisher"] for a in retrieved[:5] if a.get("publisher")}))
    head = _short_headlines(title)
    body = "\n".join(f"- {f}" for f in facts) or "- Details awaited."
    p = ", ".join(f"{k}: {v}" for k, v in (params or {}).items() if v) or "default settings"
    return (
        f"[MOCK DRAFT — template output. Add an Anthropic API key in "
        f"Ops → AI writer settings for live N-Pro scripts.]\n\n"
        f"FORMAT: {recipe['label']} ({p})\n\n"
        f"HEADLINES:\n" + "\n".join(f"  {h}" for h in head) + "\n\n"
        f"KEY REPORTING:\n{body}\n\n"
        f"WHY THIS MATTERS:\nThis is a developing story of clear viewer interest. "
        f"Reported by {pubs or 'multiple outlets'}; verify figures before air.\n"
    )


def _short_headlines(title: str) -> list[str]:
    words = title.split()
    base = " ".join(words[:5])[:40]
    return [base, (" ".join(words[:4]) + " Update")[:40], (base.split(":")[0])[:40]]
