"""One-off import: india_x_master_database_1.xlsx -> data/handles.json.

Maps each account's Category onto the dashboard's three streams:
  A - Institutional & Government Feeds
  B - Rival / Competitor Channels
  C - Field Reporters & Emergency Respondents

Trust scores are seeded per category tier; rows the spreadsheet itself flags
with 'Verify' in Notes are docked (its README warns black-text rows are
unverified).

Usage: python scripts/import_handles.py [path-to-xlsx]
"""

import json
import sys
from pathlib import Path

import openpyxl

DEFAULT_XLSX = Path(r"C:\Users\Gautam\Desktop\AI Newsroom 3\X Monitor\india_x_master_database_1.xlsx")
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "handles.json"

COLUMN_MAP = {
    "Government/Official Agency": "A",
    "Wire Service": "A",
    "Fact-Checking Organization": "A",
    "National TV Channel": "B",
    "Regional TV Channel": "B",
    "Newspaper": "B",
    "Digital-First Outlet": "B",
    "Bureau Chief": "C",
    "State Beat Reporter": "C",
    "Top Anchor/Editor": "C",
    "Political Correspondent": "C",
    "Business/Markets Journalist": "C",
    "Defence Correspondent": "C",
    "Technology Reporter": "C",
    "Court Reporter": "C",
    "Election Specialist": "C",
}

TRUST_BASE = {
    "Government/Official Agency": 92,
    "Wire Service": 90,
    "Fact-Checking Organization": 85,
    "National TV Channel": 80,
    "Newspaper": 80,
    "Digital-First Outlet": 72,
    "Regional TV Channel": 75,
    "Bureau Chief": 78,
    "Top Anchor/Editor": 75,
    "Political Correspondent": 72,
    "Business/Markets Journalist": 72,
    "Defence Correspondent": 74,
    "Court Reporter": 74,
    "Election Specialist": 70,
    "State Beat Reporter": 68,
    "Technology Reporter": 68,
}


def main() -> None:
    xlsx = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_XLSX
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb["Master DB"]

    handles = []
    header_seen = False
    for row in ws.iter_rows(values_only=True):
        if not header_seen:
            header_seen = row and row[0] == "S.No"
            continue
        if not row or not row[4]:
            continue
        _, category, sub_category, name, handle, org, region, notes = (
            [str(c).strip() if c is not None else "" for c in row[:8]]
        )
        trust = TRUST_BASE.get(category, 60)
        if "verify" in notes.lower():
            trust = max(30, trust - 15)
        handles.append({
            "handle": handle if handle.startswith("@") else f"@{handle}",
            "name": name,
            "category": category,
            "sub_category": sub_category,
            "organization": org,
            "region": region,
            "stream_column": COLUMN_MAP.get(category, "C"),
            "trust_score": trust,
            "notes": notes,
        })

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(handles, indent=2, ensure_ascii=False), encoding="utf-8")

    counts = {}
    for h in handles:
        counts[h["stream_column"]] = counts.get(h["stream_column"], 0) + 1
    print(f"Wrote {len(handles)} handles to {OUT_PATH}")
    print("Per column:", counts)


if __name__ == "__main__":
    main()
