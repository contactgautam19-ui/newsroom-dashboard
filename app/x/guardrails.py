"""X monitoring guardrails (X Monitoring Guardrails.docx).

Implemented rules for the MVP feed:
  1  Editorial relevance filter (drop personal-life / lifestyle posts)
  3  Ignore engagement farming
  5  Require a news signal to surface as alert-worthy
  6  Credibility threshold via per-handle trust scores
 11  Duplicate story detection (identical-text clustering via pipeline dedup)

Discarded tweets are still stored (discarded=1 + reason) to keep the audit
trail rule 19/"Audit trail" asks for.
"""

import re

from app.x.models import Tweet

PERSONAL_NOISE = [
    "good morning", "happy monday", "happy birthday", "birthday", "vacation",
    "coffee", "sunset", "blessed", "family", "my cat", "my dog", "selfie",
    "food", "dinner", "lunch", "gym", "workout", "weekend vibes",
]

ENGAGEMENT_BAIT = [
    "what do you think", "comment below", "like and share", "follow me",
    "thank you for", "followers", "retweet if", "poll:", "tag someone",
]

# The ten news signals from the guardrails document (rule 5), exactly.
# Word-boundary matched so 'documentation' never reads as 'document' and
# a ministry PR post never counts as a government notification.
NEWS_SIGNALS = {
    "breaking announcement": ["breaking", "just in", "big breaking", "flash"],
    "official document": ["gazette", "circular", "official document",
                          "memorandum", "order copy", "white paper"],
    "court order": ["court", "verdict", "judgment", "judgement", "bail",
                    "remanded", "custody", "tribunal", "acquitted", "convicted"],
    "government notification": ["notifies", "notified", "notification",
                                "ordinance", "gazetted", "cabinet approves",
                                "cabinet clears"],
    "company filing": ["filing", "sebi", "regulatory filing", "exchange filing",
                       "ipo", "quarterly results", "agm"],
    "press conference": ["press conference", "presser", "media briefing",
                         "press briefing", "addresses media"],
    "exclusive reporting": ["exclusive", "scoop", "sources confirm",
                            "sources say", "accessed"],
    "investigation": ["investigation", "probe", "raid", "raids", "arrested",
                      "arrest", "seized", "chargesheet", "fir", "detained"],
    "eyewitness report": ["ground report", "on the spot", "visuals from",
                          "eyewitness", "from the ground", "ground zero"],
    "public safety": ["red alert", "orange alert", "advisory", "evacuation",
                      "evacuated", "rescue", "warning issued", "casualties",
                      "death toll", "alert issued"],
}

_term_cache: dict[str, "re.Pattern"] = {}


def _matches(text_lower: str, term: str) -> bool:
    pat = _term_cache.get(term)
    if pat is None:
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")
        _term_cache[term] = pat
    return bool(pat.search(text_lower))


def detect_signal(text: str) -> str | None:
    """Which guardrail news signal (if any) this text carries."""
    low = text.lower()
    for name, terms in NEWS_SIGNALS.items():
        if any(_matches(low, t) for t in terms):
            return name
    return None


TRUST_FLOOR = 50  # below: ignore unless independently verified (rule 6)


def evaluate(tweet: Tweet) -> dict:
    """Returns {'keep': bool, 'reason': str|None, 'news_signal': str|None}."""
    low = tweet.text.lower()

    for term in PERSONAL_NOISE:
        if _matches(low, term):
            return {"keep": False, "reason": f"personal/lifestyle content ('{term}')",
                    "news_signal": None}

    for term in ENGAGEMENT_BAIT:
        if _matches(low, term):
            return {"keep": False, "reason": f"engagement farming ('{term}')",
                    "news_signal": None}

    if tweet.trust_score < TRUST_FLOOR:
        return {"keep": False,
                "reason": f"trust score {tweet.trust_score} below floor {TRUST_FLOOR}",
                "news_signal": None}

    # No news signal: keep in the column for context, but it carries no
    # alert weight and its terms still feed broker volume counts.
    return {"keep": True, "reason": None, "news_signal": detect_signal(tweet.text)}
