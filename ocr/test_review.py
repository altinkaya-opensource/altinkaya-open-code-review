"""Regression checks for authorization, coverage policy, and GitHub diff positions."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import review


BASE = "a" * 40
HEAD = "b" * 40
PATCHES = {"example.py": "--- a/example.py\n+++ b/example.py\n@@ -1,2 +1,3 @@\n context\n+new\n end\n"}


def complete_result():
    """Match the pinned OCR v1.12.1 JSON contract, including its manifest."""
    return {
        "status": "complete", "comments": [],
        "manifest": {
            "schema_version": "ocr.run-manifest/v1", "terminal_state": "complete",
            "input": {"resolved_base": BASE, "resolved_head": HEAD},
            "coverage": {
                "selected": [{"path": "example.py"}], "completed": [{"path": "example.py"}],
                "reused": [], "failed": [], "waived": [],
            },
        },
    }


class ReviewPolicyTests(unittest.TestCase):
    def payload(self, result=None, exit_code=0, author="developer", patches=None):
        return review.review_payload(
            result if result is not None else complete_result(), exit_code,
            patches if patches is not None else PATCHES, BASE, HEAD, author, "https://github.com/run",
        )

    def test_complete_clean_review_approves(self):
        payload, complete = self.payload()
        self.assertTrue(complete)
        self.assertEqual(payload["event"], "APPROVE")
        self.assertEqual(payload["commit_id"], HEAD)

    def test_null_comments_are_valid_only_with_proven_coverage(self):
        result = complete_result()
        result["comments"] = None
        self.assertEqual(self.payload(result)[0]["event"], "APPROVE")
        del result["manifest"]
        self.assertEqual(self.payload(result)[0]["event"], "COMMENT")

    def test_each_severity_and_inline_position(self):
        for severity in ("critical", "high", "medium", "low"):
            with self.subTest(severity=severity):
                result = complete_result()
                result["comments"] = [{"severity": severity, "content": "Concrete defect",
                                       "path": "example.py", "start_line": 2}]
                payload, complete = self.payload(result)
                self.assertTrue(complete)
                expected = "REQUEST_CHANGES" if severity in {"critical", "high"} else "COMMENT"
                self.assertEqual(payload["event"], expected)
                self.assertEqual(payload["comments"][0]["line"], 2)
                self.assertEqual(payload["comments"][0]["side"], "RIGHT")

    def test_incomplete_exit_zero_cannot_approve(self):
        result = complete_result()
        result["status"] = "partial"
        result["manifest"]["terminal_state"] = "partial"
        result["manifest"]["coverage"]["failed"] = [{"path": "other.py"}]
        payload, complete = self.payload(result)
        self.assertFalse(complete)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertIn("Review incomplete", payload["body"])

    def test_high_finding_survives_incomplete_review(self):
        result = complete_result()
        result["comments"] = [{"severity": "high", "content": "Defect", "path": "example.py"}]
        self.assertEqual(self.payload(result, exit_code=1)[0]["event"], "REQUEST_CHANGES")

    def test_schema_sha_waiver_and_denominator_fail_closed(self):
        variants = []
        for field, value in (("schema_version", "unknown"), ("terminal_state", "skipped")):
            result = complete_result()
            result["manifest"][field] = value
            variants.append(result)
        for field in ("resolved_base", "resolved_head"):
            result = complete_result()
            result["manifest"]["input"][field] = "c" * 40
            variants.append(result)
        result = complete_result()
        result["manifest"]["coverage"]["waived"] = [{"path": "binary.png"}]
        variants.append(result)
        result = complete_result()
        result["manifest"]["coverage"]["completed"] = []
        variants.append(result)
        for result in variants:
            with self.subTest(result=result):
                self.assertEqual(self.payload(result)[0]["event"], "COMMENT")
        self.assertFalse(self.payload(patches={**PATCHES, "unselected.png": "binary"})[1])
        self.assertFalse(self.payload(exit_code=1)[1])

    def test_invalid_line_is_retained_in_summary(self):
        for line in (0, 1, 99, "2", True):
            result = complete_result()
            result["comments"] = [{"severity": "medium", "content": "Do not lose this finding",
                                   "path": "example.py", "start_line": line}]
            payload, _ = self.payload(result)
            self.assertEqual(payload["comments"], [])
            self.assertIn("Do not lose this finding", payload["body"])

    def test_unknown_severity_or_missing_comments_is_an_error(self):
        result = complete_result()
        result["comments"] = [{"severity": "unknown", "content": "Defect"}]
        with self.assertRaises(ValueError):
            self.payload(result)
        del result["comments"]
        with self.assertRaises(ValueError):
            self.payload(result)

    def test_bot_cannot_approve_own_pr(self):
        self.assertEqual(self.payload(author="altinkaya-bot")[0]["event"], "COMMENT")

    def test_deleted_and_context_lines_are_not_new_line_comments(self):
        diff = "@@ -9,3 +9,3 @@\n context\n-old\n+new\n context\n@@ -20,0 +21,1 @@\n+next\n"
        self.assertEqual(review.added_lines(diff), {10, 21})

    def test_more_than_fifty_findings_remain_in_summary(self):
        result = complete_result()
        result["comments"] = [
            {"severity": "medium", "content": f"Defect {index}", "path": "example.py", "start_line": 2}
            for index in range(51)
        ]
        payload, _ = self.payload(result)
        self.assertEqual(len(payload["comments"]), 50)
        self.assertIn("Defect 50", payload["body"])

    def test_sandbox_has_no_host_home_or_root_mount(self):
        command = review.sandbox_command(Path("/tmp/repo"), Path("/tmp/out"), Path("/tmp/action"), BASE, HEAD)
        self.assertNotIn("/root", command)
        self.assertNotIn("/home/ocr-runner", command)
        self.assertNotIn("/", command)
        self.assertIn("--unshare-all", command)
        self.assertIn("--die-with-parent", command)


class EventTests(unittest.TestCase):
    def test_non_pr_target_events_fail_before_network_access(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "issue_comment"}):
            with patch.object(review, "api") as api:
                with self.assertRaises(ValueError):
                    review.main()
                api.assert_not_called()

    def test_superseded_result_is_not_posted(self):
        import json
        pr = {"state": "open", "base": {"sha": BASE}, "head": {"sha": HEAD}, "user": {"login": "developer"}}
        current = copy.deepcopy(pr)
        current["head"]["sha"] = "c" * 40
        with tempfile.TemporaryDirectory() as temporary:
            event_file = Path(temporary) / "event.json"
            event_file.write_text(json.dumps({"pull_request": {"number": 1, "head": {"sha": HEAD}}}))
            env = {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_EVENT_PATH": str(event_file),
                   "GITHUB_REPOSITORY": "altinkaya-opensource/test", "OCR_BOT_TOKEN": "test-only",
                   "GITHUB_RUN_ID": "123", "OCR_ACTION_PATH": temporary}
            with patch.dict(os.environ, env), patch.object(review, "api", side_effect=[{"login": review.BOT}, pr, current]) as api:
                with patch.object(review, "prepare_repository", return_value=(Path(temporary), BASE, PATCHES)):
                    with patch.object(review, "run_ocr", return_value=(complete_result(), 0)):
                        review.main()
            self.assertEqual(api.call_count, 3)
            self.assertTrue(all(len(call.args) == 2 for call in api.call_args_list))


if __name__ == "__main__":
    unittest.main()
