"""Offline security regression tests for the trusted upstream updater.

Run with: python -m unittest discover -s .github/scripts -p 'test_*.py'
All GitHub requests are mocked. Git checks use disposable local repositories.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "upstream_update", Path(__file__).with_name("upstream_update.py")
)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)

CHECKER = '''from datetime import date

# Keep Unicode and every byte outside the two values: café.
MODEL_SHUTDOWNS = {"old-model": date(2026, 10, 20)}
EARLIEST_SHUTDOWNS = set()

class Checker:
    def run(self):
        return MODEL_SHUTDOWNS
'''
TESTS = '''import pytest


def check(value, today):
    return value


def test_existing():
    assert check(1, 0) == 1
'''
NEW_TESTS = TESTS + '''

def test_new_shutdown():
    assert check("new-model", 0) == "new-model"
'''
VALUES = {
    "repository": "example/project",
    "issue_number": "17",
    "source_sha": "1" * 40,
    "review_run_id": "29",
}
BASE = "2" * 40
MODEL = "mai-code-1.1-flash"


def response(**changes):
    value = {
        "status": "UPDATE",
        "model_shutdowns": {"old-model": "2026-10-20", "new-model": "2027-02-28"},
        "earliest_shutdowns": ["new-model"],
        "tests": NEW_TESTS,
        "summary": "Add the newly documented shutdown date.",
    }
    value.update(changes)
    return value


def provenance(values=None, base=BASE):
    values = dict(values or VALUES)
    return {
        "schema_version": 1, **values, "base_sha": base,
        "run_attempt": "2", "branch": updater.branch_name(values),
    }


def issue(values=None):
    values = values or VALUES
    return {
        "state": "open", "title": updater.ISSUE_TITLE,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "assignees": [],
        "body": f"Sources changed.\n- Commit: https://github.com/{values['repository']}/commit/{values['source_sha']}\n",
    }


def run(values=None, base=BASE):
    values = values or VALUES
    return {
        "id": int(values["review_run_id"]),
        "repository": {"full_name": values["repository"]},
        "head_repository": {"full_name": values["repository"]},
        "path": updater.REVIEW_WORKFLOW, "event": "workflow_dispatch",
        "head_branch": "main", "head_sha": base,
        "run_attempt": 2, "status": "completed", "conclusion": "success",
    }


def manifest(values=None, base=BASE):
    values = values or VALUES
    return {
        "schema_version": 1, **values, "run_attempt": "2",
        "review_head_sha": base, "decision": "NEEDS_UPDATE",
    }


def artifact_metadata(values=None, base=BASE):
    values = values or VALUES
    return {
        "id": 51, "name": "classifier-result-2", "expired": False,
        "size_in_bytes": 1000,
        "workflow_run": {"id": int(values["review_run_id"]), "head_sha": base},
    }


def archive(value=None, *, names=("classifier-result.json",), mode=0o100644):
    result = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(result, "w") as bundle:
            for name in names:
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = mode << 16
                bundle.writestr(entry, json.dumps(manifest() if value is None else value))
    return result.getvalue()


def pull(values=None, state="open"):
    values = values or VALUES
    return {
        "state": state, "draft": True,
        "html_url": f"https://github.com/{values['repository']}/pull/42",
        "head": {"ref": updater.branch_name(values), "repo": {"full_name": values["repository"]}},
    }


class InputTests(unittest.TestCase):
    def test_environment_accepts_exact_identifiers(self):
        with patch.dict(os.environ, {
            "ISSUE_NUMBER": "17", "SOURCE_SHA": VALUES["source_sha"],
            "REVIEW_RUN_ID": "29", "GITHUB_REPOSITORY": "example/project",
        }, clear=True):
            self.assertEqual(updater.environment(), VALUES)

    def test_environment_rejects_malformed_or_missing_inputs(self):
        valid = {"ISSUE_NUMBER": "17", "SOURCE_SHA": "1" * 40,
                 "REVIEW_RUN_ID": "29", "GITHUB_REPOSITORY": "example/project"}
        for key, invalids in {
            "ISSUE_NUMBER": [None, "", "0", "-1", "17\n", "1; echo bad", "1" * 21],
            "SOURCE_SHA": [None, "main", "1" * 39, "A" * 40, "1" * 40 + "\n"],
            "REVIEW_RUN_ID": [None, "0", "2.0", "2/attempts/1"],
            "GITHUB_REPOSITORY": [None, "owner", "owner/repo/extra", "owner/repo?x=1", "owner/repo\n"],
        }.items():
            for value in invalids:
                with self.subTest(key=key, value=value):
                    env = {**valid, key: value} if value is not None else {k: v for k, v in valid.items() if k != key}
                    with patch.dict(os.environ, env, clear=True), self.assertRaises(updater.InvalidUpdate):
                        updater.environment()

    def test_provenance_is_exact_and_not_boolean_schema(self):
        self.assertEqual(updater.validate_provenance(provenance()), provenance())
        for changes in ({"extra": "x"}, {"schema_version": True}, {"run_attempt": "0"},
                        {"base_sha": "main"}, {"branch": "main"}):
            with self.subTest(changes=changes), self.assertRaises(updater.InvalidUpdate):
                updater.validate_provenance({**provenance(), **changes})

    def test_json_rejects_duplicate_nonfinite_trailing_or_invalid_utf8(self):
        for raw in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{} {}', b'"\xff"'):
            with self.subTest(raw=raw), self.assertRaises(updater.InvalidUpdate):
                updater.strict_json(raw)
        self.assertEqual(updater.strict_json('{"x": 1}'), {"x": 1})

    def test_model_must_be_configured_and_well_formed(self):
        for value in (None, "", "model\n", "model;exec"):
            env = {} if value is None else {"UPDATER_MODEL": value}
            with self.subTest(value=value), patch.dict(os.environ, env, clear=True), self.assertRaises(updater.InvalidUpdate):
                updater.configured_model()
        with patch.dict(os.environ, {"UPDATER_MODEL": MODEL}):
            self.assertEqual(updater.configured_model(), MODEL)

    def test_cli_argument_contract(self):
        cases = {
            "validate": ["--output", "provenance.json"],
            "prepare": ["--provenance", "provenance.json", "--output", "prompt.txt"],
            "build": ["--provenance", "provenance.json", "--response", "response.json", "--artifact", "artifact"],
            "apply": ["--artifact", "artifact"], "publish": ["--artifact", "artifact"],
        }
        for command, args in cases.items():
            with self.subTest(command=command), patch.object(updater, f"command_{command}") as handler:
                self.assertEqual(updater.main([command, *args]), 0)
                handler.assert_called_once()
            with self.subTest(missing=command), patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
                updater.main([command])


class GitHubValidationTests(unittest.TestCase):
    def test_issue_requires_exact_identity_title_commit_open_and_unassigned(self):
        with patch.object(updater, "api", return_value=issue()):
            updater.validate_issue(VALUES)
        bad = [
            {"state": "closed"}, {"title": updater.ISSUE_TITLE + " "},
            {"user": {"login": "github-actions[bot]", "type": "User"}},
            {"user": {"login": "other-bot", "type": "Bot"}},
            {"assignees": [{"login": "human"}]}, {"pull_request": {}},
            {"body": issue()["body"].replace("1" * 40, "3" * 40)},
            {"body": issue()["body"].replace("- Commit:", "Commit:")},
            {"body": issue()["body"] * 2},
            {"body": issue()["body"].replace("\n- Commit:", "\n - Commit:")},
            {"body": None},
        ]
        for changes in bad:
            with self.subTest(changes=changes), patch.object(updater, "api", return_value={**issue(), **changes}), self.assertRaises(updater.InvalidUpdate):
                updater.validate_issue(VALUES)

    def test_review_metadata_and_success_are_bound_to_exact_run(self):
        with patch.object(updater, "api", return_value=run()):
            self.assertEqual(updater.completed_review(VALUES, BASE), run())
        for key, wrong in {
            "id": 30, "repository": {"full_name": "other/repo"},
            "head_repository": {"full_name": "fork/project"}, "path": "other.yml",
            "event": "pull_request", "head_branch": "feature", "head_sha": "3" * 40,
            "run_attempt": 0, "conclusion": "failure", "status": "invalid",
        }.items():
            with self.subTest(key=key), patch.object(updater, "api", return_value={**run(), key: wrong}), self.assertRaises(updater.InvalidUpdate):
                updater.completed_review(VALUES, BASE)

    def test_review_waits_for_same_run_then_times_out_closed(self):
        with patch.object(updater, "api", side_effect=[{**run(), "status": "in_progress"}, run()]), patch.object(updater.time, "sleep") as sleep:
            self.assertEqual(updater.completed_review(VALUES, BASE)["conclusion"], "success")
            sleep.assert_called_once_with(3)
        with patch.object(updater, "api", return_value={**run(), "status": "in_progress"}), patch.object(updater.time, "monotonic", side_effect=[0, 61]), self.assertRaises(updater.InvalidUpdate):
            updater.completed_review(VALUES, BASE)

    def classifier(self, *, metadata=None, bundle=None):
        listing = [artifact_metadata()] if metadata is None else metadata
        with patch.object(updater, "api", side_effect=[{"artifacts": listing}, archive() if bundle is None else bundle]):
            return updater.classifier_manifest(VALUES, BASE, run())

    def test_classifier_requires_one_current_attempt_artifact(self):
        self.assertEqual(self.classifier(), "2")
        for metadata in ([], [artifact_metadata(), artifact_metadata()],
                         [{**artifact_metadata(), "name": "classifier-result-1"}],
                         [{**artifact_metadata(), "expired": True}],
                         [{**artifact_metadata(), "size_in_bytes": updater.MAX_BYTES + 1}],
                         [{**artifact_metadata(), "workflow_run": {"id": 30, "head_sha": BASE}}],
                         [{**artifact_metadata(), "workflow_run": {"id": 29, "head_sha": "3" * 40}}]):
            with self.subTest(metadata=metadata), self.assertRaises(updater.InvalidUpdate):
                self.classifier(metadata=metadata)

    def test_classifier_manifest_binds_every_authorization_field(self):
        for key, wrong in {
            "schema_version": True, "repository": "fork/project", "review_run_id": "30",
            "run_attempt": "1", "review_head_sha": "3" * 40, "issue_number": "18",
            "source_sha": "4" * 40, "decision": "NO_UPDATE",
        }.items():
            with self.subTest(key=key), self.assertRaises(updater.InvalidUpdate):
                self.classifier(bundle=archive({**manifest(), key: wrong}))
        with self.assertRaises(updater.InvalidUpdate):
            self.classifier(bundle=archive({**manifest(), "extra": True}))

    def test_classifier_zip_rejects_duplicate_traversal_symlink_executable(self):
        bundles = [b"not zip", archive(names=("classifier-result.json", "classifier-result.json")),
                   archive(names=("../classifier-result.json",)), archive(names=("classifier-result.json", "extra.txt")),
                   archive(mode=stat.S_IFLNK | 0o777), archive(mode=stat.S_IFREG | 0o755),
                   archive(names=("classifier-result.json/",)), archive({"large": "x" * 20001})]
        for number, bundle in enumerate(bundles):
            with self.subTest(bundle=number), self.assertRaises(updater.InvalidUpdate):
                self.classifier(bundle=bundle)

    def test_duplicate_open_closed_or_merged_pr_is_terminal(self):
        for state in ("open", "closed"):
            existing = pull(state=state)
            if state == "closed":
                existing["merged_at"] = "2026-10-01T00:00:00Z"
            with self.subTest(state=state), patch.object(updater, "api", return_value=[existing]) as api:
                self.assertEqual(updater.duplicate(VALUES), existing)
                self.assertEqual(api.call_count, 1)
                self.assertIn("state=all", api.call_args.args[0])

    def test_duplicate_ignores_fork_and_ref_without_pr_conflicts(self):
        other = pull()
        other["head"]["repo"]["full_name"] = "fork/project"
        with patch.object(updater, "api", side_effect=[[other], updater.APIError("absent", 404)]):
            self.assertIsNone(updater.duplicate(VALUES))
        with patch.object(updater, "api", side_effect=[[], {"ref": "exists"}]), self.assertRaises(updater.InvalidUpdate):
            updater.duplicate(VALUES)
        with patch.object(updater, "api", side_effect=[[], updater.APIError("forbidden", 403)]), self.assertRaises(updater.APIError):
            updater.duplicate(VALUES)


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.original = {updater.CHECKER: CHECKER, updater.TESTS: TESTS}

    def test_reconstruction_only_replaces_literal_values_not_executable_code(self):
        generated = updater.reconstruct(self.original, response())
        self.assertEqual(updater.read_tables(generated[updater.CHECKER]),
                         (response()["model_shutdowns"], {"new-model"}))
        def masked(source):
            raw = source.encode("utf-8")
            spans = [updater.byte_span(source, node) for node in updater.table_nodes(source).values()]
            for start, end in sorted(spans, reverse=True):
                raw = raw[:start] + b"<TABLE>" + raw[end:]
            return raw
        self.assertEqual(masked(generated[updater.CHECKER]), masked(CHECKER))
        self.assertEqual(generated[updater.TESTS], NEW_TESTS)
        self.assertEqual(set(generated), set(updater.FILES))

    def test_response_ambiguous_unknown_fields_bad_dates_and_subset_fail(self):
        for changes in (
            {"status": "AMBIGUOUS"}, {"status": "NO_UPDATE"}, {"checker": "print('bad')"},
            {"model_shutdowns": {}}, {"model_shutdowns": {"x": "2027-02-29"}},
            {"model_shutdowns": {"x": "2027-2-28"}}, {"model_shutdowns": {"x": 20270228}},
            {"model_shutdowns": {"x\"; evil()": "2027-02-28"}},
            {"earliest_shutdowns": ["missing"]}, {"earliest_shutdowns": ["new-model", "new-model"]},
            {"earliest_shutdowns": "new-model"}, {"earliest_shutdowns": [True]},
            {"model_shutdowns": {"old-model": "2026-10-20"}, "earliest_shutdowns": []},
        ):
            with self.subTest(changes=changes), self.assertRaises(updater.InvalidUpdate):
                updater.reconstruct(self.original, response(**changes))

    def test_checker_tables_cannot_contain_calls_unpacking_duplicates_or_bad_dates(self):
        for table in ('dict(x=date(2027, 1, 1))', '{**other}', '{"x": evil()}',
                      '{"x": date(True, 1, 1)}', '{"x": date(2027, 2, 29)}',
                      '{"x": date(2027, 1, 1), "x": date(2027, 2, 1)}'):
            source = f"MODEL_SHUTDOWNS = {table}\nEARLIEST_SHUTDOWNS = set()\n"
            with self.subTest(table=table), self.assertRaises(updater.InvalidUpdate):
                updater.read_tables(source)
        for earliest in ('set(["old-model"])', '{"missing"}', '{"old-model", "old-model"}'):
            with self.subTest(earliest=earliest), self.assertRaises(updater.InvalidUpdate):
                updater.read_tables(CHECKER.replace("set()", earliest))
        with self.assertRaises(updater.InvalidUpdate):
            updater.read_tables(CHECKER + '\nMODEL_SHUTDOWNS = {}\n')

    def test_tests_preserve_existing_names_and_non_test_code_add_assertions(self):
        updater.validate_tests(TESTS, NEW_TESTS)
        for candidate in (
            TESTS, NEW_TESTS.replace("test_existing", "test_renamed"),
            NEW_TESTS.replace("import pytest", "import os"),
            NEW_TESTS.replace("return value", "return None"),
            NEW_TESTS.replace("assert check(1, 0) == 1", "pass"),
            NEW_TESTS + '\nprint("side effect")\n',
            NEW_TESTS + '\ndef test_existing():\n    assert True\n',
        ):
            with self.subTest(candidate=candidate), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, candidate)

    def test_tests_reject_execution_at_collection_and_coverage_disabling(self):
        for header, body in (
            ('@evil()\ndef test_new_shutdown():', 'assert check(1, 0) == 1'),
            ('def test_new_shutdown(x=evil()):', 'assert check(1, 0) == 1'),
            ('def test_new_shutdown(x: evil()):', 'assert check(1, 0) == 1'),
            ('def test_new_shutdown() -> evil():', 'assert check(1, 0) == 1'),
            ('@pytest.mark.parametrize("x", evil())\ndef test_new_shutdown(x):', 'assert check(x, 0)'),
            ('@pytest.mark.skip\ndef test_new_shutdown():', 'assert check(1, 0) == 1'),
            ('def test_new_shutdown():', 'pytest.skip("disabled")\n    assert check(1, 0) == 1'),
            ('def test_new_shutdown():', 'pytest.xfail("disabled")\n    assert check(1, 0) == 1'),
        ):
            candidate = TESTS + f"\n\n{header}\n    {body}\n"
            with self.subTest(header=header, body=body), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, candidate)
        updater.validate_tests(TESTS, TESTS + '\n\n@pytest.mark.parametrize("x", [1, 2], ids=["one", "two"])\ndef test_new_shutdown(x):\n    assert check(x, 0)\n')

    def test_existing_test_structure_is_immutable_except_factual_literals(self):
        updater.validate_tests(TESTS, NEW_TESTS.replace("assert check(1, 0) == 1", "assert check(2, 0) == 2"))
        for replacement in (
            "assert check(1, 0) == True", "assert check(1, 0) != 1", "assert other(1) == 1",
            "assert True", "return\n    assert check(1, 0) == 1",
            "if False:\n        assert check(1, 0) == 1", "assert check(1, 0) == 1 or True",
        ):
            with self.subTest(replacement=replacement), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, NEW_TESTS.replace("assert check(1, 0) == 1", replacement))

    def test_new_tests_assert_checker_results_instead_of_vacuous_truths(self):
        for body in ("assert True", "assert 1 == 1", "check(1, 0)\n    assert True"):
            with self.subTest(body=body), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, TESTS + f"\n\ndef test_new_shutdown():\n    {body}\n")
        updater.validate_tests(TESTS, TESTS + '\n\ndef test_new_shutdown():\n    errors = check(1, 0)\n    assert errors == 1\n')

    def test_new_tests_cannot_exit_mutate_import_or_request_arbitrary_fixtures(self):
        for body in (
            'import os\n    os._exit(0)', 'os._exit(0)', 'return', 'global check',
            'check = 1', '__name__ = "changed"', 'value = __import__("os")',
            'errors = check(1, 0)\n    errors[0] = 1',
            'while True:\n        pass', 'value = open("file", "w")',
        ):
            candidate = TESTS + f'\n\ndef test_new_shutdown():\n    {body}\n    assert check(1, 0) == 1\n'
            with self.subTest(body=body), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, candidate)
        for header in (
            'def test_new_shutdown(monkeypatch):',
            '@pytest.mark.parametrize("x", [])\ndef test_new_shutdown(x):',
            '@pytest.mark.parametrize("x", [1], indirect=True)\ndef test_new_shutdown(x):',
            '@pytest.mark.parametrize("check", [1])\ndef test_new_shutdown(check):',
        ):
            candidate = TESTS + f'\n\n{header}\n    assert check(1, 0) == 1\n'
            with self.subTest(header=header), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, candidate)

    def test_new_test_supports_realistic_checker_date_and_message_assertions(self):
        candidate = TESTS + '''

def test_new_shutdown():
    errors = check('MODEL = "new-model"', date(2027, 2, 28))
    assert len(errors) == 1
    assert errors[0][2].startswith("LSG001")
    assert errors[0][2].endswith("2027-02-28")
'''
        updater.validate_tests(TESTS, candidate)

    def test_non_utf8_encoding_cookie_cannot_hide_executable_code(self):
        for prefix in ('# coding: utf-7\n# +AAo-BYPASS = 1\n', '# coding: latin-1\n', '# coding: unknown-encoding\n'):
            with self.subTest(prefix=prefix), self.assertRaises(updater.InvalidUpdate):
                updater.validate_tests(TESTS, prefix + NEW_TESTS)
        updater.validate_tests(TESTS, '# coding: utf-8\n' + NEW_TESTS)

    def test_reconstruction_preserves_order_and_unchanged_table_bytes(self):
        changed = updater.reconstruct(self.original, response(earliest_shutdowns=[]))[updater.CHECKER]
        self.assertIn("EARLIEST_SHUTDOWNS = set()", changed)
        self.assertLess(changed.index('"old-model"'), changed.index('"new-model"'))
        changed = updater.reconstruct(self.original, response(
            model_shutdowns={"old-model": "2026-10-20"}, earliest_shutdowns=["old-model"],
        ))[updater.CHECKER]
        self.assertIn('MODEL_SHUTDOWNS = {"old-model": date(2026, 10, 20)}', changed)


class RepositoryCase(unittest.TestCase):
    """Each test gets a small real Git history and no network-capable API."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="upstream-updater-test-")
        self.addCleanup(self.directory.cleanup)
        previous = os.getcwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, previous)
        self.env = patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Offline Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Offline Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "UPDATER_MODEL": MODEL,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git("init", "-q", "--initial-branch=main")
        for path, contents in {
            updater.CHECKER: CHECKER, updater.TESTS: TESTS,
            updater.SOURCES[0]: "Original source A\n", updater.SOURCES[1]: "Original source B\n",
            ".github/prompts/implement-upstream-update.md": "Generate strict JSON from the diff.\n",
            "README.md": "Unchanged trusted file.\n",
        }.items():
            self.write(path, contents)
        self.initial = self.commit("Initial trusted files")
        self.write(updater.SOURCES[0], "New source A\n")
        self.source = self.commit("Update source A")
        self.base = self.source
        self.values = {**VALUES, "source_sha": self.source}
        self.provenance = provenance(self.values, self.base)
        self.dispatch = patch.dict(os.environ, {
            "ISSUE_NUMBER": self.values["issue_number"], "SOURCE_SHA": self.source,
            "REVIEW_RUN_ID": self.values["review_run_id"], "GITHUB_REPOSITORY": self.values["repository"],
            "GITHUB_OUTPUT": str(Path("outputs.txt").absolute()),
            "GITHUB_STEP_SUMMARY": str(Path("summary.md").absolute()),
        })
        self.dispatch.start()
        self.addCleanup(self.dispatch.stop)
        self.api = patch.object(updater, "api", side_effect=self.fake_api).start()
        self.addCleanup(patch.stopall)
        self.main_responses = []
        self.pulls_responses = []

    def git(self, *args):
        completed = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
        return completed.stdout.strip()

    def write(self, path, text):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def fake_api(self, endpoint, *, method="GET", data=None, binary=False):
        prefix = f"repos/{self.values['repository']}"
        if method == "GET":
            if endpoint == f"{prefix}/git/ref/heads/main":
                sha = self.main_responses.pop(0) if self.main_responses else self.base
                return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": sha}}
            if endpoint == f"{prefix}/issues/17":
                return issue(self.values)
            if endpoint == f"{prefix}/actions/runs/29":
                return run(self.values, self.base)
            if endpoint == f"{prefix}/actions/runs/29/artifacts?per_page=100&page=1":
                return {"artifacts": [artifact_metadata(self.values, self.base)]}
            if endpoint == f"{prefix}/actions/artifacts/51/zip":
                self.assertTrue(binary)
                return archive(manifest(self.values, self.base))
            if endpoint.startswith(f"{prefix}/pulls?"):
                return self.pulls_responses.pop(0) if self.pulls_responses else []
            if endpoint == f"{prefix}/git/ref/heads/{self.provenance['branch']}":
                raise updater.APIError("not found", 404)
            if endpoint == f"{prefix}/git/commits/{self.base}":
                return {"tree": {"sha": "3" * 40}}
        if method == "POST":
            if endpoint.endswith("/git/blobs"):
                return {"sha": "4" * 40}
            if endpoint.endswith("/git/trees"):
                return {"sha": "5" * 40}
            if endpoint.endswith("/git/commits"):
                return {"sha": "6" * 40}
            if endpoint.endswith("/git/refs"):
                return {"ref": data["ref"], "object": {"sha": data["sha"]}}
            if endpoint == f"{prefix}/pulls":
                created = pull(self.values)
                created["head"]["sha"] = "6" * 40
                created["base"] = {"ref": "main", "sha": self.base}
                return created
        self.fail(f"Unexpected API call: {method} {endpoint}")

    def build(self, model_response=None):
        updater.write_json("provenance.json", self.provenance)
        updater.write_json("response.json", model_response or response())
        updater.command_build(SimpleNamespace(provenance="provenance.json", response="response.json", artifact="artifact"))
        return json.loads(Path("artifact/candidate.json").read_text())

    def posts(self):
        return [call for call in self.api.call_args_list if call.kwargs.get("method") == "POST"]


