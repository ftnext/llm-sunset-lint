#!/usr/bin/env python3
"""Trusted boundary for the isolated upstream-model updater (stdlib only).

Generated Python is parsed as data, never imported or executed here. Only the
publish command can write to GitHub; it creates a new ref rather than pushing or
updating a branch. The test workflow executes candidate tests without secrets.
"""

from __future__ import annotations

import argparse
import ast
import base64
import codecs
import copy
from datetime import date
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tokenize
from urllib.parse import urlencode
import zipfile


CHECKER = "flake8-llm-sunset/src/flake8_llm_sunset/checker.py"
TESTS = "flake8-llm-sunset/tests/test_checker.py"
SOURCES = (
    "flake8-llm-sunset/sources/google-cloud/google-models.md",
    "flake8-llm-sunset/sources/google-cloud/model-versions.md",
)
FILES = (CHECKER, TESTS)
REVIEW_WORKFLOW = ".github/workflows/review-upstream-markdown-changes.yml"
ISSUE_TITLE = "Upstream Markdown sources changed"
MAX_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 250_000
MAX_CONTEXT_BYTES = 100_000
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
NUMBER_RE = re.compile(r"[1-9][0-9]{0,19}\Z")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/-]{0,199}\Z")
PROVENANCE_KEYS = {
    "schema_version", "repository", "base_sha", "source_sha", "issue_number",
    "review_run_id", "run_attempt", "branch",
}


class InvalidUpdate(ValueError):
    """Fail closed on malformed, stale, ambiguous, or conflicting inputs."""


class APIError(InvalidUpdate):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def require(condition, message):
    if not condition:
        raise InvalidUpdate(message)


def matched(value, pattern, label):
    require(isinstance(value, str) and pattern.fullmatch(value), f"Invalid {label}.")
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key!r}.")
            result[key] = value
        return result

    def constant(value):
        raise InvalidUpdate(f"Non-finite JSON value: {value}.")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise InvalidUpdate("Expected one strict UTF-8 JSON document.") from error


def text_value(value, label, limit=MAX_BYTES):
    require(isinstance(value, str), f"{label} must be a string.")
    require("\0" not in value, f"NUL in {label}.")
    try:
        require(len(value.encode("utf-8")) <= limit, f"{label} is too large.")
    except UnicodeError as error:
        raise InvalidUpdate(f"Invalid UTF-8 in {label}.") from error
    return value


