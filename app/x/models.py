from dataclasses import dataclass, field


@dataclass
class Tweet:
    id: str
    handle: str
    display_name: str
    text: str
    created_at: str            # ISO timestamp
    stream_column: str = "C"   # A | B | C
    trust_score: int = 60
    terms: list = field(default_factory=list)  # extracted hashtags/entities