class SourceTests(RepositoryCase):
    def test_source_on_main_and_unchanged_in_later_main_commits(self):
        updater.validate_source(self.source, self.base)
        self.write("README.md", "Unrelated later documentation.\n")
        later = self.commit("Later main commit")
        updater.validate_source(self.source, later)

    def test_source_must_have_exactly_one_parent(self):
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(self.initial, self.base)
        self.git("checkout", "-q", "-b", "side", self.initial)
        self.write("side.txt", "side\n")
        self.commit("Side branch")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-q", "side", "-m", "Merge")
        merged = self.git("rev-parse", "HEAD")
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(merged, merged)

    def test_source_must_be_ancestor_of_main(self):
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(self.source, self.initial)

    def test_source_must_change_only_allowlisted_sources(self):
        self.write(updater.SOURCES[1], "Changed B\n")
        self.write("README.md", "Unexpected change\n")
        mixed = self.commit("Mixed changes")
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(mixed, mixed)
        self.git("commit", "--allow-empty", "-q", "-m", "No changes")
        empty = self.git("rev-parse", "HEAD")
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(empty, empty)

    def test_source_rejects_changed_snapshot_on_main(self):
        self.write(updater.SOURCES[1], "Newer source B\n")
        later = self.commit("Superseding source snapshot")
        with self.assertRaises(updater.InvalidUpdate):
            updater.validate_source(self.source, later)

    def test_source_rejects_symlink_executable_deletion_and_repair(self):
        for mutation in ("symlink", "executable", "deleted"):
            with self.subTest(mutation=mutation):
                self.git("reset", "--hard", "-q", self.source)
                path = Path(updater.SOURCES[0])
                if mutation == "symlink":
                    path.unlink()
                    path.symlink_to("model-versions.md")
                elif mutation == "executable":
                    path.chmod(0o755)
                else:
                    path.unlink()
                bad = self.commit("Invalid source file mode")
                with self.assertRaises(updater.InvalidUpdate):
                    updater.validate_source(bad, bad)
                if mutation != "deleted":
                    path.unlink()
                    self.write(str(path), "Repaired regular file\n")
                    repaired = self.commit("Repair invalid source mode")
                    with self.assertRaises(updater.InvalidUpdate):
                        updater.validate_source(repaired, repaired)

    def test_validate_and_prepare_emit_pinned_provenance_and_delimited_diff(self):
        with patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(updater.main(["validate", "--output", "validated.json"]), 0)
        self.assertEqual(updater.read_json("validated.json"), self.provenance)
        self.assertIn("skip=false", Path("outputs.txt").read_text())
        self.assertEqual(updater.main(["prepare", "--provenance", "validated.json", "--output", "prompt.txt"]), 0)
        prompt = Path("prompt.txt").read_text()
        self.assertIn("<TRUSTED_CHECKER>\n" + CHECKER, prompt)
        self.assertIn("<TRUSTED_TESTS>\n" + TESTS, prompt)
        self.assertIn("<UNTRUSTED_UPSTREAM_DIFF>", prompt)
        self.assertIn("+New source A", prompt)
        self.assertEqual(self.posts(), [])

    def test_validate_existing_closed_pr_skips_stale_live_checks(self):
        self.pulls_responses = [[pull(self.values, "closed")]]
        with patch.object(updater, "validate_live", side_effect=AssertionError("Closed PR is terminal")), patch("sys.stdout", new=io.StringIO()):
            updater.command_validate(SimpleNamespace(output="provenance.json"))
        self.assertIn("skip=true", Path("outputs.txt").read_text())
        self.assertFalse(Path("provenance.json").exists())
        self.assertEqual(self.posts(), [])


