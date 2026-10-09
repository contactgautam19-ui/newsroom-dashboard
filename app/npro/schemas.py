"""Request shapes for the N-Pro endpoints.

Everything the console sends is checked here before it can reach a search, a
prompt or the database: lengths are capped, enumerated choices must be real
choices, links must be ordinary web links, and unknown fields are dropped.
A malformed request is refused with a 422 rather than half-processed.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.npro import recipes

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(v: Any, limit: int) -> str:
    """A plain single string: control characters out, whitespace tidy, capped."""
    if v is None:
        return ""
    s = _CTRL.sub(" ", str(v))
    return s.strip()[:limit]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Report(_Base):
    """One retrieved report, as the console holds it between calls."""
    title: str = Field(default="", max_length=400)
    url: str = Field(default="", max_length=2000)
    publisher: str = Field(default="", max_length=160)
    published_at: str = Field(default="", max_length=40)
    summary: str = Field(default="", max_length=800)

    @field_validator("title", "publisher", "summary", mode="before")
    @classmethod
    def _clean(cls, v):
        return _text(v, 800)

    @field_validator("url", mode="before")
    @classmethod
    def _http_only(cls, v):
        v = _text(v, 2000)
        return v if re.match(r"^https?://[^\s<>\"']+$", v) else ""

    @field_validator("published_at", mode="before")
    @classmethod
    def _iso(cls, v):
        v = _text(v, 40)
        return v if re.match(r"^\d{4}-\d{2}-\d{2}T[\d:.+\-Z]+$", v) else ""


Reports = list[Report]


class _Named(_Base):
    name: str = Field(default="", max_length=160)
    aliases: list[str] = Field(default_factory=list)
    role: str = Field(default="subject", max_length=10)

    @field_validator("role", mode="before")
    @classmethod
    def _r(cls, v):
        return "voice" if v == "voice" else "subject"

    @field_validator("name", mode="before")
    @classmethod
    def _n(cls, v):
        return _text(v, 160)

    @field_validator("aliases", mode="before")
    @classmethod
    def _a(cls, v):
        return [_text(x, 160) for x in (v or [])[:8] if _text(x, 160)] if isinstance(v, list) else []


class _When(_Base):
    said: str = Field(default="", max_length=200)
    date: str = Field(default="", max_length=10)

    @field_validator("said", mode="before")
    @classmethod
    def _s(cls, v):
        return _text(v, 200)

    @field_validator("date", mode="before")
    @classmethod
    def _d(cls, v):
        v = _text(v, 10)
        return v if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) else ""


class Triad(_Base):
    """Who / where / when a story is pinned to (app/npro/triad.py)."""
    entities: list[_Named] = Field(default_factory=list)
    places: list[_Named] = Field(default_factory=list)
    when: _When = Field(default_factory=_When)
    event_terms: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)

    @field_validator("entities", "places", mode="before")
    @classmethod
    def _rows(cls, v):
        return [x for x in (v or [])[:8] if isinstance(x, dict)] if isinstance(v, list) else []

    @field_validator("event_terms", "queries", mode="before")
    @classmethod
    def _terms(cls, v):
        return [_text(x, 80) for x in (v or [])[:6] if _text(x, 80)] if isinstance(v, list) else []

    @field_validator("when", mode="before")
    @classmethod
    def _w(cls, v):
        return v if isinstance(v, dict) else {}


class Note(_Base):
    """A reporter's note as taken in: the original and its English."""
    english: str = Field(default="", max_length=8000)
    original: str = Field(default="", max_length=8000)
    language: str = Field(default="", max_length=60)

    @field_validator("english", "original", "language", mode="before")
    @classmethod
    def _t(cls, v):
        return _text(v, 8000)


def _cap_reports(v):
    return (v or [])[:30] if isinstance(v, list) else []


class _WithReports(_Base):
    story_id: int | None = Field(default=None, ge=0, le=2_000_000_000)
    topic: str = Field(default="", max_length=300)
    retrieved: Reports = Field(default_factory=list)
    note: Note | None = None
    triad: Triad | None = None
    record_id: str = Field(default="", max_length=40)

    @field_validator("retrieved", mode="before")
    @classmethod
    def _cap(cls, v):
        return _cap_reports(v)

    @field_validator("note", "triad", mode="before")
    @classmethod
    def _obj(cls, v):
        return v if isinstance(v, dict) and v else None

    @field_validator("record_id", mode="before")
    @classmethod
    def _rid(cls, v):
        v = _text(v, 40)
        return v if re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", v) else ""

    def note_dict(self) -> dict | None:
        return self.note.model_dump() if self.note and (self.note.english or self.note.original) else None

    def triad_dict(self) -> dict | None:
        return self.triad.model_dump() if self.triad else None

    @field_validator("topic", mode="before")
    @classmethod
    def _topic(cls, v):
        return _text(v, 300)

    def reports(self) -> list[dict]:
        return [r.model_dump() for r in self.retrieved if r.title]


