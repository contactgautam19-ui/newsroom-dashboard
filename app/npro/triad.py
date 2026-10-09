"""The triad rule: context is only context if it is about the same WHO, the
same WHERE, and its WHEN is known.

A script borrows background from other reporting. The commonest way that goes
wrong is a lookalike: a similar accident in another district, a namesake, last
year's flood passed off as this year's. So before a report may inform a
script it has to match the story on entity and on geography, and it has to
carry a date, which decides whether it is the same development or dated
background. Everything here is mechanical; no model decides what is admitted.
"""

import math
import re
import unicodedata
from datetime import datetime, timedelta, timezone

# what a triad looks like (also the JSON schema the intake model must fill)
SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string"},
        "already_english": {"type": "boolean"},
        "english": {"type": "string"},
        "uncertain": {"type": "array", "items": {
            "type": "object",
            "properties": {"original": {"type": "string"}, "reading": {"type": "string"}},
            "required": ["original", "reading"], "additionalProperties": False}},
        "headline": {"type": "string"},
        "entities": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "aliases": {"type": "array", "items": {"type": "string"}},
                           "kind": {"type": "string",
                                    "enum": ["person", "organisation", "other"]},
                           "role": {"type": "string", "enum": ["subject", "voice"]}},
            "required": ["name", "aliases", "kind", "role"], "additionalProperties": False}},
        "places": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"},
                           "aliases": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "aliases"], "additionalProperties": False}},
        "when": {"type": "object",
                 "properties": {"said": {"type": "string"}, "date": {"type": "string"}},
                 "required": ["said", "date"], "additionalProperties": False},
        "event_terms": {"type": "array", "items": {"type": "string"}},
        "queries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["language", "already_english", "english", "uncertain", "headline",
                 "entities", "places", "when", "event_terms", "queries"],
    "additionalProperties": False,
}

# the same, without the translation: for a story that is already a headline
PIN_SCHEMA = {
    "type": "object",
    "properties": {k: SCHEMA["properties"][k]
                   for k in ("entities", "places", "when", "event_terms", "queries")},
    "required": ["entities", "places", "when", "event_terms", "queries"],
    "additionalProperties": False,
}

SAME_WINDOW_DAYS = 2      # a report this close to the event is the same development

_SCRIPTS = [
    ("Hindi or Marathi", 0x0900, 0x097F), ("Bengali or Assamese", 0x0980, 0x09FF),
    ("Punjabi", 0x0A00, 0x0A7F), ("Gujarati", 0x0A80, 0x0AFF),
    ("Odia", 0x0B00, 0x0B7F), ("Tamil", 0x0B80, 0x0BFF),
    ("Telugu", 0x0C00, 0x0C7F), ("Kannada", 0x0C80, 0x0CFF),
    ("Malayalam", 0x0D00, 0x0D7F), ("Urdu", 0x0600, 0x06FF),
]
# native digits of each script -> 0-9
_DIGIT_BASES = [0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6,
                0x0D66, 0x06F0, 0x0660]


def script_of(text: str) -> str | None:
    """The writing system of a note, when it is not Latin."""
    counts: dict[str, int] = {}
    for ch in text or "":
        cp = ord(ch)
        for name, lo, hi in _SCRIPTS:
            if lo <= cp <= hi:
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def ascii_digits(text: str) -> str:
    out = []
    for ch in text or "":
        cp = ord(ch)
        for base in _DIGIT_BASES:
            if base <= cp <= base + 9:
                ch = str(cp - base)
                break
        out.append(ch)
    return "".join(out)


def figures_lost(original: str, english: str) -> list[str]:
    """Figures written in digits in the note that the translation does not
    carry. A translation that drops or changes a number is not faithful."""
    def nums(t):
        return {m.replace(",", "").rstrip(".") for m in
                re.findall(r"\d[\d,]*(?:\.\d+)?", ascii_digits(t))}
    have = nums(english)

    def carried(n: str) -> bool:
        # "40 हजार" is rightly written 40,000; lakh and crore expand the same way
        if n in have:
            return True
        try:
            v = float(n)
        except ValueError:
            return False
        for h in have:
            try:
                ratio = float(h) / v if v else 0
            except ValueError:
                continue
            if ratio in (100.0, 1000.0, 100000.0, 10000000.0):
                return True
        return False
    return sorted((n for n in nums(original) if not carried(n)), key=lambda x: (len(x), x))


# ── matching ───────────────────────────────────────────────────────────────

