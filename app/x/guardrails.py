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

NEWS_SIGNALS = {
    "breaking announcement": ["breaking", "just in", "big breaking"],
    "official document": ["notification", "circular", "gazette", "filing", "document"],
    "court order": ["court", "verdict", "judgment", "bail", "hearing", "plea"],
    "government notification": ["ministry", "govt", "government", "notifies", "cabinet"],
    "press conference": ["press conference", "presser", "briefing", "statement"],
    "exclusive reporting": ["exclusive", "sources confirm", "sources say", "accessed"],
    "eyewitness/ground report": ["visuals", "#watch", "ground report", "on the spot", "from the ground"],
    "public safety": ["alert", "advisory", "evacuation", "rescue", "warning", "casualties"],
    "update/developing": ["update:", "developing", "confirms", "announced", "cleared"],
}

TRUST_FLOOR = 50  # below: ignore unless independently verified (rule 6)


def evaluate(tweet: Tweet) -> dict:
    """Returns {'keep': bool, 'reason': str|None, 'news_signal': str|None}."""
    low = tweet.text.lower()

    for term in PERSONAL_NOISE:
        if term in low:
            return {"keep": False, "reason": f"personal/lifestyle content ('{term}')",
                    "news_signal": None}

    for term in ENGAGEMENT_BAIT:
        if term in low:
            return {"keep": False, "reason": f"engagement farming ('{term}')",
                    "news_signal": None}

    if tweet.trust_score < TRUST_FLOOR:
        return {"keep": False,
                "reason": f"trust score {tweet.trust_score} below floor {TRUST_FLOOR}",
                "news_signal": None}

    signal = None
    for name, terms in NEWS_SIGNALS.items():
        if any(t in low for t in terms):
            signal = name
            break

    # No news signal: keep in the column for context, but it carries no
    # alert weight and its terms still feed broker volume counts.
    return {"keep": True, "reason": None, "news_signal": signal}
