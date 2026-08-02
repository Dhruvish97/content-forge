#!/usr/bin/env python3
"""Daily post runner — loads today's (or a given day's) content
from content/YYYY-MM-DD.json and generates the 3 daily posts."""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from generate_posts import generate_all_daily_posts

CONTENT_DIR = Path(__file__).parent / "content"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA_EXAMPLE = """{
  "news": [
    {"headline": "...", "summary": "...", "category": "...", "source": "...",
     "stat_label": "...", "stat_value": "..."},
    {"headline": "...", "summary": "...", "category": "...", "source": "..."}
  ],
  "educational": {
    "title": "...", "category": "...",
    "points": ["...", "...", "...", "...", "..."]
  }
}"""


def load_content(date_str):
    if not DATE_RE.match(date_str):
        print(f"❌ Invalid date '{date_str}' — expected YYYY-MM-DD.")
        return None

    path = CONTENT_DIR / f"{date_str}.json"
    if not path.exists():
        print(f"❌ No content file found for {date_str}: {path}")
        print(f"   Create it with this shape:\n{SCHEMA_EXAMPLE}")
        return None

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        print(f"❌ {path.name} is not valid JSON: {e}")
        return None

    missing = [k for k in ("news", "educational") if k not in data]
    if missing:
        print(f"❌ {path.name} is missing required key(s): {', '.join(missing)}")
        return None

    return data


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

    print("🚀 Content Forge — Daily Post Runner")
    print(f"📅 Date: {date_str}")
    print("=" * 60)

    data = load_content(date_str)
    if data is None:
        sys.exit(1)

    results = generate_all_daily_posts(
        news_items=data["news"],
        edu_item=data["educational"],
    )
    print("=" * 60)
    if results:
        print(f"✨ {len(results)} posts generated and saved!")
    else:
        print("❌ No posts generated — check duplicate/diversity warnings above.")
