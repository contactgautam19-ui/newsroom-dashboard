"""One-off import: Newsroom_50_Source_Ingestion_Matrix.xlsx -> data/news_sources.json.

Rows whose RSS_URL_Example is not an http(s) URL (wire APIs, scraping-dependent
sites) are kept but marked inactive — they're the documented plug-in points for
direct API integrations later.

Usage: python scripts/import_news_sources.py [path-to-xlsx]
"""

import json
import sys
from pathlib import Path

import openpyxl

DEFAULT_XLSX = Path(r"C:\Users\Gautam\Desktop\AI Newsroom 3\News Monitor\Newsroom_50_Source_Ingestion_Matrix.xlsx")
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "news_sources.json"

# Focus column -> dashboard category hint (falls back to keyword enrichment)
FOCUS_CATEGORY = [
    ("markets", "Business"), ("corporate", "Business"), ("econom", "Business"),
    ("finance", "Business"), ("business", "Business"), ("sebi", "Business"),
    ("startup", "Business"), ("m&a", "Business"),
    ("tech", "Technology"), ("telecom", "Technology"), ("silicon", "Technology"),
    ("cricket", "Sports"), ("sports", "Sports"),
    ("global", "International"), ("geopolitics", "International"),
    ("middle east", "International"), ("european", "International"),
    ("asia-pacific", "International"), ("world", "International"),
    ("court", "Legal"), ("law", "Legal"),
    ("politics", "Politics"), ("governance", "Politics"), ("policy", "Politics"),
]


def category_hint(focus: str) -> str:
    low = focus.lower()
    for term, cat in FOCUS_CATEGORY:
        if term in low:
            return cat
    return "National"


def main() -> None:
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb["Ingestion_Matrix"]

    sources = []
    rows = ws.iter_rows(values_only=True)
    next(rows)  # header
    for row in rows:
        if not row or row[1] is None:
            continue
        rank, source, tier, focus, rss = (str(c).strip() if c is not None else "" for c in row[:5])
        has_rss = rss.lower().startswith("http")
        sources.append({
            "rank": int(rank),
            "source": source,
            "velocity_tier": tier,
            "focus": focus,
            "rss_url": rss if has_rss else None,
            "active": has_rss,
            "category_hint": category_hint(focus),
            "note": None if has_rss else f"no direct RSS ({rss}) — needs API/scraper integration",
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
    active = sum(1 for s in sources if s["active"])
    print(f"Wrote {len(sources)} sources ({active} active RSS, {len(sources) - active} pending API) to {OUT_PATH}")


if __name__ == "__main__":
    main()