class ArtifactTests(RepositoryCase):
    def test_build_load_apply_changes_exactly_two_files(self):
        candidate = self.build()
        self.assertEqual(updater.load_candidate("artifact"), candidate)
        self.assertEqual(candidate["model"], MODEL)
        self.assertEqual(set(candidate["files"]), set(updater.FILES))
        updater.command_apply(SimpleNamespace(artifact="artifact"))
        changed = set(self.git("diff", "--name-only").splitlines())
        self.assertEqual(changed, set(updater.FILES))
        self.assertEqual(Path("README.md").read_text(), "Unchanged trusted file.\n")
        for path in updater.FILES:
            self.assertEqual(Path(path).read_text(), candidate["files"][path]["content"])
        self.assertEqual(self.posts(), [])

    def test_artifact_rejects_content_hash_base_hash_model_and_extra_paths(self):
        original = self.build()
        mutations = [
            lambda c: c["files"][updater.CHECKER].update(content=CHECKER + "\nprint('bad')\n"),
            lambda c: c["files"][updater.CHECKER].update(sha256="0" * 64),
            lambda c: c["files"][updater.CHECKER].update(content="bad", sha256=updater.sha256("bad")),
            lambda c: c["files"][updater.TESTS].update(base_sha256="0" * 64),
            lambda c: c["response"]["model_shutdowns"].update({"new-model": "2027-03-01"}),
            lambda c: c["files"].update({"README.md": {"content": "bad"}}),
            lambda c: c.update(model="other-model"),
            lambda c: c.update(extra=True),
            lambda c: c["provenance"].update(base_sha="7" * 40),
        ]
        for number, mutation in enumerate(mutations):
            candidate = deepcopy(original)
            mutation(candidate)
            updater.write_json("artifact/candidate.json", candidate)
            with self.subTest(mutation=number), self.assertRaises(updater.InvalidUpdate):
                updater.load_candidate("artifact")

    def test_artifact_rejects_extra_files_symlinks_and_executable_manifest(self):
        self.build()
        self.write("artifact/extra.txt", "extra")
        with self.assertRaises(updater.InvalidUpdate):
            updater.load_candidate("artifact")
        Path("artifact/extra.txt").unlink()
        candidate = Path("artifact/candidate.json")
        candidate.chmod(0o755)
        with self.assertRaises(updater.InvalidUpdate):
            updater.load_candidate("artifact")
        candidate.chmod(0o644)
        candidate.rename("saved.json")
        candidate.symlink_to(Path("saved.json").absolute())
        with self.assertRaises(updater.InvalidUpdate):
            updater.load_candidate("artifact")
        candidate.unlink()
        Path("saved.json").rename(candidate)
        Path("artifact-link").symlink_to(Path("artifact").absolute(), target_is_directory=True)
        with self.assertRaises(updater.InvalidUpdate):
            updater.load_candidate("artifact-link")

    def test_apply_preflights_both_files_before_writing(self):
        self.build()
        self.write(updater.TESTS, TESTS + "\n# Local edit\n")
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_apply(SimpleNamespace(artifact="artifact"))
        self.assertEqual(Path(updater.CHECKER).read_text(), CHECKER)
        self.assertIn("Local edit", Path(updater.TESTS).read_text())

    def test_apply_rejects_worktree_executable_or_symlink(self):
        self.build()
        path = Path(updater.TESTS)
        path.chmod(0o755)
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_apply(SimpleNamespace(artifact="artifact"))
        path.chmod(0o644)
        path.rename("saved-tests.py")
        path.symlink_to(Path("saved-tests.py").absolute())
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_apply(SimpleNamespace(artifact="artifact"))
        self.assertEqual(Path(updater.CHECKER).read_text(), CHECKER)

    def test_build_never_overwrites_nonempty_artifact(self):
        self.build()
        previous = Path("artifact/candidate.json").read_bytes()
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_build(SimpleNamespace(provenance="provenance.json", response="response.json", artifact="artifact"))
        self.assertEqual(Path("artifact/candidate.json").read_bytes(), previous)


