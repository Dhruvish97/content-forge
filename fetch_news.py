#!/usr/bin/env python3
"""Fetches candidate news headlines from direct publisher RSS feeds (free, no
API key) and drafts content/YYYY-MM-DD.json for run_today.py to pick up.
Optionally polishes each summary via Claude Haiku if ANTHROPIC_API_KEY is set
(gracefully degrades to the real RSS description otherwise).

Reuses generate_posts' existing dedup logic so fetched candidates are
pre-filtered against recent history the same way hand-written content is.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import anthropic
import feedparser
import requests

from generate_posts import (
    check_duplicates,
    check_topic_diversity,
    check_edu_content_similarity,
    _is_high_importance,
    _keywords,
    _jaccard,
)

CONTENT_DIR = Path(__file__).parent / "content"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ANTHROPIC_MODEL = "claude-haiku-4-5"
USER_AGENT = "Mozilla/5.0 (compatible; ContentForgeBot/1.0; +https://github.com/Dhruvish97/content-forge)"
# Jaccard threshold for clustering headlines about the same real-world story
# across different outlets. Lower than EDU_SIMILARITY_THRESHOLD in
# generate_posts.py (0.45) because headlines are short and more heavily
# paraphrased between publishers than educational bullet points are.
NEWS_SIMILARITY_THRESHOLD = 0.3

# Direct publisher RSS feeds — their <link> is the real article URL (no Google
# News redirect-decoding needed), and they include real editorial descriptions.
# "source" is set explicitly rather than trusting each feed's raw <title> —
# some are clunky ("AI News & Artificial Intelligence | TechCrunch") or
# outright wrong for display purposes (CNBC's earnings feed titles itself
# just "Earnings", not "CNBC").
#
# Deliberately avoid each publisher's general "top stories" firehose for
# TECH/MARKETS — those mix in reviews, buying guides, and (for MarketWatch
# specifically) the first-person "Moneyist" advice column alongside real
# news, which is how posts about gaming-laptop reviews and inheritance
# etiquette ended up in the feed. Section-specific feeds (funding/enterprise
# for tech, market-moving headlines for finance) stay on-topic instead.
#
# Multiple feeds per category let fetch_candidate_news() prefer stories
# corroborated across independent outlets (see NEWS_SIMILARITY_THRESHOLD
# clustering below) as a free proxy for "popular," since RSS carries no
# engagement metrics of its own. EARNINGS has no second free feed, so it
# always falls back to "most recent" for that category.
FEED_SOURCES = [
    {"url": "https://techcrunch.com/category/venture/feed/", "category": "TECH", "source": "TechCrunch"},
    {"url": "https://techcrunch.com/tag/enterprise/feed/", "category": "TECH", "source": "TechCrunch"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "category": "AI", "source": "TechCrunch"},
    {"url": "https://venturebeat.com/category/ai/feed/", "category": "AI", "source": "VentureBeat"},
    {"url": "https://feeds.marketwatch.com/marketwatch/marketpulse/", "category": "MARKETS", "source": "MarketWatch"},
    {"url": "https://www.cnbc.com/id/10000664/device/rss/rss.html", "category": "MARKETS", "source": "CNBC"},
    {"url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135", "category": "EARNINGS", "source": "CNBC"},
    {"url": "https://decrypt.co/feed", "category": "CRYPTO", "source": "Decrypt"},
    {"url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "category": "CRYPTO", "source": "CoinDesk"},
    {"url": "https://cointelegraph.com/rss", "category": "CRYPTO", "source": "Cointelegraph"},
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
    {
        "title": "Bull Market vs. Bear Market — What the Terms Actually Mean",
        "category": "MARKETS 101",
        "points": [
            "A bull market is a sustained rise in prices; a bear market is a sustained fall",
            "The common threshold: a 20%+ drop from a recent high defines a bear market",
            "Bull markets historically last far longer than bear markets on average",
            "Investor psychology often overshoots in both directions — greed, then fear",
            "Long-term investors are taught to expect both phases, not just the good one",
        ],
    },
    {
        "title": "How a Stock Split Actually Works",
        "category": "INVESTING 101",
        "points": [
            "A stock split divides each share into multiple shares — e.g. 1 becomes 4",
            "Your total investment value doesn't change, just the share count and price",
            "Companies split shares to make each one look more affordable to new buyers",
            "It's a cosmetic change, not a sign the company suddenly became more valuable",
            "Reverse splits do the opposite — often a red flag, not a good one",
        ],
    },
    {
        "title": "What Inflation Actually Does to Your Money",
        "category": "FINANCIAL LITERACY",
        "points": [
            "Inflation means the same dollar buys less over time as prices rise",
            "Cash sitting idle quietly loses purchasing power every year to inflation",
            "This is a core reason people invest rather than just save in cash",
            "Central banks target a small amount of inflation (often ~2%) as \"healthy\"",
            "Wages that don't keep pace with inflation mean a real pay cut, even if the number goes up",
        ],
    },
    {
        "title": "How Venture Capital Actually Funds Startups",
        "category": "TECH 101",
        "points": [
            "VCs invest other people's money (a \"fund\") in exchange for equity, not loans",
            "Most funded startups fail — VC math relies on a few huge wins covering the rest",
            "Funding rounds (seed, Series A, B...) mark stages of growth and rising valuation",
            "Taking VC money means giving up some ownership and control in exchange for capital",
            "Not every business should raise VC — it's built for high-growth, high-risk bets",
        ],
    },
    {
        "title": "What a 401(k) Match Actually Means (Free Money, Really)",
        "category": "PERSONAL FINANCE",
        "points": [
            "A 401(k) match is your employer adding money when you contribute to retirement",
            "Not contributing enough to get the full match is leaving guaranteed money on the table",
            "It's typically the best first place to invest, before other accounts",
            "Contributions often reduce your taxable income today, on top of the match",
            "Vesting schedules can delay when employer-matched money is fully yours — check the terms",
        ],
    },
]


def _strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def _entry_to_news_item(entry, category, source):
    headline = _strip_html(entry.get("title", "")).strip()
    summary = _strip_html(entry.get("summary", "")).strip()
    return {
        "headline": headline,
        "summary": summary or f"Reported by {source}.",
        "category": category,
        "source": source,
    }


def _fetch_feed_entries(url):
    """Fetch+parse one feed; returns [] on any failure (timeout, 4xx/5xx, bad XML)
    so one broken/blocked feed can't take down the whole run. A plain
    feedparser.parse(url) call gets redirected/blocked (308) on some hosts
    (VentureBeat, CoinDesk) without a browser-like User-Agent, so fetch via
    requests first and hand the raw bytes to feedparser."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
        resp.raise_for_status()
        return feedparser.parse(resp.content).entries
    except Exception:
        return []


