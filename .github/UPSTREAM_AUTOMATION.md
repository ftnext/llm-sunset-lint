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
   returns data with no available tools from an isolated temporary directory.
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

The model can change only the two checker tables and `tests/test_checker.py`.
The helper renders table literals itself, preserves all other checker bytes,
retains existing test structure (allowing factual literal updates) and non-test
module code, and requires new regression coverage. New tests use a narrow
assertion-only language. The original issue body is not used as a prompt: the
validated Git source diff provides the evidence. Passing tests is a publication gate, not
proof that generated facts or tests are correct; the Draft PR requires review.

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
