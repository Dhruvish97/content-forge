# ByteAndBull

Generates ByteAndBull's daily Instagram posts: 2 news graphics + 1 educational
graphic, each with a matching caption. Output is written to `posts/`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Daily workflow

1. Create `content/YYYY-MM-DD.json` with today's stories:

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

2. Run it:

   ```bash
   python3 run_today.py            # uses today's date
   python3 run_today.py 2026-05-23 # or generate for a specific date
   ```

`generate_posts.py` checks `posts/content_log.json` and will refuse to
generate (with a warning) if a headline repeats within 7 days, a topic
category has run 3+ days this week, or an educational point is too similar
to one from the last 7 days. Breaking/high-importance news bypasses the
topic-diversity cap.

## Layout

- `generate_posts.py` — image rendering + dedup logic
- `run_today.py` — loads `content/{date}.json` and generates the day's posts
- `content/` — one JSON file per day, the input to each run
- `posts/` — generated PNGs + captions (gitignored) and `content_log.json` (tracked, drives dedup)
- `fonts/` — bundled Poppins font files (SIL OFL, see `fonts/OFL.txt`)

## Tests

```bash
python3 -m unittest discover tests
```
