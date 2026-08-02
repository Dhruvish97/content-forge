#!/usr/bin/env python3
"""Fetches candidate news headlines from Google News RSS (free, no API key)
and drafts content/YYYY-MM-DD.json for run_today.py to pick up.

Reuses generate_posts' existing dedup logic so fetched candidates are
pre-filtered against recent history the same way hand-written content is.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import feedparser

from generate_posts import (
    check_duplicates,
    check_topic_diversity,
    check_edu_content_similarity,
    _is_high_importance,
)

CONTENT_DIR = Path(__file__).parent / "content"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

NEWS_QUERIES = [
    {"query": "technology industry news", "category": "TECH"},
    {"query": "stock market", "category": "MARKETS"},
    {"query": "quarterly earnings report", "category": "EARNINGS"},
    {"query": "artificial intelligence", "category": "AI"},
    {"query": "cryptocurrency bitcoin", "category": "CRYPTO"},
]

# Rotating fallback educational content for fully-automated runs (--auto-educational).
# Picked in order, skipping anything that would fail the existing dedup checks.
EDUCATIONAL_BANK = [
    {
        "title": "What Is Diversification, Really?",
        "category": "INVESTING 101",
        "points": [
            "Diversification means spreading money across many assets, not just many stocks",
            "It won't stop losses, but it stops one bad bet from wiping you out",
            "Owning 10 tech stocks isn't diversified — that's still one sector's risk",
            "True diversification spans asset classes: stocks, bonds, cash, real estate",
            "The goal isn't more returns — it's smoother, more survivable ones",
        ],
    },
    {
        "title": "How Interest Rates Actually Move Your Money",
        "category": "FINANCIAL LITERACY",
        "points": [
            "The Fed sets a target rate that ripples into loans, mortgages, and savings",
            "Higher rates make borrowing pricier — car loans and mortgages cost more",
            "Higher rates also make saving pay better — bank yields tend to rise too",
            "Stocks often wobble on rate news because future profits get discounted more",
            "You don't need to predict rates — just understand why headlines move markets",
        ],
    },
    {
        "title": "What Actually Happens When You Buy a Stock",
        "category": "INVESTING 101",
        "points": [
            "You're buying a tiny slice of real ownership in a company, not a lottery ticket",
            "Your broker matches your order with a seller through an exchange in milliseconds",
            "Stock price moves on collective belief about future profits, not just today's news",
            "You make money two ways: the price rising, or dividends paid out over time",
            "Owning one share still makes you a part-owner, with voting rights and all",
        ],
    },
    {
        "title": "Machine Learning vs. AI vs. LLMs — What's the Difference?",
        "category": "TECH 101",
        "points": [
            "AI is the broad goal: machines performing tasks that normally need human intelligence",
            "Machine learning is one method — systems that improve from data instead of fixed rules",
            "Deep learning is a subset of ML using layered neural networks",
            "LLMs (like the ones powering chatbots) are deep learning models trained on huge text data",
            "Not all AI is an LLM, and not all machine learning involves neural networks",
        ],
    },
    {
        "title": "What Is Bitcoin's 'Halving' and Why Does It Matter?",
        "category": "CRYPTO 101",
        "points": [
            "Roughly every 4 years, Bitcoin's mining reward is cut in half by design",
            "This slows how fast new bitcoin enters circulation, capping supply growth",
            "It's baked into the code — no company or person decides when it happens",
            "Historically, halvings have preceded major price cycles, though it's not guaranteed",
            "The last coin will ever be mined around the year 2140 due to this schedule",
        ],
    },
    {
        "title": "The Emergency Fund Rule Nobody Follows (But Should)",
        "category": "FINANCIAL LITERACY",
        "points": [
            "An emergency fund is 3-6 months of essential expenses, kept in cash, not invested",
            "It's not for vacations — it's for job loss, medical bills, or a broken car",
            "Without one, a single bad month can force you into high-interest debt",
            "Keep it boring: a high-yield savings account, not the stock market",
            "Build it before investing aggressively — it's the foundation everything else sits on",
        ],
    },
    {
        "title": "Why Company Earnings Move Stock Prices So Much",
        "category": "MARKETS 101",
        "points": [
            "Stock prices reflect expectations — earnings reports test if those expectations were right",
            "Beating profit estimates doesn't guarantee a stock rises if guidance disappoints",
            "'Guidance' — a company's own forecast — often matters more than the past quarter",
            "Markets move on the surprise, not the number itself",
            "This is why a profitable company's stock can still crash on earnings day",
        ],
    },
    {
        "title": "What a Recession Actually Means (And What It Doesn't)",
        "category": "FINANCIAL LITERACY",
        "points": [
            "A recession is broadly defined as a significant decline in economic activity",
            "It's not the same as a stock market crash — they can happen independently",
            "Recessions typically bring rising unemployment and falling consumer spending",
            "They're a normal part of economic cycles, not a rare catastrophe",
            "Historically, markets have recovered from every recession, though timing varies",
        ],
    },
    {
        "title": "Index Funds vs. Picking Individual Stocks",
        "category": "INVESTING 101",
        "points": [
            "An index fund buys a whole market segment at once — instant diversification",
            "Most professional stock-pickers underperform simple index funds over time",
            "Individual stocks can outperform, but concentrate your risk in one company",
            "Index investing trades excitement for consistency and much lower fees",
            "A common approach: index funds as the base, individual picks as a smaller side bet",
        ],
    },
    {
        "title": "What Cloud Computing Actually Means",
        "category": "TECH 101",
        "points": [
            "\"The cloud\" is just someone else's data center, rented over the internet",
            "It lets companies scale computing power up or down without buying hardware",
            "Three big providers dominate: AWS, Microsoft Azure, and Google Cloud",
            "This is why AI's growth is tightly linked to cloud/data-center spending",
            "Nearly every app you use — banking, social media, streaming — runs on someone's cloud",
        ],
    },
]


def _strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _entry_to_news_item(entry, category):
    source = (entry.get("source") or {}).get("title")
    title = entry.get("title", "").strip()
    headline = title
    if source and title.endswith(f" - {source}"):
        headline = title[: -len(f" - {source}")].strip()

    plain_summary = _strip_html(entry.get("summary", ""))
    # Google News RSS summaries are just a link wrapping the (often truncated)
    # headline — not real body text. Fall back to a generic line when the
    # "summary" is really just the headline restated.
    is_redundant = (
        not plain_summary
        or plain_summary.lower().rstrip(".…").startswith(headline.lower()[:20])
    )
    if is_redundant:
        summary = f"Reported by {source}." if source else "Details developing — check the source for full coverage."
    else:
        summary = plain_summary

    return {
        "headline": headline,
        "summary": summary,
        "category": category,
        "source": source or "Unknown",
    }


def _fetch_query(query, max_results=5):
    url = f"https://news.google.com/rss/search?q={quote(query)}+when:1d&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(url)
    return feed.entries[:max_results]


def fetch_candidate_news(n=2):
    """Fetch, dedupe, and pick n news items across the configured topic queries."""
    raw_candidates = []
    seen_headlines = set()
    for q in NEWS_QUERIES:
        for entry in _fetch_query(q["query"]):
            item = _entry_to_news_item(entry, q["category"])
            key = item["headline"].lower().strip()
            if key and key not in seen_headlines:
                seen_headlines.add(key)
                raw_candidates.append(item)

    dupes = {h.lower().strip() for h in check_duplicates(raw_candidates, None)}
    candidates = [c for c in raw_candidates if c["headline"].lower().strip() not in dupes]

    overused = {cat.lower() for cat, _ in check_topic_diversity(candidates)}
    filtered = [
        c for c in candidates
        if c["category"].lower() not in overused or _is_high_importance(c["headline"])
    ]
    if not filtered:
        filtered = candidates

    picked = []
    used_categories = set()
    for c in filtered:
        if len(picked) >= n:
            break
        if c["category"] not in used_categories:
            picked.append(c)
            used_categories.add(c["category"])
    for c in filtered:
        if len(picked) >= n:
            break
        if c not in picked:
            picked.append(c)
    return picked[:n]


def pick_educational():
    """Return the first bank entry that clears the existing dedup checks."""
    for edu in EDUCATIONAL_BANK:
        if check_duplicates([], edu):
            continue
        if check_edu_content_similarity(edu):
            continue
        return edu
    return EDUCATIONAL_BANK[0]


def write_draft(date_str, news_items, auto_educational=False, force=False):
    path = CONTENT_DIR / f"{date_str}.json"
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            existing = {}

    if existing.get("news") and not force:
        print(f"⚠️  {path.name} already has news content — use --force to overwrite.")
        return None

    educational = existing.get("educational")
    if not educational and auto_educational:
        educational = pick_educational()

    data = {"news": news_items, "educational": educational}
    path.write_text(json.dumps(data, indent=2))
    print(f"✅ Wrote {path.name} with {len(news_items)} news item(s).")
    if educational is None:
        print("   ⚠️  \"educational\" is still empty — fill it in before running run_today.py")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch candidate news via Google News RSS and draft content/{date}.json"
    )
    parser.add_argument("date", nargs="?", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("-n", "--count", type=int, default=2, help="Number of news items to draft")
    parser.add_argument("--force", action="store_true", help="Overwrite existing news content for the date")
    parser.add_argument(
        "--auto-educational", action="store_true",
        help="Auto-fill educational content from a rotating bank instead of leaving it blank",
    )
    args = parser.parse_args()

    if not DATE_RE.match(args.date):
        print(f"❌ Invalid date '{args.date}' — expected YYYY-MM-DD.")
        sys.exit(1)

    print(f"📡 Fetching candidate news for {args.date}...")
    news_items = fetch_candidate_news(n=args.count)
    if not news_items:
        print("❌ No candidate news found (everything was filtered as duplicate/overused, or the feed was empty).")
        sys.exit(1)

    for item in news_items:
        print(f"   • [{item['category']}] {item['headline']} ({item['source']})")

    written = write_draft(args.date, news_items, auto_educational=args.auto_educational, force=args.force)
    if written is None:
        sys.exit(1)