class PublishTests(RepositoryCase):
    def test_publish_uses_api_only_and_never_executes_generated_tests(self):
        candidate = self.build()
        original_run = subprocess.run
        commands = []
        def only_read_git(command, *args, **kwargs):
            commands.append(command)
            self.assertEqual(command[0], "git")
            self.assertNotIn("push", command)
            self.assertNotIn("checkout", command)
            self.assertNotIn("commit", command)
            return original_run(command, *args, **kwargs)
        with patch.object(updater.subprocess, "run", side_effect=only_read_git), patch("builtins.exec", side_effect=AssertionError("Generated code must not execute")), patch("builtins.eval", side_effect=AssertionError("Generated code must not be evaluated")), patch("sys.stdout", new=io.StringIO()):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertTrue(commands)
        self.assertEqual(Path(updater.TESTS).read_text(), TESTS)
        posts = self.posts()
        self.assertEqual(len(posts), 6)
        blobs = [call.kwargs["data"] for call in posts if call.args[0].endswith("/blobs")]
        self.assertEqual({base64.b64decode(blob["content"]).decode() for blob in blobs},
                         {candidate["files"][path]["content"] for path in updater.FILES})
        tree = next(call.kwargs["data"] for call in posts if call.args[0].endswith("/trees"))
        self.assertEqual({entry["path"] for entry in tree["tree"]}, set(updater.FILES))
        self.assertTrue(all(entry["mode"] == "100644" for entry in tree["tree"]))
        commit = next(call.kwargs["data"] for call in posts if call.args[0].endswith("/commits"))
        self.assertEqual(commit["parents"], [self.base])
        ref = next(call.kwargs["data"] for call in posts if call.args[0].endswith("/refs"))
        self.assertEqual(ref["ref"], "refs/heads/" + self.provenance["branch"])
        pr = next(call.kwargs["data"] for call in posts if call.args[0].endswith("/pulls"))
        self.assertTrue(pr["draft"])
        self.assertEqual(pr["base"], "main")
        for required in ("Closes #17", self.source, "/actions/runs/29", self.base, MODEL):
            self.assertIn(required, pr["body"])
        self.assertIn("pr_url=https://github.com/example/project/pull/42", Path("outputs.txt").read_text())

    def test_publish_initial_main_race_fails_before_mutation(self):
        self.build()
        self.main_responses = ["7" * 40]
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertEqual(self.posts(), [])

    def test_publish_pre_ref_main_race_leaves_no_visible_branch_or_pr(self):
        self.build()
        self.main_responses = [self.base, "7" * 40]
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertFalse(any(call.args[0].endswith(("/refs", "/pulls")) for call in self.posts()))

    def test_publish_post_ref_main_race_does_not_open_pr(self):
        self.build()
        self.main_responses = [self.base, self.base, "7" * 40]
        with self.assertRaises(updater.InvalidUpdate):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertTrue(any(call.args[0].endswith("/refs") for call in self.posts()))
        self.assertFalse(any(call.args[0].endswith("/pulls") for call in self.posts()))

    def test_publish_duplicate_open_or_closed_skips_every_write(self):
        self.build()
        for state in ("open", "closed"):
            self.api.reset_mock()
            self.pulls_responses = [[pull(self.values, state)]]
            with self.subTest(state=state), patch.object(updater, "validate_live", side_effect=AssertionError("Existing PR is terminal")), patch("sys.stdout", new=io.StringIO()):
                updater.command_publish(SimpleNamespace(artifact="artifact"))
            self.assertEqual(self.posts(), [])
        self.assertIn("skip=true", Path("outputs.txt").read_text())

    def test_publish_duplicate_race_before_ref_creation_skips_branch(self):
        self.build()
        self.pulls_responses = [[], [], [pull(self.values)]]
        with patch("sys.stdout", new=io.StringIO()):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertFalse(any(call.args[0].endswith(("/refs", "/pulls")) for call in self.posts()))

    def test_publish_dispatch_or_review_attempt_tamper_fails(self):
        self.build()
        with patch.dict(os.environ, {"ISSUE_NUMBER": "18"}), self.assertRaises(updater.InvalidUpdate):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertEqual(self.posts(), [])
        with patch.object(updater, "validate_live", return_value={**self.provenance, "run_attempt": "3"}), self.assertRaises(updater.InvalidUpdate):
            updater.command_publish(SimpleNamespace(artifact="artifact"))
        self.assertEqual(self.posts(), [])

    def test_publish_422_branch_and_pr_races_report_existing_pr_without_retry(self):
        self.build()
        for suffix in ("/git/refs", "/pulls"):
            self.api.reset_mock()
            self.pulls_responses = [[], [], [], [pull(self.values)]]
            def conflict(endpoint, *, method="GET", data=None, binary=False):
                if method == "POST" and endpoint.endswith(suffix):
                    raise updater.APIError("already exists", 422)
                return self.fake_api(endpoint, method=method, data=data, binary=binary)
            self.api.side_effect = conflict
            with self.subTest(endpoint=suffix), patch("sys.stdout", new=io.StringIO()):
                updater.command_publish(SimpleNamespace(artifact="artifact"))
            self.assertEqual(sum(call.args[0].endswith(suffix) for call in self.posts()), 1)
            if suffix == "/git/refs":
                self.assertFalse(any(call.args[0].endswith("/pulls") for call in self.posts()))
        self.assertIn("skip=true", Path("outputs.txt").read_text())

    def test_publish_rejects_created_pr_with_unexpected_head_base_or_draft_status(self):
        self.build()
        for key, value in (
            ("draft", False),
            ("head", {"sha": "7" * 40, "ref": self.provenance["branch"]}),
            ("head", {"sha": "6" * 40, "ref": "wrong-branch"}),
            ("base", {"sha": "7" * 40, "ref": "main"}),
            ("base", {"sha": self.base, "ref": "wrong-branch"}),
        ):
            self.api.reset_mock()
            def changed_pr(endpoint, *, method="GET", data=None, binary=False):
                result = self.fake_api(endpoint, method=method, data=data, binary=binary)
                if method == "POST" and endpoint.endswith("/pulls"):
                    result[key] = value
                return result
            self.api.side_effect = changed_pr
            with self.subTest(key=key, value=value), self.assertRaises(updater.InvalidUpdate):
                updater.command_publish(SimpleNamespace(artifact="artifact"))
            self.assertFalse(Path("outputs.txt").exists())


if __name__ == "__main__":
    unittest.main()
