# Content Forge — Project Instructions

This is a **public** GitHub repository. Before every `git push`, review the
changes being pushed (diff/status) and confirm none of the following are
present:

- API keys, tokens, or other secrets (check `.env`-style values, hardcoded
  strings, and anything that should instead be a GitHub Actions secret or
  live only in the gitignored `config.json`)
- Personal information (real name, email, phone number, physical address,
  etc.) beyond what's already intentionally public (e.g. git commit author)
- Local machine file paths that reveal personal directory structure
  (e.g. `/Users/<name>/...`) in code, comments, or committed config

If anything questionable turns up, stop and flag it before pushing rather
than pushing first and fixing after.
