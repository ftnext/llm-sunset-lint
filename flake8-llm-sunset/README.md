# flake8-llm-sunset

## Model lifecycle data

Gemini and other Google model retirement dates are sourced from Google Cloud's
[Model versions and lifecycle (Markdown source)](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions.md.txt).
Models without an announced retirement date are not checked.

## Install

```console
$ uv tool install flake8 --with flake8-llm-sunset
```

## Usage

```console
% flake8 --select LLS examples/adk_example_fields_output_schema_agent.py
examples/adk_example_fields_output_schema_agent.py:14:11: LLS001 Gemini model 'gemini-2.0-flash' was shut down on 2026-06-01
```
