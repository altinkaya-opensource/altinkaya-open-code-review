"""Verify PR-Agent's transport contract without dependencies or provider calls."""

import unittest
from pathlib import Path

import pr_agent_engine
import pr_agent_runner
import review


class PRAgentTests(unittest.TestCase):
    def test_existing_endpoint_and_reasoning_provider_base(self):
        self.assertEqual(pr_agent_runner.provider_base_url("https://example.test/v1/chat/completions"),
                         "https://example.test/v1")
        self.assertEqual(pr_agent_runner.provider_base_url("https://example.test/v1/"), "https://example.test/v1")
        with self.assertRaises(ValueError):
            pr_agent_runner.provider_base_url("file:///token")

    def test_findings_preserve_severity_and_historical_id(self):
        for severity, expected in (("HIGH", "REQUEST_CHANGES"), ("MEDIUM", "COMMENT"), ("LOW", "APPROVE")):
            findings = pr_agent_runner.convert_findings({"review": {"key_issues_to_review": [{
                "relevant_file": "app.py", "start_line": 1, "end_line": 1,
                "issue_header": f"[{severity}] Problem", "issue_content": "Evidence [OCR-ID:test]",
            }]}})
            result = pr_agent_runner.build_result({"base": "a", "head": "b"}, {"app.py"}, {"app.py"}, findings, {}, 1)
            payload, complete = review.review_payload(result, 0, {"app.py": "@@ -0,0 +1 @@\n+bad()"},
                                                     "a", "b", "author", "url")
            self.assertTrue(complete)
            self.assertEqual(payload["event"], expected)
            self.assertIn("## PR-Agent", payload["body"])

    def test_unclassified_finding_is_not_silently_clean(self):
        with self.assertRaises(ValueError):
            pr_agent_runner.convert_findings({"review": {"key_issues_to_review": [{"issue_header": "Bug"}]}})

    def test_missing_chunk_never_approves(self):
        result = pr_agent_runner.build_result({"base": "a", "head": "b"}, {"one", "two"}, {"one"}, [], {}, 1)
        payload, complete = review.review_payload(result, 0, {"one": "", "two": ""}, "a", "b", "author", "url")
        self.assertFalse(complete)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(result["manifest"]["coverage"]["failed"], [{"path": "two", "classification": "budget"}])

    def test_no_files_is_explicitly_skipped(self):
        result = pr_agent_runner.build_result({"base": "a", "head": "b"}, set(), set(), [], {}, 1)
        self.assertEqual(result["status"], "skipped")

    def test_sandbox_imports_from_empty_directory(self):
        command = pr_agent_engine.sandbox_command(Path("/repo"), Path("/out"), Path("/action"),
                                                   Path("/opt/pr-agent/revision"), Path("/input"))
        self.assertEqual(command[command.index("--chdir") + 1], "/tmp")
        self.assertIn("-I", command)
        self.assertNotIn("OCR_BOT_TOKEN", " ".join(command))


if __name__ == "__main__":
    unittest.main()
