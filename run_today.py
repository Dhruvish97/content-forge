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

REQUIRED_NEWS_FIELDS = ("headline", "summary", "category", "source")


def _validate_news_item(item, idx):
    if not isinstance(item, dict):
        return [f"news[{idx}] must be an object, got {type(item).__name__}"]
    return [
        f"news[{idx}].{field} is missing or empty"
        for field in REQUIRED_NEWS_FIELDS
        if not isinstance(item.get(field), str) or not item[field].strip()
    ]


def _validate_educational(edu):
    if not isinstance(edu, dict):
        return ["educational must be an object"]
    errors = []
    if not isinstance(edu.get("title"), str) or not edu["title"].strip():
        errors.append("educational.title is missing or empty")
    if not isinstance(edu.get("points"), list) or len(edu["points"]) == 0:
        errors.append("educational.points must be a non-empty list")
    return errors


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

    if not isinstance(data["news"], list) or len(data["news"]) == 0:
        print(f"❌ {path.name}: \"news\" must be a non-empty list.")
        return None

    errors = []
    for idx, item in enumerate(data["news"]):
        errors.extend(_validate_news_item(item, idx))
    errors.extend(_validate_educational(data["educational"]))
    if errors:
        print(f"❌ {path.name} failed validation:")
        for e in errors:
            print(f"   • {e}")
        return None

    if len(data["news"]) == 1:
        print("⚠️  Only 1 news item provided — post 2 will reuse it.")
    points = data["educational"].get("points", [])
    if 0 < len(points) < 5:
        print(f"⚠️  Only {len(points)} educational points provided — layout supports up to 5.")

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