def regular_path(path, *, exists=True):
    """Reject symlinks in any component, including parents of output files."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink(), f"Symlink is not allowed: {part}.")
    if exists:
        mode = path.stat().st_mode
        require(stat.S_ISREG(mode) and not mode & 0o111, f"Expected non-executable regular file: {path}.")
    return path


def read_json(path, limit=MAX_BYTES):
    path = regular_path(path)
    require(path.stat().st_size <= limit, "JSON input is too large.")
    return strict_json(path.read_bytes())


def write_json(path, value):
    path = regular_path(path, exists=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git(*args, binary=False):
    result = subprocess.run(
        ["git", "--no-pager", *args], check=False, capture_output=True, timeout=30,
        env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0"},
    )
    require(result.returncode == 0, f"Git failed: {' '.join(args[:2])}.")
    require(len(result.stdout) <= MAX_BYTES * 3, "Git output is too large.")
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def api(endpoint, *, method="GET", data=None, binary=False):
    """All endpoints are built from validated identifiers; never use shell=True."""
    command = ["gh", "api", "--method", method, endpoint]
    body = None
    if data is not None:
        command += ["--input", "-"]
        body = json.dumps(data).encode("utf-8")
    result = subprocess.run(command, input=body, capture_output=True, timeout=45, check=False)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace")
        status = re.search(r"\(HTTP ([0-9]{3})\)", error)
        raise APIError("GitHub API request failed.", int(status[1]) if status else None)
    require(len(result.stdout) <= MAX_BYTES * 3, "GitHub API response is too large.")
    return result.stdout if binary else strict_json(result.stdout)


def environment():
    return {
        "issue_number": matched(os.environ.get("ISSUE_NUMBER"), NUMBER_RE, "issue number"),
        "source_sha": matched(os.environ.get("SOURCE_SHA"), SHA_RE, "source SHA"),
        "review_run_id": matched(os.environ.get("REVIEW_RUN_ID"), NUMBER_RE, "review run ID"),
        "repository": matched(os.environ.get("GITHUB_REPOSITORY"), REPO_RE, "repository"),
    }


def branch_name(values):
    return f"automation/upstream-{values['issue_number']}-{values['source_sha']}"


def validate_provenance(value):
    require(isinstance(value, dict) and set(value) == PROVENANCE_KEYS, "Invalid provenance fields.")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Invalid provenance schema.")
    matched(value["repository"], REPO_RE, "repository")
    for key in ("base_sha", "source_sha"):
        matched(value[key], SHA_RE, key)
    for key in ("issue_number", "review_run_id", "run_attempt"):
        matched(value[key], NUMBER_RE, key)
    require(value["branch"] == branch_name(value), "Invalid branch provenance.")
    return value


def base_files(base_sha):
    matched(base_sha, SHA_RE, "base SHA")
    return {path: git_file(base_sha, path) for path in FILES}


def git_file(commit, path):
    entry = git("ls-tree", commit, "--", path).split()
    require(len(entry) == 4 and entry[0:2] == ["100644", "blob"] and entry[3] == path,
            f"Expected tracked regular file: {path}.")
    content = git("show", f"{commit}:{path}", binary=True).decode("utf-8")
    return text_value(content, path)


def validate_source(source_sha, base_sha):
    parents = git("rev-list", "--parents", "-n", "1", source_sha).split()
    require(len(parents) == 2 and parents[0] == source_sha, "Source commit must have exactly one parent.")
    git("merge-base", "--is-ancestor", source_sha, base_sha)
    changes = git("diff-tree", "--no-commit-id", "--no-renames", "--name-only", "-r", "-z", source_sha, binary=True)
    changed = [part.decode("utf-8") for part in changes.split(b"\0") if part]
    require(changed and len(changed) == len(set(changed)) and set(changed) <= set(SOURCES),
            "Source commit changed unexpected paths or contains no source changes.")
    for path in SOURCES:
        snapshot = git_file(source_sha, path)
        require(git_file(base_sha, path) == snapshot, "Source snapshots have changed on main; refusing a stale update.")
    for path in changed:
        # Also disallow replacing a symlink/executable with a regular file.
        git_file(parents[1], path)


def validate_issue(values):
    issue = api(f"repos/{values['repository']}/issues/{values['issue_number']}")
    expected_line = f"- Commit: https://github.com/{values['repository']}/commit/{values['source_sha']}"
    require(
        isinstance(issue, dict) and issue.get("state") == "open"
        and issue.get("title") == ISSUE_TITLE
        and issue.get("user", {}).get("login") == "github-actions[bot]"
        and issue.get("user", {}).get("type") == "Bot"
        and "pull_request" not in issue and issue.get("assignees") == []
        and isinstance(issue.get("body"), str)
        and issue["body"].splitlines().count(expected_line) == 1,
        "Issue must remain the exact open, unassigned bot-created source-update issue.",
    )


def completed_review(values, base_sha):
    endpoint = f"repos/{values['repository']}/actions/runs/{values['review_run_id']}"
    deadline = time.monotonic() + 60
    while True:
        run = api(endpoint)
        require(
            isinstance(run, dict) and str(run.get("id")) == values["review_run_id"]
            and run.get("repository", {}).get("full_name") == values["repository"]
            and run.get("head_repository", {}).get("full_name") == values["repository"]
            and run.get("path") == REVIEW_WORKFLOW and run.get("event") == "workflow_dispatch"
            and run.get("head_branch") == "main" and run.get("head_sha") == base_sha,
            "Review run does not match the trusted workflow, repository, branch, and base SHA.",
        )
        matched(str(run.get("run_attempt")), NUMBER_RE, "review attempt")
        if run.get("status") == "completed":
            require(run.get("conclusion") == "success", "Review run did not succeed.")
            return run
        require(run.get("status") in {"queued", "in_progress", "pending", "waiting", "requested"}, "Invalid review run status.")
        require(time.monotonic() < deadline, "Review run has not completed successfully within 60 seconds.")
        time.sleep(3)


def classifier_manifest(values, base_sha, run):
    attempt = str(run["run_attempt"])
    name = f"classifier-result-{attempt}"
    endpoint = f"repos/{values['repository']}/actions/runs/{values['review_run_id']}/artifacts"
    artifacts = []
    for page in range(1, 101):
        response = api(f"{endpoint}?per_page=100&page={page}")
        require(isinstance(response, dict) and isinstance(response.get("artifacts"), list), "Invalid artifact listing.")
        artifacts.extend(item for item in response["artifacts"] if item.get("name") == name)
        if len(response["artifacts"]) < 100:
            break
    else:
        raise InvalidUpdate("Too many review artifacts.")
    require(len(artifacts) == 1, "Expected exactly one classifier artifact for this review attempt.")
    artifact = artifacts[0]
    require(artifact.get("expired") is False and 0 < artifact.get("size_in_bytes", 0) <= MAX_BYTES,
            "Classifier artifact is expired or too large.")
    workflow_run = artifact.get("workflow_run", {})
    require(str(workflow_run.get("id")) == values["review_run_id"]
            and workflow_run.get("head_sha") == base_sha,
            "Classifier artifact belongs to a different review run.")
    artifact_id = matched(str(artifact.get("id")), NUMBER_RE, "artifact ID")
    archive = api(f"repos/{values['repository']}/actions/artifacts/{artifact_id}/zip", binary=True)
    require(len(archive) <= MAX_BYTES, "Classifier archive is too large.")
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = bundle.infolist()
            require(len(members) == 1 and members[0].filename == "classifier-result.json", "Unexpected classifier archive contents.")
            member = members[0]
            mode = member.external_attr >> 16
            require(member.file_size <= 20_000 and not member.is_dir()
                    and (not stat.S_IFMT(mode) or stat.S_ISREG(mode)) and not mode & 0o111,
                    "Classifier archive entry is not a small regular file.")
            manifest = strict_json(bundle.read(member))
    except (zipfile.BadZipFile, RuntimeError, OSError) as error:
        raise InvalidUpdate("Invalid classifier archive.") from error
    expected = {
        "schema_version": 1, "repository": values["repository"],
        "review_run_id": values["review_run_id"], "run_attempt": attempt,
        "review_head_sha": base_sha, "issue_number": values["issue_number"],
        "source_sha": values["source_sha"], "decision": "NEEDS_UPDATE",
    }
    require(manifest == expected and type(manifest.get("schema_version")) is int,
            "Classifier result does not authorize this exact update.")
    return attempt


def duplicate(values):
    """Closed/merged PRs are terminal. A branch without a PR is not ours to reuse."""
    branch = branch_name(values)
    owner = values["repository"].split("/")[0]
    query = urlencode({"state": "all", "head": f"{owner}:{branch}", "per_page": 100})
    pulls = api(f"repos/{values['repository']}/pulls?{query}")
    require(isinstance(pulls, list), "Invalid pull request listing.")
    for pull in pulls:
        if pull.get("head", {}).get("ref") == branch and pull.get("head", {}).get("repo", {}).get("full_name") == values["repository"]:
            require(pull.get("state") in {"open", "closed"}, "Invalid pull request state.")
            return pull
    try:
        api(f"repos/{values['repository']}/git/ref/heads/{branch}")
    except APIError as error:
        if error.status == 404:
            return None
        raise
    raise InvalidUpdate("Deterministic branch already exists without a matching PR; refusing to update it.")


def current_main(repository):
    result = api(f"repos/{repository}/git/ref/heads/main")
    require(result.get("ref") == "refs/heads/main" and result.get("object", {}).get("type") == "commit", "Invalid main reference.")
    return matched(result["object"].get("sha"), SHA_RE, "main SHA")


def validate_live(values, expected_base=None):
    base_sha = current_main(values["repository"])
    require(expected_base is None or expected_base == base_sha, "Main moved since generation; refusing a stale update.")
    require(git("rev-parse", "HEAD") == base_sha, "Checkout must match the current pinned main SHA.")
    validate_source(values["source_sha"], base_sha)
    validate_issue(values)
    run = completed_review(values, base_sha)
    attempt = classifier_manifest(values, base_sha, run)
    return validate_provenance({
        "schema_version": 1, **values, "base_sha": base_sha,
        "run_attempt": attempt, "branch": branch_name(values),
    })


def output(name, value):
    value = str(value)
    require("\n" not in value and "\r" not in value, "Unsafe workflow output.")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")
    print(f"{name}={value}")


def report_pr(pull, *, skipped):
    url = pull.get("html_url")
    require(isinstance(url, str) and re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*", url), "Invalid PR URL.")
    output("skip", "true" if skipped else "false")
    output("pr_url", url)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(f"{'Existing PR; no changes made' if skipped else 'Created draft PR'}: {url}\n")


def command_validate(args):
    values = environment()
    existing = duplicate(values)
    if existing:
        output("base_sha", matched(git("rev-parse", "HEAD"), SHA_RE, "checkout SHA"))
        output("branch", branch_name(values))
        report_pr(existing, skipped=True)
        return
    provenance = validate_live(values)
    write_json(args.output, provenance)
    output("base_sha", provenance["base_sha"])
    output("branch", provenance["branch"])
    output("skip", "false")


def parse_python(source, label):
    text_value(source, label)
    try:
        raw = source.encode("utf-8")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        require(codecs.lookup(encoding).name in {"utf-8", "utf-8-sig"}, "Python source must use UTF-8 encoding.")
        # Parse exactly the bytes emitted to disk. Parsing only a Unicode string
        # would ignore an encoding cookie that changes Python's interpretation.
        return ast.parse(raw)
    except (SyntaxError, ValueError, LookupError, RecursionError) as error:
        raise InvalidUpdate(f"Invalid Python syntax in {label}.") from error


def byte_span(source, node):
    lines = source.encode("utf-8").splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
    return start, end


def table_nodes(source):
    tree = parse_python(source, CHECKER)
    nodes = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"MODEL_SHUTDOWNS", "EARLIEST_SHUTDOWNS"}:
                require(name not in nodes, "Duplicate model table assignment.")
                nodes[name] = node.value
    require(set(nodes) == {"MODEL_SHUTDOWNS", "EARLIEST_SHUTDOWNS"}, "Missing expected model tables.")
    return nodes


def read_tables(source):
    nodes = table_nodes(source)
    shutdowns = nodes["MODEL_SHUTDOWNS"]
    require(isinstance(shutdowns, ast.Dict), "MODEL_SHUTDOWNS must be a dictionary literal.")
    models = {}
    for key, value in zip(shutdowns.keys, shutdowns.values):
        require(isinstance(key, ast.Constant) and isinstance(key.value, str), "Model IDs must be string literals.")
        model = matched(key.value, MODEL_RE, "model ID")
        require(model not in models, "Duplicate model ID in checker.")
        require(isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                and value.func.id == "date" and len(value.args) == 3 and not value.keywords
                and all(isinstance(arg, ast.Constant) and type(arg.value) is int for arg in value.args),
                "Shutdown dates must be date(year, month, day) constants.")
        try:
            models[model] = date(*(arg.value for arg in value.args)).isoformat()
        except ValueError as error:
            raise InvalidUpdate("Invalid checker shutdown date.") from error
    earliest_node = nodes["EARLIEST_SHUTDOWNS"]
    if isinstance(earliest_node, ast.Set):
        require(all(isinstance(node, ast.Constant) and isinstance(node.value, str) for node in earliest_node.elts), "Earliest dates must be a set of model string literals.")
        earliest = [node.value for node in earliest_node.elts]
    else:
        require(isinstance(earliest_node, ast.Call) and isinstance(earliest_node.func, ast.Name)
                and earliest_node.func.id == "set" and not earliest_node.args and not earliest_node.keywords,
                "EARLIEST_SHUTDOWNS must be a set literal (or empty set()).")
        earliest = []
    require(len(earliest) == len(set(earliest)) and set(earliest) <= set(models), "Invalid earliest-date model set.")
    return models, set(earliest)


def test_partition(source):
    """Mask test functions; all bytes outside their spans remain trusted."""
    tree = parse_python(source, TESTS)
    raw = source.encode("utf-8")
    pieces = []
    tests = {}
    cursor = 0
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        require(node.name not in tests, "Duplicate test function name.")
        tests[node.name] = node
        start, end = byte_span(source, node)
        if node.decorator_list:
            # col_offset is after @, so include that byte in the editable span.
            start = byte_span(source, node.decorator_list[0])[0] - 1
        pieces.append(raw[cursor:start])
        cursor = end
    pieces.append(raw[cursor:])
    return tree, tests, pieces


def literal_decorator(node):
    require(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parametrize" and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "mark" and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "pytest", "Only literal pytest.mark.parametrize test decorators are allowed.")
    require(2 <= len(node.args) <= 3 and all(key.arg in {"ids", "indirect", "scope"} for key in node.keywords), "Invalid parametrize decorator.")
    try:
        for value in [*node.args, *(key.value for key in node.keywords)]:
            ast.literal_eval(value)
        values = ast.literal_eval(node.args[1])
        require(isinstance(values, (list, tuple)) and len(values) > 0, "Parametrize values must be nonempty literal data.")
    except (ValueError, TypeError, SyntaxError) as error:
        raise InvalidUpdate("Parametrize arguments must be literal data.") from error


def validate_new_test(node):
    """New tests are a small assertion language, not general Python programs.

    In particular, a generated test cannot exit pytest successfully before the
    original regressions execute. Existing tests use their trusted AST shape.
    """
    parameters = set()
    for decorator in node.decorator_list:
        literal_decorator(decorator)
        require(len(decorator.args) == 2 or ast.literal_eval(decorator.args[2]) is False,
                "New tests cannot invoke indirect fixtures.")
        names = ast.literal_eval(decorator.args[0])
        if isinstance(names, str):
            names = [name.strip() for name in names.split(",")]
        require(isinstance(names, (list, tuple)) and all(isinstance(name, str) and name.isidentifier() for name in names), "Invalid new test parameter names.")
        parameters.update(names)
        for keyword in decorator.keywords:
            require(keyword.arg != "indirect" or ast.literal_eval(keyword.value) is False, "New tests cannot invoke indirect fixtures.")
    arguments = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    require(arguments == parameters, "New tests may only request literal parametrized arguments.")
    reserved = {"check", "date", "len", "ModelExpiryChecker"}
    require(not parameters & reserved and not any(name.startswith("__") for name in parameters), "Unsafe new test parameter name.")
    available = set(parameters) | {"ModelExpiryChecker"}
    allowed = (
        ast.Constant, ast.Name, ast.Load, ast.List, ast.Tuple, ast.Set, ast.Dict,
        ast.JoinedStr, ast.FormattedValue, ast.Subscript, ast.Slice,
        ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.BoolOp, ast.And, ast.Or,
        ast.UnaryOp, ast.Not, ast.UAdd, ast.USub, ast.BinOp, ast.Add, ast.Sub,
        ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    )

    def expression(value):
        if isinstance(value, ast.Call):
            require(not value.keywords, "New test calls use positional arguments only.")
            if isinstance(value.func, ast.Name):
                require(value.func.id in {"check", "date", "len"}, "New tests may call only check(), date(), and len().")
                require(len(value.args) == {"check": 2, "date": 3, "len": 1}[value.func.id], "Unexpected new test call arguments.")
            else:
                require(isinstance(value.func, ast.Attribute) and value.func.attr in {"startswith", "endswith"}, "Unexpected method call in new test.")
                expression(value.func.value)
                require(len(value.args) == 1, "Unexpected string method arguments.")
            for arg in value.args:
                expression(arg)
            return
        require(isinstance(value, allowed), "New tests must use simple literal/check/date/len assertions.")
        if isinstance(value, ast.Name):
            require(value.id in available, "Unknown or unsafe name in new test.")
        if isinstance(value, ast.Dict):
            require(all(key is not None for key in value.keys), "Dictionary unpacking is not allowed in new tests.")
        for child in ast.iter_child_nodes(value):
            expression(child)

    for index, statement in enumerate(node.body):
        if index == 0 and isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str):
            continue
        if isinstance(statement, ast.Assign):
            require(len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name), "New tests may assign only local variables.")
            target = statement.targets[0].id
            require(target not in reserved and not target.startswith("__"), "Unsafe new test assignment.")
            expression(statement.value)
            available.add(target)
        elif isinstance(statement, ast.Assert):
            expression(statement.test)
            if statement.msg is not None:
                expression(statement.msg)
        else:
            raise InvalidUpdate("New tests may contain only local assignments and assertions.")


def validate_tests(original, candidate):
    original_tree, old, _ = test_partition(original)
    tree, new, _ = test_partition(candidate)
    require(set(old) <= set(new), "Existing regression test names cannot be removed or renamed.")
    require(set(new) - set(old), "At least one new regression test is required.")
    # Preserve all non-test statements, including imports and the shared helper,
    # byte-for-byte. Whitespace between top-level statements may be reformatted.
    def fixed_parts(text, module):
        raw = text.encode("utf-8")
        return [raw[slice(*byte_span(text, node))] for node in module.body
                if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))]
    require(fixed_parts(original, original_tree) == fixed_parts(candidate, tree), "Only test functions may change in the test module.")

    class FactualLiterals(ast.NodeTransformer):
        def visit_Constant(self, node):
            # bool is deliberately not an int here: assert x == 1 must not turn
            # into assert x == True, nor may assertions/operators disappear.
            if type(node.value) is str:
                node.value = "<string literal>"
            elif type(node.value) is int:
                node.value = 0
            return node

    def structure(node):
        return ast.dump(FactualLiterals().visit(copy.deepcopy(node)), include_attributes=False)

    for name, node in old.items():
        require(structure(node) == structure(new[name]),
                "Existing tests may change only factual string/integer literals, not regression structure.")

    def has_check(node):
        return any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                   and child.func.id == "check" for child in ast.walk(node))

    for name in set(new) - set(old):
        node = new[name]
        validate_new_test(node)
        # This checks useful test shape, not semantic correctness of the facts.
        # Human review remains necessary before merging the draft.
        checked_names = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Assign) and has_check(child.value):
                checked_names.update(target.id for target in child.targets if isinstance(target, ast.Name))
        require(has_check(node) and any(
            isinstance(child, ast.Assert) and (
                has_check(child.test) or any(isinstance(value, ast.Name) and value.id in checked_names for value in ast.walk(child.test))
            ) for child in ast.walk(node)
        ), "New regression tests must call check() and assert on its result.")

    for node in new.values():
        require(any(isinstance(child, ast.Assert) for child in ast.walk(node)), "Every test must retain an assertion.")
        require(not node.args.defaults and not any(node.args.kw_defaults)
                and node.returns is None
                and all(arg.annotation is None for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs])
                and node.args.vararg is None and node.args.kwarg is None,
                "Test signatures must not execute defaults or annotations.")
        for decorator in node.decorator_list:
            literal_decorator(decorator)
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute):
                require(child.attr not in {"skip", "skipif", "xfail"}, "Tests may not disable regression coverage.")
            if isinstance(child, ast.Name):
                require(child.id not in {"skip", "skipif", "xfail"}, "Tests may not disable regression coverage.")


def reconstruct(original, response):
    require(isinstance(response, dict), "Model response must be a JSON object.")
    require(response.get("status") != "AMBIGUOUS", "Model reported ambiguous source facts; refusing to guess.")
    require(set(response) == {"status", "model_shutdowns", "earliest_shutdowns", "tests", "summary"}
            and response.get("status") == "UPDATE", "Invalid model response schema.")
    models = response["model_shutdowns"]
    earliest = response["earliest_shutdowns"]
    require(isinstance(models, dict) and 0 < len(models) <= 1000, "Expected a bounded nonempty model table.")
    for model, shutdown in models.items():
        matched(model, MODEL_RE, "model ID")
        require(isinstance(shutdown, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", shutdown), "Shutdown dates must use YYYY-MM-DD.")
        try:
            date.fromisoformat(shutdown)
        except ValueError as error:
            raise InvalidUpdate("Invalid shutdown date.") from error
    require(isinstance(earliest, list) and all(isinstance(model, str) for model in earliest)
            and len(earliest) == len(set(earliest)) and set(earliest) <= set(models), "Invalid earliest-shutdown set.")
    text_value(response["summary"], "summary", 2000)
    tests = text_value(response["tests"], "generated tests", MAX_RESPONSE_BYTES)
    old_models, old_earliest = read_tables(original[CHECKER])
    require((models, set(earliest)) != (old_models, old_earliest), "No semantic model table change was proposed.")
    model_order = [model for model in old_models if model in models] + sorted(set(models) - set(old_models))
    nodes = table_nodes(original[CHECKER])
    old_earliest_order = [node.value for node in nodes["EARLIEST_SHUTDOWNS"].elts] if isinstance(nodes["EARLIEST_SHUTDOWNS"], ast.Set) else []
    earliest_order = [model for model in old_earliest_order if model in earliest] + sorted(set(earliest) - old_earliest)
    rendered_models = "{\n" + "".join(
        f"    {json.dumps(model)}: date({', '.join(str(int(part)) for part in models[model].split('-'))}),\n"
        for model in model_order
    ) + "}"
    rendered_earliest = "{\n" + "".join(f"    {json.dumps(model)},\n" for model in earliest_order) + "}" if earliest else "set()"
    raw = original[CHECKER].encode("utf-8")
    replacements = {}
    if models != old_models:
        replacements["MODEL_SHUTDOWNS"] = rendered_models
    if set(earliest) != old_earliest:
        replacements["EARLIEST_SHUTDOWNS"] = rendered_earliest
    spans = [(byte_span(original[CHECKER], nodes[name]), value) for name, value in replacements.items()]
    for (start, end), value in sorted(spans, reverse=True):
        raw = raw[:start] + value.encode("utf-8") + raw[end:]
    checker = raw.decode("utf-8")
    require(read_tables(checker) == (models, set(earliest)), "Reconstructed tables do not match model data.")
    validate_tests(original[TESTS], tests)
    return {CHECKER: checker, TESTS: tests}


def command_prepare(args):
    provenance = validate_provenance(read_json(args.provenance))
    require(git("rev-parse", "HEAD") == provenance["base_sha"], "Prompt checkout differs from pinned base.")
    original = base_files(provenance["base_sha"])
    diff = git("diff", "--no-ext-diff", "--no-textconv", "--unified=80", f"{provenance['source_sha']}^", provenance["source_sha"], "--", *SOURCES, binary=True).decode("utf-8")
    template = git_file(provenance["base_sha"], ".github/prompts/implement-upstream-update.md")
    prompt = template + "\n\n" + "\n\n".join(
        f"<{name}>\n{content}\n</{name}>" for name, content in (
            ("TRUSTED_CHECKER", original[CHECKER]), ("TRUSTED_TESTS", original[TESTS]),
            ("UNTRUSTED_UPSTREAM_DIFF", diff),
        )
    )
    text_value(prompt, "generation prompt", MAX_CONTEXT_BYTES)
    regular_path(args.output, exists=False).write_text(prompt, encoding="utf-8")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def configured_model():
    return matched(os.environ.get("UPDATER_MODEL"), MODEL_RE, "updater model")


def command_build(args):
    provenance = validate_provenance(read_json(args.provenance))
    require(git("rev-parse", "HEAD") == provenance["base_sha"], "Build checkout differs from pinned base.")
    response = read_json(args.response, MAX_RESPONSE_BYTES)
    original = base_files(provenance["base_sha"])
    generated = reconstruct(original, response)
    directory = Path(args.artifact)
    regular_path(directory, exists=False)
    require(not directory.exists() or not any(directory.iterdir()), "Artifact directory must be empty.")
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "candidate.json", {
        "schema_version": 1, "provenance": provenance, "model": configured_model(),
        "response": response,
        "files": {path: {"content": generated[path], "sha256": sha256(generated[path]),
                         "base_sha256": sha256(original[path])} for path in FILES},
    })


def load_candidate(directory):
    directory = Path(directory)
    regular_path(directory, exists=False)
    require(directory.is_dir() and {path.name for path in directory.iterdir()} == {"candidate.json"}, "Unexpected artifact files.")
    candidate = read_json(directory / "candidate.json")
    require(isinstance(candidate, dict) and set(candidate) == {"schema_version", "provenance", "model", "response", "files"}
            and type(candidate["schema_version"]) is int and candidate["schema_version"] == 1, "Invalid candidate schema.")
    provenance = validate_provenance(candidate["provenance"])
    require(git("rev-parse", "HEAD") == provenance["base_sha"], "Artifact base does not match trusted checkout.")
    require(candidate["model"] == configured_model(), "Candidate model does not match configured model.")
    original = base_files(provenance["base_sha"])
    generated = reconstruct(original, candidate["response"])
    require(isinstance(candidate["files"], dict) and set(candidate["files"]) == set(FILES), "Artifact path allowlist violation.")
    for path in FILES:
        expected = {"content": generated[path], "sha256": sha256(generated[path]), "base_sha256": sha256(original[path])}
        require(candidate["files"][path] == expected, "Artifact content or hashes do not match deterministic reconstruction.")
    return candidate


def command_apply(args):
    candidate = load_candidate(args.artifact)
    # Preflight both files before writing either; don't overwrite local edits.
    for path in FILES:
        regular_path(path)
        require(sha256(Path(path).read_text(encoding="utf-8")) == candidate["files"][path]["base_sha256"], "Working tree differs from trusted base.")
    for path in FILES:
        Path(path).write_text(candidate["files"][path]["content"], encoding="utf-8")


def command_publish(args):
    candidate = load_candidate(args.artifact)
    provenance = candidate["provenance"]
    values = environment()
    require(all(provenance[key] == value for key, value in values.items()), "Artifact provenance differs from dispatch inputs.")
    existing = duplicate(values)
    if existing:
        report_pr(existing, skipped=True)
        return
    live = validate_live(values, expected_base=provenance["base_sha"])
    require(live == provenance, "Review provenance changed since generation.")
    existing = duplicate(values)
    if existing:
        report_pr(existing, skipped=True)
        return
    repository = values["repository"]
    endpoint = f"repos/{repository}/git"
    base = api(f"{endpoint}/commits/{provenance['base_sha']}")
    tree_sha = matched(base.get("tree", {}).get("sha"), SHA_RE, "base tree SHA")
    entries = []
    for path in FILES:
        blob = api(f"{endpoint}/blobs", method="POST", data={
            "content": base64.b64encode(candidate["files"][path]["content"].encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": matched(blob.get("sha"), SHA_RE, "blob SHA")})
    tree = api(f"{endpoint}/trees", method="POST", data={"base_tree": tree_sha, "tree": entries})
    commit = api(f"{endpoint}/commits", method="POST", data={
        "message": f"Update model shutdown tables for upstream issue #{values['issue_number']}",
        "tree": matched(tree.get("sha"), SHA_RE, "tree SHA"), "parents": [provenance["base_sha"]],
        "author": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"},
    })
    commit_sha = matched(commit.get("sha"), SHA_RE, "commit SHA")
    # Check again immediately before creating any visible ref. Orphaned immutable
    # blobs/trees/commits are harmless if the source/issue/main changed meanwhile.
    require(current_main(repository) == provenance["base_sha"], "Main moved before publication.")
    validate_issue(values)
    existing = duplicate(values)
    if existing:
        report_pr(existing, skipped=True)
        return
    try:
        api(f"{endpoint}/refs", method="POST", data={"ref": f"refs/heads/{provenance['branch']}", "sha": commit_sha})
    except APIError as error:
        if error.status == 422:
            existing = duplicate(values)
            if existing:
                report_pr(existing, skipped=True)
                return
        raise
    require(current_main(repository) == provenance["base_sha"], "Main moved after branch creation; branch was left for human review.")
    validate_issue(values)
    body = (
        f"Closes #{values['issue_number']}\n\n"
        f"Source commit: https://github.com/{repository}/commit/{values['source_sha']}\n"
        f"Review run: https://github.com/{repository}/actions/runs/{values['review_run_id']}\n"
        f"Pinned base: {provenance['base_sha']}\n"
        f"Generator model: {candidate['model']}\n\n"
        "This draft changes only the two shutdown tables and regression tests. "
        "The candidate passed the isolated Python test matrix before publication. "
        "Please verify the retirement dates against the linked upstream source diff.\n"
    )
    try:
        pull = api(f"repos/{repository}/pulls", method="POST", data={
            "title": f"Update model shutdown dates for upstream issue #{values['issue_number']}",
            "head": provenance["branch"], "base": "main", "draft": True, "body": body,
        })
    except APIError as error:
        if error.status == 422:
            existing = duplicate(values)
            if existing:
                report_pr(existing, skipped=True)
                return
        raise
    require(pull.get("draft") is True, "Created pull request did not report draft status.")
    require(pull.get("head", {}).get("sha") == commit_sha
            and pull.get("head", {}).get("ref") == provenance["branch"]
            and pull.get("base", {}).get("sha") == provenance["base_sha"]
            and pull.get("base", {}).get("ref") == "main",
            "A PR was created but its head/base differs from the tested candidate; manual review is required.")
    report_pr(pull, skipped=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare", "build", "apply", "publish"):
        command = commands.add_parser(name)
        if name in {"validate", "prepare"}:
            command.add_argument("--output", required=True)
        if name in {"prepare", "build"}:
            command.add_argument("--provenance", required=True)
        if name == "build":
            command.add_argument("--response", required=True)
        if name in {"build", "apply", "publish"}:
            command.add_argument("--artifact", required=True)
        command.set_defaults(function=globals()[f"command_{name}"])
    args = parser.parse_args(argv)
    try:
        args.function(args)
    except (InvalidUpdate, OSError, UnicodeError, subprocess.TimeoutExpired) as error:
        print(f"Refusing upstream update: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
