"""Exercise the classifier's actual shell gate and key job isolation contracts."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


WORKFLOWS = Path(__file__).resolve().parents[1] / "workflows"


class ClassifierGateTests(unittest.TestCase):
    def test_actual_classifier_gate_fails_closed(self):
        workflow = (WORKFLOWS / "review-upstream-markdown-changes.yml").read_text()
        match = re.search(r'case "\$decision" in\n.*?\n\s+esac', workflow, re.DOTALL)
        self.assertIsNotNone(match)
        for decision, accepted in (
            ("NEEDS_UPDATE", True),
            ("NO_UPDATE", True),
            ("UNDETERMINED", False),
            ("", False),
            ("needs_update", False),
            ("NEEDS_UPDATE\nNO_UPDATE", False),
            ("```NEEDS_UPDATE```", False),
            ("NEEDS_UPDATE; echo injected", False),
        ):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "outputs"
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", match[0]],
                    env={**os.environ, "decision": decision, "GITHUB_OUTPUT": str(output)},
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode == 0, accepted)
                actual = output.read_text() if output.exists() else ""
                self.assertEqual(actual, f"decision={decision}\n" if accepted else "")

    def test_dispatch_and_close_have_disjoint_explicit_gates(self):
        workflow = (WORKFLOWS / "review-upstream-markdown-changes.yml").read_text()
        dispatch = workflow.split("- name: Request implementation of required update", 1)[1]
        dispatch, close = dispatch.split("- name: Close unnecessary update", 1)
        self.assertIn("if: needs.classify.outputs.decision == 'NEEDS_UPDATE'", dispatch)
        self.assertIn("gh workflow run implement-upstream-markdown-changes.yml", dispatch)
        self.assertIn('--field review_run_id="$GITHUB_RUN_ID"', dispatch)
        self.assertNotIn("gh issue close", dispatch)
        self.assertIn("if: needs.classify.outputs.decision == 'NO_UPDATE'", close)
        self.assertIn('gh issue close "$ISSUE_NUMBER"', close)
        self.assertNotIn("gh workflow run", close)


class WorkflowIsolationTests(unittest.TestCase):
    def test_publish_requires_successful_matrix_and_runs_only_trusted_helper(self):
        workflow = (WORKFLOWS / "implement-upstream-markdown-changes.yml").read_text()
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn("needs: [validate, generate, test]", publish)
        self.assertNotIn("always()", publish)
        self.assertNotIn("continue-on-error", publish)
        self.assertNotIn("copilot-requests", publish)
        self.assertNotIn("npm install", publish)
        self.assertNotIn("pip install", publish)
        self.assertNotIn("pytest", publish)
        self.assertIn("ref: ${{ needs.validate.outputs.base_sha }}", publish)
        self.assertIn("persist-credentials: false", publish)
        commands = re.findall(r"^\s+run: (.+)$", publish, re.MULTILINE)
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].startswith("python .github/scripts/upstream_update.py publish "))

    def test_reusable_matrix_has_no_write_token_or_supplied_secrets(self):
        workflow = (WORKFLOWS / "testing.yml").read_text()
        self.assertIn("workflow_call:", workflow)
        self.assertIn('python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]', workflow)
        self.assertIn("fail-fast: false", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("UPDATER_MODEL: ${{ inputs.candidate_model }}", workflow)
        self.assertNotIn(": write", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("github.token", workflow)
        self.assertNotIn("continue-on-error", workflow)


if __name__ == "__main__":
    unittest.main()
