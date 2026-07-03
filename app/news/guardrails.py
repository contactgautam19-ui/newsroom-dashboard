"""Editorial guardrails (PRD section 4).

- Two-source rule: breaking stories corroborated by fewer than two publishers
  are demoted from 'breaking' status and flagged.
- Confidence gate: below 70% -> needs_review (human-in-the-loop advisory flag).
- Repetitive decay: stories that do not materially develop across cycles lose
  points each hour so stale bulletins sink.
"""

from app import config
from app.news.models import EnrichedStory


def apply_guardrails(story: EnrichedStory, score: dict, confidence: int) -> dict:
    """Returns {'status', 'needs_review', 'notes': [audit strings]}."""
    notes = []
    is_breaking = story.flags.get("breaking", False)
    source_count = len(set(story.sources))

    status = "developing"
    if is_breaking:
        if source_count >= 2:
            status = "breaking"
            notes.append(f"two-source rule satisfied ({source_count} publishers)")
        else:
            status = "developing"
            notes.append(
                f"two-source rule NOT met ({source_count} publisher) — "
                "breaking status withheld per zero-speculation policy"
            )
    elif confidence >= 80 and source_count >= 2:
        status = "verified"
        notes.append(f"corroborated by {source_count} publishers at {confidence}% confidence")

    needs_review = confidence < config.CONFIDENCE_REVIEW_THRESHOLD
    if needs_review:
        notes.append(
            f"confidence {confidence}% < {config.CONFIDENCE_REVIEW_THRESHOLD}% — "
            "flagged for human review"
        )

    return {"status": status, "needs_review": needs_review, "notes": notes}


def decay_for_stale_cycles(stale_cycles: int) -> int:
    # stale_cycles now advances at most once per hour (ingest aging query),
    # and total decay is capped so frequent refreshes can't zero a story out
    return min(config.REPETITIVE_DECAY_CAP,
               stale_cycles * config.REPETITIVE_DECAY_PER_HOUR)
