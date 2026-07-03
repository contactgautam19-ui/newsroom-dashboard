"""Thematic flag + rich-media detection over headline/summary text.

Everything is dictionary-driven so each detection is traceable to the exact
matched term (surfaced later as scoring evidence).
"""

import re
from functools import lru_cache

from app.news.models import EnrichedStory, RawArticle

THEME_TERMS = {
    "political": [
        "pm", "prime minister", "modi", "parliament", "lok sabha", "rajya sabha",
        "chief minister", "cm", "election", "bjp", "congress", "aap", "cabinet",
        "minister", "president", "governor", "supreme court", "high court",
        "assembly", "mla", "mp", "opposition", "coalition", "ordinance",
    ],
    "celebrity": [
        "bollywood", "actor", "actress", "cricketer", "kohli", "rohit sharma",
        "shah rukh", "salman khan", "deepika", "ranbir", "superstar", "singer",
        "film star", "celebrity", "influencer",
    ],
    "violence": [
        "attack", "shooting", "stabbing", "murder", "killed", "clash", "riot",
        "violence", "assault", "terror", "blast", "encounter", "firing", "mob",
    ],
    "economy": [
        "rupee", "sensex", "nifty", "rbi", "inflation", "gdp", "budget", "gst",
        "tax", "market", "stocks", "economy", "jobs", "layoff", "ipo", "crore",
        "lakh crore", "fuel price", "petrol", "repo rate",
    ],
    "health": [
        "hospital", "virus", "outbreak", "vaccine", "disease", "health",
        "epidemic", "pandemic", "infection", "cancer", "aiims", "icmr", "dengue",
    ],
    "disaster": [
        "earthquake", "flood", "cyclone", "landslide", "collapse", "derail",
        "crash", "fire", "blaze", "explosion", "rescue", "evacuate", "evacuation",
        "drown", "drowns", "drowned", "cloudburst", "heatwave", "storm",
    ],
    "breaking": [
        "breaking", "just in", "live:", "live updates", "big breaking", "alert",
    ],
    "developing": [
        "developing", "updates", "latest", "ongoing", "underway", "continues",
    ],
}

MEDIA_TERMS = {
    "live_feed": ["live", "live updates", "watch live", "live visuals", "live coverage"],
    "drone_footage": ["drone", "aerial"],
    "police_action": ["police", "lathi", "crackdown", "raid", "arrest"],
    "crowds": ["crowd", "crowds", "protest", "rally", "gathering", "stampede", "march"],
    "explosions": ["blast", "explosion", "explode"],
    "floods": ["flood", "floods", "waterlogging", "submerged", "inundated"],
    "fires": ["fire", "blaze", "gutted", "inferno"],
}

CATEGORY_RULES = [
    ("International", ["us ", "china", "pakistan", "russia", "ukraine", "israel",
                       "gaza", "trump", "white house", "un ", "nato", "bangladesh",
                       "sri lanka", "nepal"]),
    ("Business", ["sensex", "nifty", "rbi", "ipo", "startup", "market", "rupee",
                  "earnings", "quarterly"]),
    ("Sports", ["cricket", "ipl", "world cup", "olympi", "football", "hockey",
                "badminton", "tennis", "medal"]),
    ("Entertainment", ["bollywood", "film", "movie", "box office", "trailer",
                       "actor", "actress", "ott "]),
    ("Technology", ["ai ", "tech", "smartphone", "isro", "satellite", "cyber",
                    "startup", "app "]),
]

# Indian states/major cities for a rough location tag
LOCATIONS = [
    "delhi", "mumbai", "kolkata", "chennai", "bengaluru", "bangalore", "hyderabad",
    "pune", "ahmedabad", "jaipur", "lucknow", "patna", "bhopal", "chandigarh",
    "uttar pradesh", "maharashtra", "bihar", "west bengal", "tamil nadu",
    "karnataka", "kerala", "gujarat", "rajasthan", "punjab", "haryana", "assam",
    "odisha", "telangana", "andhra pradesh", "madhya pradesh", "jharkhand",
    "uttarakhand", "himachal", "goa", "kashmir", "jammu", "manipur", "noida",
    "gurugram", "varanasi", "srinagar",
]


@lru_cache(maxsize=4096)
def _term_re(term: str) -> re.Pattern:
    # word-boundary match so 'factory' never matches 'actor' and '8pm'
    # never matches 'pm'; multi-word terms match as phrases
    return re.compile(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])")


def find_matches(text: str, terms) -> list[str]:
    low = text.lower()
    return [t for t in terms if _term_re(t).search(low)]


def enrich(raw: RawArticle) -> EnrichedStory:
    text = f"{raw.title} {raw.summary}"

    flags = {}
    flag_evidence = {}
    for theme, terms in THEME_TERMS.items():
        matches = find_matches(text, terms)
        flags[theme] = bool(matches)
        if matches:
            flag_evidence[theme] = matches

    media = {}
    media_evidence = {}
    for indicator, terms in MEDIA_TERMS.items():
        matches = find_matches(text, terms)
        media[indicator] = bool(matches)
        if matches:
            media_evidence[indicator] = matches
    # Google News RSS gives no image payload; count inline media references
    media["image_count"] = len(re.findall(r"<img\b", raw.summary or ""))

    category = raw.category_hint or "National"
    for cat, terms in CATEGORY_RULES:
        if find_matches(text, terms):
            category = cat
            break

    location = "India"
    loc_matches = find_matches(text, LOCATIONS)
    if loc_matches:
        location = loc_matches[0].title()

    story = EnrichedStory(
        raw=raw, location=location, category=category, flags=flags, media=media,
        sources=[raw.publisher] if raw.publisher else [],
    )
    # stash evidence for the scorer
    story.flags["_evidence"] = flag_evidence
    story.media["_evidence"] = media_evidence
    return story
