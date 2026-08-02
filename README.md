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
  Google News RSS (no API key required) across tech/markets/earnings/AI/
  crypto, pre-filtered through the same dedup logic as a manual run.
- **Automated daily scheduling** — a GitHub Actions workflow
  (`.github/workflows/daily-posts.yml`) fetches, validates, and generates
  posts daily, entirely in the cloud — no LLM/API calls involved anywhere
  in this pipeline, just Python + Pillow.
- **Config-driven brand** — your account name/handle live in a local,
  gitignored config file, not hardcoded into the tool.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy the example config and set your own brand/handle — this file is
gitignored so your real account details never get committed:

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

**Option A — auto-fetch news (free, no API key):**

```bash
python3 fetch_news.py                        # drafts content/{today}.json from Google News RSS
python3 fetch_news.py --auto-educational      # also fills in educational content from a rotating bank
python3 fetch_news.py 2026-05-23 --force      # target a specific date, overwriting existing news
```

Review/edit the drafted `content/{date}.json` (fetched summaries are often
thin — Google News RSS doesn't provide real article body text), then run it
as below. Or skip the review and use `--auto-educational` for a fully
unattended run (this is what the GitHub Actions workflow does).

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
the repo, and uploads the generated PNGs/captions as a downloadable Actions
artifact (they aren't committed, since `posts/*.png` is gitignored). Trigger
it manually anytime via the Actions tab → "Run workflow".

## Layout

- `generate_posts.py` — image rendering (4 interchangeable news layouts +
  educational layout) + dedup logic
- `run_today.py` — validates and loads `content/{date}.json`, generates the
  day's posts
- `fetch_news.py` — fetches/drafts news content from Google News RSS
- `.github/workflows/daily-posts.yml` — scheduled automation (fetch →
  validate → generate → commit + upload artifact)
- `content/` — one JSON file per day, the input to each run
- `posts/` — generated PNGs + captions (gitignored) and `content_log.json`
  (tracked — this is what dedup checks against)
- `fonts/` — bundled Poppins font files (SIL OFL, see `fonts/OFL.txt`)
- `config.json` — your local brand/handle (gitignored)
- `config.example.json` — generic template, tracked in git

## Tests

```bash
python3 -m unittest discover tests
```
