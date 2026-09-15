"""Regression checks for authorization, coverage policy, and GitHub diff positions."""

import copy
import json
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
        command = review.sandbox_command(
            Path("/tmp/repo"), Path("/tmp/out"), Path("/tmp/action"), BASE, HEAD,
            Path("/tmp/config.json"), Path("/tmp/ocr"),
        )
        self.assertNotIn("/root", command)
        self.assertNotIn("/home/ocr-runner", command)
        self.assertNotIn("/", command)
        self.assertIn("--unshare-all", command)
        self.assertIn("--die-with-parent", command)

    def test_failed_comment_submission_never_approves_complete_coverage(self):
        for tool_calls in (
            {"failure_by_tool": {"code_comment": 1}},
            {"failure_details": [{"tool_name": "code_comment", "arguments": "{}"}]},
        ):
            result = complete_result()
            result["tool_calls"] = tool_calls
            payload, complete = self.payload(result)
            self.assertFalse(complete)
            self.assertEqual(payload["event"], "COMMENT")
            self.assertIn("failed finding submission", payload["body"])

    def test_exploratory_search_failure_does_not_imply_lost_findings(self):
        result = complete_result()
        result["tool_calls"] = {
            "failure_by_tool": {"code_search": 1},
            "failure_details": [{"tool_name": "code_search"}],
        }
        self.assertEqual(self.payload(result)[0]["event"], "APPROVE")


class FailureDiagnosticsTests(unittest.TestCase):
    def test_public_reason_has_categories_without_private_error_text(self):
        result = complete_result()
        result["status"] = "failed"
        result["manifest"]["terminal_state"] = "failed"
        result["manifest"]["coverage"]["completed"] = []
        result["manifest"]["coverage"]["failed"] = [
            {"path": "example.py", "classification": "provider", "reason": "PRIVATE_ERROR"},
        ]
        text = review.incomplete_diagnostics(result, 1)
        self.assertIn("provider=1", text)
        self.assertIn("OCR exit: 1", text)
        self.assertNotIn("PRIVATE_ERROR", text)
        self.assertNotIn("example.py", text)

    def test_untrusted_failure_categories_are_not_printed(self):
        result = {"status": "PRIVATE_STATUS", "manifest": {
            "run_failure": {"classification": "PRIVATE_CATEGORY", "reason": "PRIVATE_REASON"},
        }}
        text = review.incomplete_diagnostics(result, 2)
        self.assertNotIn("PRIVATE", text)
        self.assertIn("run failure: unknown", text)

    def test_failure_report_is_private_and_redacts_credentials(self):
        result = {"status": "failed", "manifest": {"run_failure": {
            "classification": "provider", "reason": "failure with fake-secret",
        }}, "tool_calls": {"failure_details": [
            {"tool_name": "file_read", "error": "failed", "arguments": "DO_NOT_SAVE_ARGUMENTS"},
        ]}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            (output / "diagnostics.log").write_text("fake-secret private diagnostic")
            env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "OCR_LLM_TOKEN": "fake-secret"}
            with patch.dict(os.environ, env), patch.object(review.Path, "home", return_value=root):
                review.save_failure_report(result, 1, output)
            report = root / ".local/state/ocr/failures/123-2.json"
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(report.parent.stat().st_mode & 0o777, 0o700)
            self.assertNotIn("fake-secret", report.read_text())
            self.assertNotIn("DO_NOT_SAVE_ARGUMENTS", report.read_text())
            self.assertIn("[REDACTED]", report.read_text())


class ReasoningConfigTests(unittest.TestCase):
    def test_supported_values_reach_config_without_writing_credentials(self):
        for effort in ("minimal", "low", "medium", "high", "max", " HIGH ", ""):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "config.json").write_text('{"language":"English","telemetry":{"enabled":false}}')
                env = {"OCR_LLM_URL": "https://api.example.invalid/v1", "OCR_LLM_MODEL": "test-model",
                       "OCR_LLM_TOKEN": "test-secret-do-not-persist", "OCR_LLM_REASONING_EFFORT": effort}
                with patch.dict(os.environ, env):
                    review.write_ocr_config(root, root / "effective.json")
                text = (root / "effective.json").read_text()
                config = json.loads(text)
                self.assertNotIn(env["OCR_LLM_TOKEN"], text)
                self.assertNotIn("auth_token", config["llm"])
                self.assertEqual(config["llm"]["auth_token_cmd"], 'printf "%s" "$OCR_LLM_TOKEN"')
                self.assertEqual(config["llm"]["url"], env["OCR_LLM_URL"])
                self.assertEqual(config["llm"]["model"], env["OCR_LLM_MODEL"])
                self.assertFalse(config["llm"]["use_anthropic"])
                self.assertEqual(config["llm"]["timeout_sec"], 600)
                self.assertEqual(config["language"], "English")
                self.assertFalse(config["telemetry"]["enabled"])
                if effort.strip():
                    self.assertEqual(config["llm"]["extra_body"]["reasoning_effort"], effort.strip().lower())
                else:
                    self.assertNotIn("extra_body", config["llm"])

    def test_invalid_effort_fails_before_writing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "effective.json"
            with patch.dict(os.environ, {"OCR_LLM_REASONING_EFFORT": "unsupported"}):
                with self.assertRaises(ValueError):
                    review.write_ocr_config(Path(temporary), destination)
            self.assertFalse(destination.exists())