class OpenIn(_Base):
    story_id: int | None = Field(default=None, ge=0, le=2_000_000_000)
    topic: str = Field(default="", max_length=300)


class BriefIn(_WithReports):
    pass


class NoteIn(_Base):
    """A reporter's note, pasted as it arrived."""
    text: str = Field(min_length=20, max_length=8000)

    @field_validator("text", mode="before")
    @classmethod
    def _t(cls, v):
        return _text(v, 8000)


class QueryIn(_Base):
    query: str = Field(min_length=1, max_length=600)

    @field_validator("query", mode="before")
    @classmethod
    def _q(cls, v):
        return _text(v, 600)


class Turn(_Base):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=6000)

    @field_validator("content", mode="before")
    @classmethod
    def _c(cls, v):
        return _text(v, 6000)


class ChatIn(_WithReports):
    query: str = Field(min_length=1, max_length=600)
    history: list[Turn] = Field(default_factory=list)
    script: str = Field(default="", max_length=30000)

    @field_validator("query", mode="before")
    @classmethod
    def _q(cls, v):
        return _text(v, 600)

    @field_validator("history", mode="before")
    @classmethod
    def _h(cls, v):
        return (v or [])[-10:] if isinstance(v, list) else []

    @field_validator("script", mode="before")
    @classmethod
    def _s(cls, v):
        return _text(v, 30000)


class ContextIn(_Base):
    topic: str = Field(min_length=1, max_length=300)
    used_angles: list[str] = Field(default_factory=list)
    seen_urls: list[str] = Field(default_factory=list)
    seen_titles: list[str] = Field(default_factory=list)

    @field_validator("used_angles", "seen_urls", "seen_titles", mode="before")
    @classmethod
    def _lists(cls, v):
        return [_text(x, 2000) for x in (v or [])[:200]] if isinstance(v, list) else []


FormatId = Literal["av_read", "package", "explainer", "debate", "custom"]
_GUEST_FIELDS = ("Guest name", "Designation", "Affiliation (optional)", "Area of expertise")


def clean_params(format_id: str, raw: Any) -> dict:
    """The editor's answers, held to the questions the format actually asks.
    An option that is not on the list, or an answer to a question that does
    not exist, never reaches the prompt."""
    recipe = recipes.RECIPES[format_id]
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for q in recipe["questions"]:
        v = raw.get(q["id"])
        kind = q["type"]
        if kind == "chips":
            if isinstance(v, str) and v in q["options"]:
                out[q["id"]] = v
        elif kind in ("chips_custom", "text"):
            s = _text(v, 200) if isinstance(v, (str, int, float)) else ""
            if s:
                out[q["id"]] = s
        elif kind == "multi":
            if isinstance(v, list):
                out[q["id"]] = [o for o in q["options"] if o in v]
        elif kind == "guests":
            guests = []
            for g in (v if isinstance(v, list) else [])[:8]:
                if not isinstance(g, dict):
                    continue
                row = {f: _text(g.get(f), 120) for f in _GUEST_FIELDS}
                if row["Guest name"]:
                    guests.append(row)
            out[q["id"]] = guests
    return out


class GenerateIn(_WithReports):
    format: FormatId
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _params(self):
        self.params = clean_params(self.format, self.params)
        return self


ActionId = Literal["shorter", "conversational", "dramatic", "more_facts", "history",
                   "graphics", "debate_qs", "social", "yt_title", "thumbnail",
                   "hindi", "english", "digital", "ott"]


class ActionIn(_WithReports):
    action: ActionId
    content: str = Field(min_length=1, max_length=30000)
    format: FormatId | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _c(cls, v):
        return _text(v, 30000)

    @field_validator("format", mode="before")
    @classmethod
    def _f(cls, v):
        return v if v in recipes.RECIPES else None


class TraceIn(_WithReports):
    line: str = Field(default="", max_length=1200)
    script: str = Field(default="", max_length=30000)

    @field_validator("line", "script", mode="before")
    @classmethod
    def _c(cls, v):
        return _text(v, 30000)


assert set(ActionId.__args__) == set(recipes.SMART_ACTIONS), "ActionId out of step with recipes"
assert list(FormatId.__args__) == recipes.FORMAT_ORDER, "FormatId out of step with recipes"
