#!/usr/bin/env python3
"""Reports whether today's content/posts already exist, so the daily
workflow can skip redundant work when a backup scheduled run fires after
the primary one already succeeded. GitHub Actions does not guarantee a
`schedule` trigger fires at all on a given day (it's explicitly
best-effort and can be delayed or dropped under load), so daily-posts.yml
runs on two schedules a few hours apart; this script is what lets the
second one be a safe no-op instead of a duplicate post.

Prints GITHUB_OUTPUT-formatted `key=value` lines:
  content_ready=true|false      — content/{date}.json has real news + educational content
  already_published=true|false  — posts/publish_log.json shows all of today's posts published
"""
import json
import sys
from datetime import datetime
from pathlib import Path

CONTENT_DIR = Path(__file__).parent / "content"
PUBLISH_LOG = Path(__file__).parent / "posts" / "publish_log.json"


def content_ready(date_str):
    path = CONTENT_DIR / f"{date_str}.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return False
    return bool(data.get("news")) and bool(data.get("educational"))


def already_published(date_str):
    if not PUBLISH_LOG.exists():
        return False
    try:
        log = json.loads(PUBLISH_LOG.read_text())
    except json.JSONDecodeError:
        return False
    entries = log.get(date_str, [])
    return len(entries) >= 3 and all(e.get("status") == "published" for e in entries)


if __name__ == "__main__":
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    print(f"content_ready={'true' if content_ready(date_str) else 'false'}")
    print(f"already_published={'true' if already_published(date_str) else 'false'}")