def _entry_published(entry):
    """Return the entry's publish time as a naive UTC datetime, or None if the
    feed didn't supply a parseable one (missing timestamps are kept, not
    penalized, by the caller)."""
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*struct[:6]) if struct else None


def fetch_candidate_news(n=2, hours=24):
    """Fetch from all configured feeds, cluster same-story coverage within each
    category via headline word-overlap, and prefer the story with the most
    distinct-source corroboration in the last `hours` — a free proxy for
    "popular" since RSS carries no engagement metrics of its own. Falls back
    to most-recent when nothing corroborates (e.g. a category backed by only
    one feed)."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    raw = []
    seen_headlines = set()
    for feed_src in FEED_SOURCES:
        for entry in _fetch_feed_entries(feed_src["url"])[:10]:
            item = _entry_to_news_item(entry, feed_src["category"], feed_src["source"])
            key = item["headline"].lower().strip()
            if not key or key in seen_headlines:
                continue
            published = _entry_published(entry)
            if published and published < cutoff:
                continue
            seen_headlines.add(key)
            raw.append({"item": item, "published": published})

    by_category = {}
    for r in raw:
        by_category.setdefault(r["item"]["category"], []).append(r)

    ranked_candidates = []
    for entries in by_category.values():
        clusters = []
        for r in entries:
            kw = _keywords(r["item"]["headline"])
            for cluster in clusters:
                if _jaccard(kw, _keywords(cluster[0]["item"]["headline"])) >= NEWS_SIMILARITY_THRESHOLD:
                    cluster.append(r)
                    break
            else:
                clusters.append([r])

        def _cluster_sort_key(cluster):
            source_count = len({c["item"]["source"] for c in cluster})
            timestamps = [c["published"] for c in cluster if c["published"]]
            newest = max(timestamps).timestamp() if timestamps else 0
            return (-source_count, -newest)

        clusters.sort(key=_cluster_sort_key)
        for cluster in clusters:
            rep = max(cluster, key=lambda c: len(c["item"]["summary"]))
            ranked_candidates.append(rep["item"])

    dupes = {h.lower().strip() for h in check_duplicates(ranked_candidates, None)}
    candidates = [c for c in ranked_candidates if c["headline"].lower().strip() not in dupes]

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


def _polish_summary(headline, category, source, rss_description, api_key):
    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=ANTHROPIC_MODEL, max_tokens=180,
            messages=[{"role": "user", "content": (
                "Expand this brief news description into a 2-3 sentence Instagram caption "
                "summary (under 320 characters total — this renders on a fixed-size image "
                "with limited space), in a punchy, informative finance/tech newsletter voice. "
                "Stay grounded in the facts given below — do not invent additional facts, "
                "figures, or details not present in the description. No hashtags, no headline "
                f"restatement, no preamble.\n\nHeadline: {headline}\nCategory: {category}\n"
                f"Source: {source}\n\nDescription: {rss_description}"
            )}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "").strip()
        return text or None
    except Exception:
        return None


def enrich_news_items(items):
    """Best-effort: polish each item's real RSS description via Haiku into a fuller
    summary. Never fails the caller — leaves the item untouched if anything goes wrong,
    or if there's no real description to work from (just the thin fallback)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return items
    for item in items:
        if not item["summary"] or item["summary"].startswith("Reported by"):
            continue  # nothing real to polish — leave the thin fallback as-is
        polished = _polish_summary(item["headline"], item["category"], item["source"], item["summary"], api_key)
        if polished:
            item["summary"] = polished
    return items


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
    if auto_educational and (force or not educational):
        educational = pick_educational()

    data = {"news": news_items, "educational": educational}
    path.write_text(json.dumps(data, indent=2))
    print(f"✅ Wrote {path.name} with {len(news_items)} news item(s).")
    if educational is None:
        print("   ⚠️  \"educational\" is still empty — fill it in before running run_today.py")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch candidate news via direct publisher RSS feeds and draft content/{date}.json"
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

    news_items = enrich_news_items(news_items)

    for item in news_items:
        print(f"   • [{item['category']}] {item['headline']} ({item['source']})")

    written = write_draft(args.date, news_items, auto_educational=args.auto_educational, force=args.force)
    if written is None:
        sys.exit(1)
