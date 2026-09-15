"""Exercise result checks through the actual review publication path."""

from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import finding_history
import review
from test_review import BASE, HEAD, PATCHES, complete_result


class ResultCheckTests(unittest.TestCase):
    def run_review(self, result=None, previous=None, author="developer", incomplete_context=False,
                   error=None, reject_review=False, superseded=False):
        pr = {"state": "open", "base": {"sha": BASE, "repo": {"id": 42}},
              "head": {"sha": HEAD}, "user": {"login": author}}
        context = {"text": "{}", "snapshots": [], "warnings": [], "complete": not incomplete_context}
        self.calls = []

        def api(path, token, payload=None):
            self.calls.append((path, token, payload))
            if path == "/user":
                return {"login": review.BOT, "id": 7}
            if path.endswith("/reviews"):
                if reject_review:
                    raise RuntimeError("Review publication failed")
                return {"html_url": "https://github.com/review"}
            if path.endswith("/check-runs"):
                return {"id": 123}
            current = copy.deepcopy(pr)
            if superseded and len(self.calls) > 2:
                current["head"]["sha"] = "c" * 40
            return current

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            event = Path(temporary) / "event.json"
            # A dispatch's GITHUB_SHA is the default branch, not the reviewed PR.
            event.write_text(json.dumps({"inputs": {"pull_request_number": "1"}}))
            stack.enter_context(patch.dict(os.environ, {
                "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_EVENT_PATH": str(event),
                "GITHUB_REPOSITORY": "altinkaya-opensource/test", "GITHUB_SHA": BASE,
                "GITHUB_RUN_ID": "123", "OCR_ACTION_PATH": temporary,
                "OCR_BOT_TOKEN": "bot-token", "OCR_CHECK_TOKEN": "job-token",
            }))
            stack.enter_context(patch.object(review, "api", side_effect=api))
            stack.enter_context(patch.object(review, "prepare_repository", return_value=(Path(temporary), BASE, PATCHES)))
            stack.enter_context(patch.object(finding_history, "load", return_value=(previous or {"findings": []}, 0)))
            stack.enter_context(patch.object(review.linked_prs, "load", return_value=context))
            stack.enter_context(patch.object(review.linked_prs, "unchanged", return_value=True))
            stack.enter_context(patch.object(review, "save_failure_report"))
            stack.enter_context(patch.object(review, "run_ocr", side_effect=error,
                                             return_value=(result if result is not None else complete_result(), 0)))
            review.main()

    def check_result(self, conclusion):
        checks = [call for call in self.calls if call[0].endswith("/check-runs")]
        self.assertEqual(len(checks), 1)
        _, token, payload = checks[0]
        self.assertEqual(token, "job-token")
        self.assertEqual(payload["head_sha"], HEAD)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["conclusion"], conclusion)
        reviews = [call for call in self.calls if call[0].endswith("/reviews")]
        if reviews:
            self.assertEqual(reviews[0][1], "bot-token")
            self.assertLess(self.calls.index(reviews[0]), self.calls.index(checks[0]))

    def test_clean_review_is_green_even_for_bot_authored_pr(self):
        for author in ("developer", review.BOT):
            with self.subTest(author=author):
                self.run_review(author=author)
                self.check_result("success")

    def test_all_finding_severities_are_neutral_but_only_high_and_critical_block(self):
        for severity in ("critical", "high", "medium", "low"):
            with self.subTest(severity=severity):
                result = complete_result()
                result["comments"] = [{"severity": severity, "content": "A concrete defect",
                                       "path": "example.py", "start_line": 2}]
                self.run_review(result)
                self.check_result("neutral")
                payload = next(call[2] for call in self.calls if call[0].endswith("/reviews"))
                self.assertEqual(payload["event"], "REQUEST_CHANGES" if severity in {"critical", "high"} else "COMMENT")

    def test_old_open_findings_count_but_resolved_findings_do_not(self):
        pr = {"head": {"sha": HEAD}, "base": {"sha": BASE, "repo": {"id": 42}}}
        context = {"snapshots": [], "warnings": [], "complete": True}
        fingerprint = finding_history.digest(review.linked_prs.revision(pr, context))
        finding = finding_history.identify({"severity": "high", "content": "A concrete defect",
                                            "path": "example.py", "start_line": 2}, [])
        previous = finding_history.reconcile({"findings": []}, [finding], 42, 1, HEAD, fingerprint, True)
        self.run_review(previous=previous)
        self.check_result("neutral")
        previous["findings"][0]["state"] = "resolved"
        self.run_review(previous=previous)
        self.check_result("success")

    def test_partial_review_and_missing_related_context_are_red(self):
        partial = complete_result()
        partial["status"] = "partial"
        for result, incomplete in ((partial, False), (complete_result(), True)):
            with self.subTest(incomplete_context=incomplete), self.assertRaises(RuntimeError):
                self.run_review(result, incomplete_context=incomplete)
            self.check_result("failure")

    def test_runtime_and_review_publication_errors_never_publish_green(self):
        for options in ({"error": RuntimeError("OCR failed")}, {"reject_review": True}):
            with self.subTest(options=options), self.assertRaises(RuntimeError):
                self.run_review(**options)
            self.check_result("failure")

    def test_superseded_result_does_not_publish_a_check(self):
        self.run_review(superseded=True)
        self.assertFalse(any(call[0].endswith("/check-runs") for call in self.calls))

    def test_legacy_queued_workflow_without_check_token_remains_compatible(self):
        with patch.dict(os.environ, {"OCR_CHECK_TOKEN": ""}), patch.object(review, "api") as api:
            review.publish_check("altinkaya-opensource/test", HEAD, "https://github.com/run", "neutral", 1)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
