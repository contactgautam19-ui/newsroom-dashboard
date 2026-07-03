"""Evidence-based 100-point ranking framework (PRD section 3).

Every scorer returns (points, [evidence strings]) so each point on the board
is programmatically traceable — the UI shows this breakdown verbatim.

Weights: Breaking 15 | Emotion 15 | Political 12 | Celebrity 10 | Economy 12 |
Public Safety 15 | Visual 8 | Novelty 8 | Search Trend Momentum 15 (broker-fed).
"""

from datetime import datetime, timezone

from app.news.enrich import find_matches
from app.news.models import EnrichedStory


def _age_minutes(published_iso: str) -> float | None:
    try:
        dt = datetime.fromisoformat(published_iso)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 60
    except (ValueError, TypeError):
        return None

EMOTION_TERMS = {
    "fear": ["threat", "panic", "fear", "terror", "scare", "warning", "danger"],
    "anger": ["outrage", "fury", "anger", "protest", "slam", "lash", "backlash"],
    "outrage": ["scam", "corruption", "fraud", "injustice", "assault", "shocking abuse"],
    "surprise": ["shock", "stun", "unexpected", "surprise", "sudden", "dramatic"],
    "awe": ["historic", "first ever", "record", "massive", "unprecedented", "biggest"],
}

POWER_FIGURES = [
    "pm", "prime minister", "modi", "president", "chief minister", "cm",
    "supreme court", "chief justice", "army", "air force", "navy", "cabinet",
    "home minister", "finance minister", "defence minister", "governor", "rbi governor",
]

NOVELTY_TERMS = [
    "first time", "first ever", "never before", "rare", "unprecedented",
    "historic", "record", "unusual", "bizarre", "shocking", "black swan",
]

VARIABLES = [
    ("breaking", "Breaking News Status", 15),
    ("emotion", "Audience Emotion", 15),
    ("political", "Political Importance", 12),
    ("celebrity", "Celebrity Profile", 10),
    ("economy", "Money/Economic Impact", 12),
    ("safety", "Public Safety / Utility", 15),
    ("visual", "Visual Potential", 8),
    ("novelty", "Unexpectedness / Novelty", 8),
    ("trend", "Search Trend Momentum", 15),
]


def _flag_evidence(story: EnrichedStory, theme: str) -> list[str]:
    return story.flags.get("_evidence", {}).get(theme, [])


def score_story(story: EnrichedStory) -> dict:
    """Returns {'total', 'breakdown': [{key, label, max, points, evidence}]}."""
    text = f"{story.raw.title} {story.raw.summary}"
    breakdown = []

    def add(key: str, points: int, evidence: list[str]) -> None:
        label, max_pts = next((v[1], v[2]) for v in VARIABLES if v[0] == key)
        breakdown.append({
            "variable": key, "label": label, "max_points": max_pts,
            "points": max(0, min(points, max_pts)), "evidence": evidence,
        })

    # Breaking News Status — 15 ("immediate temporal priority": explicit
    # markers score full; otherwise very fresh publication earns partial points)
    ev = _flag_evidence(story, "breaking")
    age_min = _age_minutes(story.raw.published_at)
    if ev:
        add("breaking", 15, [f"breaking marker: '{m}'" for m in ev])
    elif _flag_evidence(story, "developing"):
        add("breaking", 8, [f"developing marker: '{m}'" for m in _flag_evidence(story, "developing")])
    elif age_min is not None and age_min <= 60:
        add("breaking", 10, [f"published {age_min:.0f} min ago — immediate temporal priority"])
    elif age_min is not None and age_min <= 180:
        add("breaking", 6, [f"published {age_min/60:.1f} h ago — recent development"])
    else:
        add("breaking", 0, [])

    # Audience Emotion — 15 (a firing trigger carries meaningful weight:
    # 8 for one distinct arousal trigger, 12 for two, 15 for three or more)
    emotion_hits = []
    for emotion, terms in EMOTION_TERMS.items():
        matches = find_matches(text, terms)
        if matches:
            emotion_hits.append(f"{emotion}: '{matches[0]}'")
    add("emotion", (0, 8, 12, 15)[min(3, len(emotion_hits))], emotion_hits)

    # Political Importance — 12 (full if a power center is named, else partial)
    power = find_matches(text, POWER_FIGURES)
    if power:
        add("political", 12, [f"power center: '{m}'" for m in power])
    elif story.flags.get("political"):
        add("political", 8, [f"political term: '{m}'" for m in _flag_evidence(story, "political")])
    else:
        add("political", 0, [])

    # Celebrity Profile — 10
    ev = _flag_evidence(story, "celebrity")
    add("celebrity", 10 if ev else 0, [f"celebrity marker: '{m}'" for m in ev])

    # Money/Economic Impact — 12 (8 for one economic vector, 12 for several)
    ev = _flag_evidence(story, "economy")
    add("economy", (0, 8, 12)[min(2, len(ev))],
        [f"economic impact: '{m}'" for m in ev])

    # Public Safety / Utility — 15 (10 for one hazard class, 15 for stacked)
    safety_ev = []
    for theme, weight_note in (("disaster", "active hazard"), ("violence", "public threat"),
                               ("health", "health hazard")):
        for m in _flag_evidence(story, theme):
            safety_ev.append(f"{weight_note}: '{m}'")
    classes = len({e.split(":")[0] for e in safety_ev})
    add("safety", (0, 10, 15, 15)[min(3, classes)], safety_ev)

    # Visual Potential — 8 (4 pts per rich-media indicator present)
    media_ev = []
    for indicator, matches in story.media.get("_evidence", {}).items():
        media_ev.append(f"{indicator.replace('_', ' ')}: '{matches[0]}'")
    if story.media.get("image_count"):
        media_ev.append(f"images detected: {story.media['image_count']}")
    add("visual", 4 * len(media_ev), media_ev)

    # Unexpectedness / Novelty — 8 (5 for one marker, 8 for several)
    matches = find_matches(text, NOVELTY_TERMS)
    add("novelty", (0, 5, 8)[min(2, len(matches))],
        [f"novelty marker: '{m}'" for m in matches])

    # Search Trend Momentum — 15: discovery via a live trending keyword earns
    # a 7-pt base (the story was *found* because the term is moving right now);
    # the Conversation Broker layers its dynamic offset on top up to the cap.
    if story.raw.discovered_via:
        add("trend", 7, [
            f"surfaced via trending keyword '{story.raw.discovered_via}' "
            "(past-hour Google News search)",
        ])
    else:
        add("trend", 0, [])

    total = sum(b["points"] for b in breakdown)
    return {"total": total, "breakdown": breakdown}


def compute_confidence(story: EnrichedStory, score: dict) -> int:
    """Confidence 0-100: source corroboration + evidence density.

    Two credible sources is the PRD's bar for major breaking stories, so
    corroboration dominates the calculation.
    """
    source_count = len(set(story.sources))
    source_component = min(source_count, 3) * 20            # up to 60
    evidenced_vars = sum(1 for b in score["breakdown"] if b["evidence"])
    evidence_component = min(evidenced_vars, 5) * 8          # up to 40
    return min(100, source_component + evidence_component)
