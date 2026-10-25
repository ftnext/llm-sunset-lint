# Reviewed upstream updates

`update-markdown-sources.yml` refreshes the two Google Cloud source snapshots,
commits them, opens an issue and explicitly dispatches the review workflow.
The classifier keeps the existing `NO_UPDATE` closure behavior. An uncertain
or malformed result fails without closing the issue or generating code.
`NEEDS_UPDATE` uploads a run-bound classifier result and explicitly dispatches
`implement-upstream-markdown-changes.yml` (bot-created issue/push events do not
reliably start another workflow).

## Implementation boundaries

1. **Validate:** require an open, unassigned automation issue, an exact source
   commit link, a source-only single-parent commit on main, unchanged current
   source snapshots, and a successful main-branch review run with its immutable
   `NEEDS_UPDATE` artifact. Pin current main as the implementation base. Existing
   open or closed PRs for the issue/source branch are not updated or reopened.
2. **Generate:** Copilot CLI 1.0.80, explicitly selecting `mai-code-1.1-flash`,
   returns JSONL events with no available tools from an isolated temporary
   directory. The helper extracts exactly one `final_answer` from the selected
   model and requires one successful terminal `result`, with no session errors
   or tool requests/executions. Commentary and reasoning never become candidate
   data. JSONL bypasses the CLI's display rendering and line wrapping; the final
   answer still passes the existing strict JSON and candidate validation.
   Only this step receives the Copilot token. Missing model access, ambiguity or
   invalid output stops the run; there is no fallback model or repair loop.
3. **Test:** the reusable `testing.yml` overlays a validated artifact onto the
   pinned base. All Python 3.10–3.14 jobs must pass. Jobs have no supplied secrets,
   no write permissions and no persisted checkout credentials. This explicit
   matrix is necessary because a `GITHUB_TOKEN` push suppresses push-triggered CI.
4. **Publish:** the trusted base helper rechecks the artifact and provenance,
   current main/source/issue, and all-state PR/branch conflicts. It creates a new
   branch and Draft PR via the GitHub API. It never imports or runs generated
   code, installs dependencies or calls Copilot. It never force-pushes or
   overwrites an existing branch. Main may still advance after the final check;
   the PR always records its exact tested base for human review.

For data-only updates, the model changes `model_shutdowns.json` and returns
`tests/test_checker.py` unchanged in the existing response schema. As established
by PR #30, `test_model_data.py` automatically validates every bundled entry and
checks that all models load; lifecycle behavior uses two fixed fixture models
in `test_checker.py`. No model-specific tests need to be added. The test matrix
still runs the complete existing suite. Changes requiring checker or test
behavior changes are reported as ambiguous for manual handling.
The helper validates dates and boolean earliest flags and renders JSON itself.
`checker.py` is read-only prompt context and is excluded from candidate artifacts.
Existing test structure (allowing factual literal updates) and non-test module
code, including trusted JSON-driven parametrization, are preserved. Unchanged
tests are accepted without requiring a new function. Any optional test edits
still undergo the existing structural checks; new tests remain restricted to a
narrow assertion-only language and literal parametrization. Existing decorators
retain their trusted structure.
The original issue body is not used as a prompt: the validated Git source diff
provides the evidence. Passing tests is a publication gate, not proof that
generated facts or tests are correct; the Draft PR requires review.

## Requirements and failure handling

- The repository owner needs Copilot access to the explicitly selected MAI
  model. GitHub supports `GITHUB_TOKEN` with `copilot-requests: write`; usage in
  a personally owned repository is billed to the owner's Copilot seat. No PAT
  or other credential needs to be created.
- Repository/organization policy must allow GitHub Actions to create pull
  requests. These workflows do not change that setting.
- Review artifacts are retained for seven days. Missing, expired, unsuccessful,
  mismatched or superseded reviews stop implementation.
- Duplicate PRs, including closed PRs, are skipped. An existing branch without a
  matching PR is treated as a conflict, not reclaimed. If PR creation fails
  after branch creation, inspect the branch and repository policy manually;
  reruns will not overwrite it.
- A changed main or newer upstream snapshot stops the run. Dispatch a fresh
  review against the appropriate source commit after checking the open issue.
- Generation, extraction or candidate-validation failures retain raw
  `response.jsonl` and, when extraction succeeded, `response.json` in
  `upstream-generation-response` for three days. Treat them as untrusted data;
  never execute its contents or pass it to the candidate publication path.
  Copilot home/cache and credentials are not included. Parser diagnostics name
  the input, byte count, exception type and available error position without
  printing response excerpts, key names or values.
- Failures remain visible in Actions and leave the issue open for manual work.
  There is no automatic merge, assignment, retry loop, or production updater
  run as part of workflow development.

Official references:
- https://docs.github.com/en/copilot/concepts/agents/copilot-cli/copilot-cli-in-github-actions
- https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli-in-actions
- https://github.blog/changelog/2026-08-11-mai-code-1-1-flash-available-in-github-copilot/
- https://github.com/github/copilot-cli/releases/tag/v1.0.80

Local checks:

```sh
python -m unittest discover -s .github/scripts -p 'test_*.py' -v
python -m pip install './flake8-llm-sunset[test]'
(cd flake8-llm-sunset && pytest -v)
# actionlint 1.7.12 does not yet recognize the documented Copilot permission.
actionlint -ignore 'unknown permission scope "copilot-requests"' .github/workflows/*.yml
```

Live MAI entitlement/inference and the production dispatch chain require the
merged workflows and a genuine reviewed source update. Static tests do not
verify those runtime permissions or incur a live model request.
