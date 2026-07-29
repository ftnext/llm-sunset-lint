# Upstream Markdown sources

This directory contains snapshots of the upstream Markdown documents used to
maintain the model lifecycle data in `src/flake8_llm_sunset/checker.py`.

Run the updater from the repository root:

```console
python scripts/fetch_markdown_sources.py
```

The snapshots are repository maintenance data. They live outside the Python
package and are explicitly excluded from source distributions.

The daily GitHub Actions workflow compares the upstream documents with these
snapshots. When it detects a difference, it opens an issue for a maintainer to
review instead of modifying the repository automatically.
