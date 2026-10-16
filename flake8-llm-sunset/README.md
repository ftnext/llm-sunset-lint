# flake8-llm-sunset

## Install

```console
$ uv tool install flake8 --with flake8-llm-sunset
```

## Usage

```console
% flake8 --select LSG examples/adk_example_fields_output_schema_agent.py
examples/adk_example_fields_output_schema_agent.py:14:11: LSG001 Gemini model 'gemini-2.0-flash' was shut down on 2026-06-01
```

## Model lifecycle data

Gemini and other Google model retirement dates are sourced from Google Cloud's
[Model versions and lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions.md.txt)
and [Google models](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/google-models.md.txt)
Markdown sources.
Models without an announced retirement date are not checked.

Retirement dates are stored in `src/flake8_llm_sunset/model_shutdowns.json`
and bundled with the package. Each model ID maps to a `shutdown_date` in
`YYYY-MM-DD` format and an `earliest` boolean. Set `earliest` to `true` when
the provider says the model may retire "on or after" that date, or `false`
for a fixed retirement date.

To add or update a model, edit this JSON file and run `pytest`. The shared
tests automatically check every entry before the warning window, at the start
of the window, and before, on, and after the retirement date. No model-specific
test needs to be added for a data-only update.

The two upstream Markdown documents are also stored under
`sources/google-cloud/` for maintenance. Run
`python scripts/fetch_markdown_sources.py` to refresh them. These snapshots are
not included in the Python package or its source distribution.

## Error codes

Error-code prefixes identify the model provider:

| Prefix | Provider | Status |
| --- | --- | --- |
| `LSG` | Google | Supported |
| `LSO` | OpenAI | Planned |
| `LSA` | Anthropic | Planned |

| Code | Description |
| --- | --- |
| `LSG001` | A Google model has reached or passed its announced retirement date. |
| `LSG002` | A Google model is within one calendar month of its announced retirement date. |

On the announced retirement date, the diagnostic changes from `LSG002` to
`LSG001`.
