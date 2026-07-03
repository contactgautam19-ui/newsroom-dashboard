"""Evidence-based 100-point ranking framework (PRD section 3).

Every scorer returns (points, [evidence strings]) so each point on the board
is programmatically traceable — the UI shows this breakdown verbatim.

Weights: Breaking 15 | Emotion 15 | Political 12 | Celebrity 10 | Economy 12 |
Public Safety 15 | Visual 8 | Novelty 8 | Search Trend Momentum 15 (broker-fed).
"""

from app.news.enrich import find_matches
from app.news.models import EnrichedStory

EMOTION_TERMS = {
    "fear": ["threat", "panic", "fear", "terror", "scare", "warning", "danger"],
    "anger": ["outrage", "fury", "anger", "protest", "slam", "lash", "backlash"],
    "outrage": ["scam", "corruption", "fraud", "injustice", "assault", "shocking abuse"],
    "surprise": ["shock", "stun", "unexpected", "surprise", "sudden", "dramatic"],
    "awe": ["historic", "first ever", "record", "massive", "unprecedented", "biggest"],
}

POWER_FIGURES = [
    "pm ", "prime minister", "modi", "president", "chief minister", " cm ",
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

    # Breaking News Status — 15
    ev = _flag_evidence(story, "breaking")
    if ev:
        add("breaking", 15, [f"breaking marker: '{m}'" for m in ev])
    elif _flag_evidence(story, "developing"):
        add("breaking", 7, [f"developing marker: '{m}'" for m in _flag_evidence(story, "developing")])
    else:
        add("breaking", 0, [])

    # Audience Emotion — 15 (3 pts per distinct arousal trigger)
    emotion_hits = []
    for emotion, terms in EMOTION_TERMS.items():
        matches = find_matches(text, terms)
        if matches:
            emotion_hits.append(f"{emotion}: '{matches[0]}'")
    add("emotion", 3 * len(emotion_hits), emotion_hits)

    # Political Importance — 12 (full if a power center is named, else partial)
    power = find_matches(text, POWER_FIGURES)
    if power:
        add("political", 12, [f"power center: '{m}'" for m in power])
    elif story.flags.get("political"):
        add("political", 6, [f"political term: '{m}'" for m in _flag_evidence(story, "political")])
    else:
        add("political", 0, [])

    # Celebrity Profile — 10
    ev = _flag_evidence(story, "celebrity")
    add("celebrity", 10 if ev else 0, [f"celebrity marker: '{m}'" for m in ev])

    # Money/Economic Impact — 12 (4 pts per distinct economic term)
    ev = _flag_evidence(story, "economy")
    add("economy", 4 * len(ev), [f"economic impact: '{m}'" for m in ev])

    # Public Safety / Utility — 15 (disaster/violence/health hazards)
    safety_ev = []
    for theme, weight_note in (("disaster", "active hazard"), ("violence", "public threat"),
                               ("health", "health hazard")):
        for m in _flag_evidence(story, theme):
            safety_ev.append(f"{weight_note}: '{m}'")
    add("safety", 5 * len({e.split(":")[0] for e in safety_ev}) + (2 if len(safety_ev) > 2 else 0),
        safety_ev)

    # Visual Potential — 8 (2 pts per rich-media indicator present)
    media_ev = []
    for indicator, matches in story.media.get("_evidence", {}).items():
        media_ev.append(f"{indicator.replace('_', ' ')}: '{matches[0]}'")
    if story.media.get("image_count"):
        media_ev.append(f"images detected: {story.media['image_count']}")
    add("visual", 2 * len(media_ev), media_ev)

    # Unexpectedness / Novelty — 8 (4 pts per novelty marker)
    matches = find_matches(text, NOVELTY_TERMS)
    add("novelty", 4 * len(matches), [f"novelty marker: '{m}'" for m in matches])

    # Search Trend Momentum — 15: discovery via a live trending keyword earns
    # a 5-pt base (the story was *found* because the term is moving right now);
    # the Conversation Broker layers its dynamic +1..+10 offset on top.
    if story.raw.discovered_via:
        add("trend", 5, [
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
