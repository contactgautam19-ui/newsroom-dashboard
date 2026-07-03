"""Simulated X feed.

Stands in for the real scraping layers so the full dashboard loop (columns,
guardrails, broker velocity, score boosts) can be exercised without touching X.
Generates tweets from the real 242-handle database:

- newsy tweets derived from *live* headlines currently on the news panel, so
  Conversation Broker term-matching genuinely fires against real stories;
- generic beat chatter that passes guardrails but matches no story;
- personal/engagement noise the guardrails are expected to discard.

A scripted spike scenario (`trigger_spike`) floods a headline's terms to
demonstrate the Viral Acceleration -> Trend Momentum boost feedback loop.
"""

import hashlib
import random
import re
import time
from datetime import datetime, timezone

from app import db
from app.x.models import Tweet
from app.x.provider import XProvider

STOPWORDS = set(
    "the a an and or of in on to for with at by from as is are was be after amid"
    " over under his her their its new says say said live update updates breaking"
    " india indian latest news today top big".split()
)

NEWSY_TEMPLATES = [
    "BREAKING: {headline} — details awaited. {tag}",
    "Sources confirm: {headline}. More on this shortly. {tag}",
    "#WATCH | Visuals from the ground. {headline} {tag}",
    "Official statement expected soon on this. {headline} {tag}",
    "Our bureau is tracking: {headline} {tag}",
    "UPDATE: {headline}. Officials have been notified. {tag}",
    "Press conference underway. {headline} {tag}",
    "Court filing accessed: {headline} {tag}",
]

CHATTER_TEMPLATES = [
    "Cabinet reshuffle buzz grows in Delhi corridors. #Politics",
    "Sensex swings 400 points in afternoon trade. #Markets",
    "Monsoon advances; IMD issues advisory for coastal districts. #Weather",
    "Supreme Court to hear the plea tomorrow. #Courts",
    "New defence procurement cleared by ministry panel. #Defence",
    "State govt notifies revised transport rules. #Policy",
    "ISRO confirms next launch window. #Space",
    "Fuel prices unchanged for the 12th straight day. #Economy",
]

NOISE_TEMPLATES = [
    "Good morning Twitter! Have a great day ahead 🌞",
    "Enjoying a well-deserved coffee break ☕",
    "Happy birthday to my dear colleague! 🎂",
    "What do you all think? Comment below!",
    "Blessed Sunday with the family 🙏",
    "Look at this beautiful sunset from my balcony!",
    "Thank you for 50k followers! Grateful 🙏",
    "My cat has opinions about my home office setup 😹",
]

_hash_re = re.compile(r"#(\w+)")


def _headline_tag(title: str) -> str:
    words = [w for w in re.sub(r"[^A-Za-z ]", "", title).split()
             if w.lower() not in STOPWORDS and len(w) > 3]
    return "#" + "".join(w.capitalize() for w in words[:3]) if words else "#News"


def extract_terms(text: str, headline: str = "") -> list[str]:
    """Hashtags + significant headline keywords, lowercased."""
    terms = {t.lower() for t in _hash_re.findall(text)}
    source = headline or text
    for w in re.sub(r"[^A-Za-z ]", "", source).split():
        if w.lower() not in STOPWORDS and len(w) > 4:
            terms.add(w.lower())
    return sorted(terms)[:8]


class SimulatedProvider(XProvider):
    name = "simulated"

    def __init__(self, tweets_per_min: int = 12):
        self.tweets_per_min = tweets_per_min
        self._last_fetch = time.monotonic()
        self._spike: dict | None = None  # {term, headline, until, rate_mult}

    def trigger_spike(self, story: dict, duration_s: int = 300, rate_mult: int = 12) -> dict:
        tag = _headline_tag(story["title"])
        self._spike = {
            "story_id": story["id"],
            "headline": story["title"],
            "tag": tag,
            "until": time.monotonic() + duration_s,
            "rate_mult": rate_mult,
        }
        return {"tag": tag, "duration_s": duration_s}

    def _active_headlines(self) -> list[dict]:
        with db.connect() as con:
            rows = con.execute(
                "SELECT id, title FROM stories WHERE active=1 ORDER BY score DESC LIMIT 8"
            ).fetchall()
        return [dict(r) for r in rows]

    def _make_tweet(self, handle: dict, text: str, headline: str = "") -> Tweet:
        now = datetime.now(timezone.utc)
        raw_id = f"{handle['handle']}|{text}|{now.timestamp()}|{random.random()}"
        return Tweet(
            id=hashlib.sha1(raw_id.encode()).hexdigest()[:16],
            handle=handle["handle"],
            display_name=handle["name"],
            text=text,
            created_at=now.isoformat(),
            stream_column=handle["stream_column"],
            trust_score=handle["trust_score"],
            terms=extract_terms(text, headline),
        )

    def fetch_new_tweets(self, handles: list[dict]) -> list[Tweet]:
        now = time.monotonic()
        elapsed = min(now - self._last_fetch, 120)
        self._last_fetch = now

        count = max(1, round(self.tweets_per_min * elapsed / 60 * random.uniform(0.6, 1.4)))
        headlines = self._active_headlines()
        tweets = []

        spike = self._spike
        if spike and now > spike["until"]:
            self._spike = spike = None

        for _ in range(count):
            handle = random.choice(handles)
            roll = random.random()
            if roll < 0.40 and headlines:
                story = random.choice(headlines)
                template = random.choice(NEWSY_TEMPLATES)
                text = template.format(headline=story["title"],
                                       tag=_headline_tag(story["title"]))
                tweets.append(self._make_tweet(handle, text, story["title"]))
            elif roll < 0.75:
                tweets.append(self._make_tweet(handle, random.choice(CHATTER_TEMPLATES)))
            else:
                tweets.append(self._make_tweet(handle, random.choice(NOISE_TEMPLATES)))

        if spike:
            # sized so a full 5-min spike pushes the term past the 5,000
            # posts/hour High-Demand threshold by its final ticks
            spike_count = max(4, round(self.tweets_per_min * elapsed / 60
                                       * spike["rate_mult"] * 0.7))
            for _ in range(spike_count):
                handle = random.choice(handles)
                template = random.choice(NEWSY_TEMPLATES)
                text = template.format(headline=spike["headline"], tag=spike["tag"])
                tweets.append(self._make_tweet(handle, text, spike["headline"]))

        return tweets