_TITLES = set("""shri smt sri mr mrs ms dr prof justice chief minister prime
deputy union state former ex the of and cm pm mla mp mlc ips ias sp dsp dcp acp
dgp si inspector commissioner collector officer president secretary general
district police station city town village taluk tehsil block ward""".split())


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _close(a: str, b: str) -> bool:
    """Spelling variants of the same name: Choudhary/Chaudhary, Thiruvananthapuram
    /Tiruvanantapuram. Short words must match exactly."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 2 or a[0] != b[0]:
        return False
    # cheap edit-distance bound: at most one edit per five letters
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1] <= max(1, len(a) // 5)


class _Text:
    def __init__(self, text: str):
        self.folded = " " + _fold(text) + " "
        self.tokens = set(self.folded.split())

    def has(self, name: str) -> bool:
        f = _fold(name)
        if not f:
            return False
        if f" {f} " in self.folded:
            return True
        words = [w for w in f.split() if w not in _TITLES and (len(w) > 2 or w.isdigit())]
        if not words:
            return False
        if len(words) == 1 and len(f.split()) > 1:
            # "Patna Police" is not matched by the word "Patna" alone: a name
            # that is one distinctive word plus a title needs the whole phrase
            return False
        hits = sum(1 for w in words
                   if w in self.tokens or any(_close(w, t) for t in self.tokens
                                              if t and t[0] == w[0]))
        # a full name needs most of its distinctive words; a single word, itself
        return hits >= max(1, math.ceil(len(words) * 0.6)) if len(words) > 1 else hits == 1


def _names(rows) -> list[list[str]]:
    out = []
    for r in rows or []:
        if isinstance(r, dict) and r.get("name"):
            out.append([r["name"]] + [a for a in (r.get("aliases") or []) if a])
    return out


def event_day(triad: dict | None):
    """The day the story happened: the note's own date when it gives one."""
    today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()
    try:
        day = datetime.fromisoformat((triad or {}).get("when", {}).get("date", "")[:10]).date()
    except (ValueError, TypeError, AttributeError):
        return today
    return min(day, today)        # a hearing "fixed for the 16th" was still reported today


def judge(title: str, text: str, published_at: str, triad: dict) -> dict:
    """Does one report belong in this story's context?

    Returns {ok, role, why, entity, place, date}. ``role`` is 'same' (the same
    development) or 'background' (an earlier, dated report on the same who and
    where). ``why`` says what failed when ok is False."""
    body = _Text((title or "") + "\n" + (text or ""))
    # Only the people and bodies the story is ABOUT can identify it. The
    # Collector, the police spokesman and the fire service speak on every
    # story in their patch; matching on them proves nothing.
    entities = _names([e for e in (triad.get("entities") or [])
                       if isinstance(e, dict) and e.get("role", "subject") != "voice"])
    # "India" places nothing: a report on a Reserve Bank decision need not
    # say which country it is in. Only places narrower than the country count.
    places = [n for n in _names(triad.get("places"))
              if _fold(n[0]) not in ("india", "bharat", "the country", "nationwide")]
    terms = [t for t in (triad.get("event_terms") or []) if t]

    who = next((names[0] for names in entities if any(body.has(n) for n in names)), "")
    where = next((names[0] for names in places if any(body.has(n) for n in names)), "")
    place_ok = bool(where) or not places
    hit = [t for t in terms if body.has(t)]
    named = bool(who)
    # A named person or body is not enough on its own: the same Collector is
    # quoted on a dozen unrelated stories from the same city. The report must
    # also be about this kind of event.
    entity_ok = named and (bool(hit) or not terms)
    by_event = False
    need = min(len(terms), max(2, math.ceil(len(terms) / 2)))
    if not named and terms and (places or not entities) and len(hit) >= need:
        # The event itself can stand as the "who": a report on the same crash
        # need not name the same police officer. That holds only for the same
        # few days; an older fire in the same town is a different fire.
        entity_ok, by_event, who = True, True, ", ".join(hit[:2])

    day = None
    try:
        day = (datetime.fromisoformat(published_at) + timedelta(hours=5, minutes=30)).date()
    except (ValueError, TypeError):
        pass

    if not entity_ok:
        why = "a different event" if (named or not entities) else "different people"
    elif not place_ok:
        why = "a different place"
    elif day is None:
        why = "no date"
    else:
        why = ""
    role = ""
    if not why:
        gap = (event_day(triad) - day).days
        role = "same" if -1 <= gap <= SAME_WINDOW_DAYS else "background"
        if by_event and role == "background":
            why, role = "a different time", ""
    return {"ok": not why, "role": role, "why": why, "entity": who, "place": where,
            "date": day.strftime("%d %b %Y").lstrip("0") if day else ""}


def summary(triad: dict | None) -> dict:
    """Who / where / when, as short strings for the console."""
    t = triad or {}
    day = event_day(t)
    said = (t.get("when") or {}).get("said") or ""
    subjects = [e for e in (t.get("entities") or [])
                if isinstance(e, dict) and e.get("role", "subject") != "voice"]
    return {
        "who": [n[0] for n in _names(subjects or t.get("entities"))][:5],
        "where": [n[0] for n in _names(t.get("places"))][:4],
        "when": day.strftime("%d %b %Y").lstrip("0") + (f" ({said})" if said else ""),
    }


def is_usable(triad: dict | None) -> bool:
    t = triad or {}
    return bool(_names(t.get("entities")) or t.get("event_terms"))