class MetricsTests(unittest.TestCase):
    def test_available_metrics_are_added_at_the_bottom(self):
        result = complete_result()
        result["summary"] = {"input_tokens": 12500, "output_tokens": 2345, "cache_read_tokens": 10000}
        result["manifest"]["elapsed_ms"] = 61000
        body = review.review_payload(result, 0, PATCHES, BASE, HEAD, "developer", "https://github.com/run")[0]["body"]
        self.assertIn("Input tokens **12,500**", body)
        self.assertIn("Output tokens **2,345**", body)
        self.assertIn("Cache hit **80.0%**", body)
        self.assertIn("OCR time **1m 1s**", body)
        self.assertGreater(body.index("**Metrics:**"), body.index("Workflow run"))

    def test_missing_values_are_omitted_without_placeholders(self):
        metrics = review.review_metrics({"summary": {"input_tokens": 42}})
        self.assertIn("Input tokens **42**", metrics)
        for text in ("Output", "Cache", "OCR time", "n/a", "N/A"):
            self.assertNotIn(text, metrics)
        self.assertEqual(review.review_metrics({}), "")

    def test_reported_zero_cache_is_different_from_missing_cache(self):
        self.assertIn("Cache hit **0.0%**", review.review_metrics({"summary": {"input_tokens": 100, "cache_read_tokens": 0}}))
        self.assertNotIn("Cache", review.review_metrics({"summary": {"input_tokens": 100}}))

    def test_zero_denominator_and_inconsistent_cache_are_not_percentages(self):
        for inputs, cached in ((0, 0), (100, 101)):
            metrics = review.review_metrics({"summary": {"input_tokens": inputs, "cache_read_tokens": cached}})
            self.assertNotIn("Cache", metrics)

    def test_invalid_counters_are_not_rendered(self):
        result = {"summary": {"input_tokens": -1, "output_tokens": "text", "cache_read_tokens": True},
                  "manifest": {"elapsed_ms": -1}}
        self.assertEqual(review.review_metrics(result), "")


class EventTests(unittest.TestCase):
    def test_dispatched_review_does_not_fan_out_again(self):
        pr = {"state": "closed", "base": {"sha": BASE, "repo": {"id": 42}},
              "head": {"sha": HEAD}, "user": {"login": "developer"}}
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps({"inputs": {"pull_request_number": "1"}}))
            env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event),
                   "GITHUB_REPOSITORY": "altinkaya-opensource/test", "OCR_BOT_TOKEN": "test-only"}
            with patch.dict(os.environ, env), patch.object(review, "api", side_effect=[{"login": review.BOT, "id": 7}, pr]), \
                    patch.object(review.dependent_reviews, "refresh") as refresh, patch.object(review, "run_ocr") as run:
                review.main()
                refresh.assert_not_called()
                run.assert_not_called()

    def test_closed_pr_still_refreshes_dependents_without_starting_an_llm(self):
        pr = {"state": "closed", "base": {"sha": BASE, "repo": {"id": 42}},
              "head": {"sha": HEAD}, "user": {"login": "developer"}}
        with tempfile.TemporaryDirectory() as temporary:
            event = Path(temporary) / "event.json"
            event.write_text(json.dumps({"pull_request": {"number": 1, "head": {"sha": HEAD}}}))
            env = {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_EVENT_PATH": str(event),
                   "GITHUB_REPOSITORY": "altinkaya-opensource/test", "OCR_BOT_TOKEN": "test-only"}
            with patch.dict(os.environ, env), patch.object(review, "api", side_effect=[{"login": review.BOT, "id": 7}, pr]), \
                    patch.object(review.dependent_reviews, "refresh", return_value=1) as refresh, patch.object(review, "run_ocr") as run:
                review.main()
                refresh.assert_called_once()
                run.assert_not_called()

    def test_non_pr_target_events_fail_before_network_access(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_NAME": "issue_comment"}):
            with patch.object(review, "api") as api:
                with self.assertRaises(ValueError):
                    review.main()
                api.assert_not_called()

    def test_superseded_result_is_not_posted(self):
        import json
        pr = {"state": "open", "base": {"sha": BASE, "repo": {"id": 42}}, "head": {"sha": HEAD}, "user": {"login": "developer"}}
        current = copy.deepcopy(pr)
        current["head"]["sha"] = "c" * 40
        with tempfile.TemporaryDirectory() as temporary:
            event_file = Path(temporary) / "event.json"
            event_file.write_text(json.dumps({"pull_request": {"number": 1, "head": {"sha": HEAD}}}))
            env = {"GITHUB_EVENT_NAME": "pull_request_target", "GITHUB_EVENT_PATH": str(event_file),
                   "GITHUB_REPOSITORY": "altinkaya-opensource/test", "OCR_BOT_TOKEN": "test-only",
                   "GITHUB_RUN_ID": "123", "OCR_ACTION_PATH": temporary}
            context = {"text": "{}", "snapshots": [], "warnings": [], "complete": True}
            with patch.dict(os.environ, env), patch.object(review, "api", side_effect=[{"login": review.BOT, "id": 7}, pr, current]) as api:
                with patch.object(review, "prepare_repository", return_value=(Path(temporary), BASE, PATCHES)), \
                        patch.object(review.dependent_reviews, "refresh", return_value=0), \
                        patch.object(review.finding_history, "load", return_value=({"findings": []}, 0)), \
                        patch.object(review.linked_prs, "load", return_value=context), \
                        patch.object(review, "run_ocr", return_value=(complete_result(), 0)):
                    review.main()
            self.assertEqual(api.call_count, 3)
            self.assertTrue(all(len(call.args) == 2 for call in api.call_args_list))


if __name__ == "__main__":
    unittest.main()
