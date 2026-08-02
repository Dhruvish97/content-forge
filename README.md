# Content Forge

An automated content generation tool for daily social media posts. Point it
at a day's news/educational content and it renders on-brand Instagram-ready
graphics (2 news posts + 1 educational post) with matching captions, while
tracking recent output so it won't repeat headlines or topics.

## Features

- **Image generation** — 1080×1080 Instagram-square graphics in dark or
  gradient styles, built with Pillow (no design tool required).
- **Caption + hashtag generation** — category-aware hashtag sets, ready to
  paste into Instagram.
- **Content deduplication** — refuses to generate if a headline repeats
  within 7 days, a topic category has run 3+ days this week, or an
  educational point is too similar (Jaccard word-overlap) to one from the
  last 7 days. High-importance breaking news bypasses the topic cap.
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

   Output (PNGs + caption `.txt` files) is written to `posts/`.

## Layout

- `generate_posts.py` — image rendering + dedup logic
- `run_today.py` — loads `content/{date}.json` and generates the day's posts
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
