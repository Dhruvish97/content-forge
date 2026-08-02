# Content Forge

An automated content generation tool for daily social media posts. Point it
at a day's news/educational content and it renders on-brand Instagram-ready
graphics (2 news posts + 1 educational post) with matching captions, while
tracking recent output so it won't repeat headlines or topics.

## Features

- **Image generation** — 1080×1080 Instagram-square graphics across 4
  interchangeable news layouts (dark, gradient, split-banner, stat-hero)
  plus a dedicated educational layout, built with Pillow (no design tool
  required). Palettes are contrast-checked so text stays readable
  regardless of which style/palette combination gets picked.
- **Caption + hashtag generation** — category-aware hashtag sets, ready to
  paste into Instagram.
- **Content deduplication** — refuses to generate if a headline repeats
  within 7 days, a topic category has run 3+ days this week, or an
  educational point is too similar (Jaccard word-overlap) to one from the
  last 7 days. High-importance breaking news bypasses the topic cap.
- **Content validation** — `run_today.py` checks required fields (headline,
  summary, category, source, educational title/points) before rendering,
  failing fast with a clear message instead of crashing mid-render.
- **Free news auto-fetch** — `fetch_news.py` pulls candidate headlines from
  2 direct publisher RSS feeds per sector (TechCrunch Venture/Enterprise for
  tech, TechCrunch AI/VentureBeat for AI, MarketWatch MarketPulse/CNBC
  Finance for markets, CNBC for earnings, Decrypt/CoinDesk/Cointelegraph for
  crypto — no API key required), pre-filtered through the same dedup logic
  as a manual run. These are deliberately section-specific feeds rather than
  each publisher's general "top stories" firehose — the latter mixes in
  reviews, buying guides, and first-person advice columns alongside real
  news. Within each sector, stories published in the last 24h are clustered
  by headline overlap and the story corroborated by the most independent
  outlets wins — a free proxy for "what's actually popular today" since RSS
  itself has no engagement metrics. A sector with only one covering outlet
  (or no cross-outlet overlap that day) falls back to picking the most
  recent story, same as before. Optionally polishes the winning summary into
  a fuller, multi-sentence caption via Claude Haiku (see
  [News Summary Enrichment](#news-summary-enrichment)) — this is the one
  place an LLM is used in the pipeline; everything else is plain Python +
  Pillow.
- **Automated daily scheduling** — a GitHub Actions workflow
  (`.github/workflows/daily-posts.yml`) fetches, validates, and generates
  posts daily, entirely in the cloud.
- **Instagram auto-posting** — `post_to_instagram.py` publishes the day's
  posts live to your Instagram Business/Creator account via the Graph API,
  fully wired into the daily workflow (see [Instagram Auto-Posting
  Setup](#instagram-auto-posting-setup)).
- **Config-driven brand** — your account name/handle live in a local,
  gitignored config file, not hardcoded into the tool.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and set your own brand/handle. Unlike most local
config files, `config.json` is tracked in git here — your brand name and
handle are already public on every generated post, so there's nothing to
protect by hiding them, and committing it means the automated GitHub
Actions workflow renders with your real brand instead of the generic
placeholder:

```bash
cp config.example.json config.json
```

```json
{
  "brand": "YourBrand",
  "handle": "@yourhandle"
}
```

If `config.json` is absent, the tool falls back to the generic values in
`config.example.json`.

## Daily workflow

**Option A — auto-fetch news (free, no API key required):**

```bash
python3 fetch_news.py                        # drafts content/{today}.json from direct publisher RSS feeds
python3 fetch_news.py --auto-educational      # also fills in educational content from a rotating bank
python3 fetch_news.py 2026-05-23 --force      # target a specific date, overwriting existing news
```

Pulls real headlines + real editorial descriptions directly from each
publisher's own RSS feed (see [News Summary
Enrichment](#news-summary-enrichment) for optionally polishing those
descriptions into fuller captions). Review/edit the drafted
`content/{date}.json`, then run it as below — or skip the review and use
`--auto-educational` for a fully unattended run (this is what the GitHub
Actions workflow does).

**Option B — hand-write it:**

1. Create `content/YYYY-MM-DD.json` with the day's stories:

   ```json
   {
     "news": [
       {"headline": "...", "summary": "...", "category": "...", "source": "...",
        "stat_label": "...", "stat_value": "..."},
       {"headline": "...", "summary": "...", "category": "...", "source": "..."}
     ],
     "educational": {
       "title": "...", "category": "...",
       "points": ["...", "...", "...", "...", "..."]
     }
   }
   ```

   `stat_label`/`stat_value` are optional per news item.

2. Run it:

   ```bash
   python3 run_today.py            # uses today's date
   python3 run_today.py 2026-05-23 # or generate for a specific date
   ```

   `run_today.py` validates required fields before rendering. Output (PNGs +
   caption `.txt` files) is written to `posts/`.

**Option C — fully automated:** the GitHub Actions workflow in
`.github/workflows/daily-posts.yml` runs `fetch_news.py --force
--auto-educational` then `run_today.py` daily at 8:00 AM America/Chicago,
commits the fetched `content/*.json` and updated `content_log.json` back to
the repo, **publishes the 3 posts live to Instagram** (see [Instagram
Auto-Posting Setup](#instagram-auto-posting-setup) — requires one-time
manual setup first), and uploads the generated PNGs/captions/publish log as
a downloadable Actions artifact regardless of publish outcome. Trigger it
manually anytime via the Actions tab → "Run workflow".

## News Summary Enrichment

`fetch_news.py` pulls a real one-sentence editorial description directly
from each publisher's RSS feed (a genuine step up from the placeholder text
a search-aggregator like Google News gives you). Optionally, if
`ANTHROPIC_API_KEY` is set, it sends that description to **Claude Haiku
4.5** to expand it into a fuller 2-4 sentence caption — explicitly
instructed to stay grounded in the given facts rather than inventing new
ones. At the volume this runs (up to 2 calls/day), cost is roughly
**$0.20–0.35/month**.

This step is entirely optional and fails safe: no key set (or the API call
fails for any reason) → falls back to the real RSS description as-is, or
the thin `"Reported by X."` placeholder if a feed had no description that
day. Nothing here can block a post from generating.

To enable it, add `ANTHROPIC_API_KEY` as a repo secret (Settings → Secrets
and variables → Actions) alongside the Instagram secrets below.

## Instagram Auto-Posting Setup

`post_to_instagram.py` publishes the day's 3 posts live to Instagram using
the **Instagram API with Instagram Login** flow — no Facebook Page linking
required. This is a one-time manual setup you do yourself in a browser
(can't be automated from here):

1. Convert your Instagram account to a **Business** or **Creator** account
   (Settings → Account type, in the Instagram app).
2. Create a Meta Developer App at
   [developers.facebook.com/apps](https://developers.facebook.com/apps),
   then add the **Instagram** product configured for **"API setup with
   Instagram login"**.
3. Complete the authorization flow in the app dashboard to connect your own
   Instagram account and generate a long-lived access token, plus note your
   Instagram-scoped user ID.
4. Because you're only posting to your own account (added as the app's own
   tester), Meta's **Standard Access** tier applies — no App Review or
   Business Verification needed.
5. Add two repository secrets (Settings → Secrets and variables → Actions
   on GitHub): `IG_ACCESS_TOKEN` and `IG_USER_ID`. (`GITHUB_TOKEN` needs no
   setup — it's Actions' built-in token, already used to create/upload the
   GitHub Release assets described below.)

**Image hosting:** Instagram's API requires each image to be fetchable at a
public URL at post-time — it won't accept a direct file upload. Since PNGs
stay out of git (see `.gitignore`), each day's run instead creates (or
reuses) a GitHub Release tagged `daily-posts-{date}` and uploads that day's
3 JPEGs as release assets, using the resulting download URLs as the
`image_url` passed to the Graph API. Releases accumulate over time (not
pruned) — trivial storage cost, not a correctness concern.

**Token lifecycle:** the long-lived access token expires every 60 days and
is **not** auto-refreshed by this project. Regenerate it roughly every
45–50 days and update the `IG_ACCESS_TOKEN` secret. An expired token shows
up as a failed red "Publish to Instagram" step in the Actions tab (and
GitHub emails you on workflow failure by default) — that's your signal it's
time to refresh.

**Local dry-run:** `python3 post_to_instagram.py --dry-run` converts images
to JPEG and really uploads them as GitHub Release assets (so you can verify
hosting/URLs work), but stops before any actual Instagram publish call —
doesn't require `IG_ACCESS_TOKEN`/`IG_USER_ID`.

## Layout

- `generate_posts.py` — image rendering (4 interchangeable news layouts +
  educational layout) + dedup logic
- `run_today.py` — validates and loads `content/{date}.json`, generates the
  day's posts
- `fetch_news.py` — fetches/drafts news content from direct publisher RSS
  feeds, optionally polished via Claude Haiku (see [News Summary
  Enrichment](#news-summary-enrichment))
- `post_to_instagram.py` — publishes the day's posts to Instagram via the
  Graph API (see [Instagram Auto-Posting Setup](#instagram-auto-posting-setup))
- `.github/workflows/daily-posts.yml` — scheduled automation (fetch →
  validate → generate → commit → publish to Instagram → upload artifact)
- `content/` — one JSON file per day, the input to each run
- `posts/` — generated PNGs + captions (gitignored), `publish_log.json`
  (gitignored — per-run Instagram publish results), and `content_log.json`
  (tracked — this is what dedup checks against)
- `fonts/` — bundled Poppins font files (SIL OFL, see `fonts/OFL.txt`)
- `config.json` — your brand/handle (tracked in git — see
  [Configuration](#configuration) for why)
- `config.example.json` — generic template, tracked in git

## Tests

```bash
python3 -m unittest discover tests
```
