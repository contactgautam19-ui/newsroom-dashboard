from dataclasses import dataclass, field


THEMATIC_FLAGS = [
    "political",
    "celebrity",
    "violence",
    "economy",
    "health",
    "disaster",
    "breaking",
    "developing",
]

MEDIA_INDICATORS = [
    "live_feed",
    "drone_footage",
    "police_action",
    "crowds",
    "explosions",
    "floods",
    "fires",
]


@dataclass
class RawArticle:
    title: str
    url: str
    publisher: str
    published_at: str  # ISO timestamp
    summary: str = ""
    category_hint: str = ""             # from the source matrix Focus column
    source_rank: int = 99               # matrix rank (1 = highest priority)
    source_country: str = "INTL"        # "IN" for Indian outlets, else "INTL"
    corroborators: list = field(default_factory=list)  # outlets in this cluster
    discovered_via: str = ""            # trending keyword that surfaced this story


@dataclass
class EnrichedStory:
    raw: RawArticle
    location: str = "India"
    category: str = "National"
    flags: dict = field(default_factory=dict)      # thematic booleans
    media: dict = field(default_factory=dict)      # rich-media indicators + image count
    sources: list = field(default_factory=list)    # corroborating publishers
