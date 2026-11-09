---
description: Sweep the repo for documentation drift and fix it
allowed-tools: Read, Glob, Grep, Edit(docs/**/*.md), Edit(README.md), Bash(git diff:*), Bash(git log:*), Bash(git grep:*), Bash(.venv/bin/python -m pytest tests/test_docs*.py:*)
---

Find where docs/ and README.md no longer match the code, and fix the docs. Compare against $ARGUMENTS
if given (a branch, tag or path); otherwise the whole repo against what the docs claim.

1. Run `.venv/bin/python -m pytest -q tests/test_docs.py tests/test_docs_drift.py` for the mechanical drift.
2. Read the pages in docs/ and check their claims against the code: settings in `tmexpenses/` and
   `.env.example`, `scripts/dev.sh` commands, routes and screens in `tmexpenses/templates/`, the
   dashboard in `grafana/`, release steps in `RELEASING.md`, workflows in `.github/workflows/`.
3. Edit Markdown only. Smallest edit, existing voice, Australian English, nothing specific to one
   installation. A new or changed screen gets a `<!-- screenshot: name - what it shows -->` marker, never an image.
4. If the code is what's wrong, don't change it: list it for me.

Finish with what you changed, and what you found but didn't change.
