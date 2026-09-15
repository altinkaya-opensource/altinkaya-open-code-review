"""Regression coverage for finding identity, trust and conservative resolution."""

import unittest

import finding_history as history
import review
from test_review import BASE, HEAD, PATCHES, complete_result

BOT_ID = 7
NEXT = "c" * 40


def finding(content="A concrete bug", path="example.py"):
    return {"path": path, "content": content, "severity": "high", "start_line": 2}


def state_with_finding():
    item = history.identify(finding(), [])
    return history.reconcile({"findings": []}, [item], 42, 1, HEAD, "old-input", True)


class FindingHistoryTests(unittest.TestCase):
    def test_open_fixed_and_reopened_with_reworded_content(self):
        first = state_with_finding()
        key = first["findings"][0]["id"]
        fixed = history.reconcile(first, [], 42, 1, NEXT, "new-input", True)
        self.assertEqual(fixed["findings"][0]["state"], "resolved")
        reworded = history.identify(finding(f"[OCR-ID:{key}] The same bug described differently"), fixed["findings"])
        reopened = history.reconcile(fixed, [reworded], 42, 1, "d" * 40, "third-input", True)
        item = reopened["findings"][0]
        self.assertEqual(item["id"], key)
        self.assertEqual(item["state"], "open")
        self.assertEqual(item["reopened_count"], 1)
        self.assertNotIn("OCR-ID", item["content"])

    def test_same_evidence_or_partial_review_cannot_resolve(self):
        first = state_with_finding()
        for head, fingerprint, complete in ((HEAD, "old-input", True), (NEXT, "new-input", False)):
            with self.subTest(complete=complete):
                state = history.reconcile(first, [], 42, 1, head, fingerprint, complete)
                self.assertEqual(state["findings"][0]["state"], "open")

    def test_duplicate_or_unverified_downgrades_preserve_blocking_severity(self):
        first = state_with_finding()
        key = first["findings"][0]["id"]
        low = history.identify({**finding(f"[OCR-ID:{key}] Same issue"), "severity": "low"}, first["findings"])
        for complete, fingerprint in ((True, "old-input"), (False, "new-input")):
            state = history.reconcile(first, [low], 42, 1, HEAD, fingerprint, complete)
            self.assertEqual(state["findings"][0]["severity"], "high")
        high = history.identify(finding(f"[OCR-ID:{key}] Same issue"), first["findings"])
        state = history.reconcile(first, [high, low], 42, 1, NEXT, "new-input", True)
        self.assertEqual(state["findings"][0]["severity"], "high")

    def test_a_complete_retry_can_resolve_a_fix_not_verified_by_a_partial_run(self):
        first = state_with_finding()
        partial = history.reconcile(first, [], 42, 1, NEXT, "new-input", False)
        fixed = history.reconcile(partial, [], 42, 1, NEXT, "new-input", True)
        self.assertEqual(fixed["findings"][0]["state"], "resolved")

    def test_returned_id_cannot_steal_a_different_files_finding(self):
        first = state_with_finding()
        key = first["findings"][0]["id"]
        other = history.identify(finding(f"[OCR-ID:{key}] Different issue", "other.py"), first["findings"])
        self.assertNotEqual(other["finding_id"], key)

    def test_state_authorship_and_scope(self):
        state = state_with_finding()
        body = history.append_state("Review", state)
        reviews = [
            {"id": 11, "user": {"id": BOT_ID}, "commit_id": HEAD, "body": body, "state": "CHANGES_REQUESTED"},
            {"id": 12, "user": {"id": 999}, "commit_id": HEAD, "body": body, "state": "COMMENTED"},
        ]
        loaded, revision = history.load("/pulls/1", 42, 1, lambda _: reviews, BOT_ID)
        self.assertEqual(revision, 11)
        self.assertEqual(loaded, state)
        with self.assertRaises(ValueError):
            history.load("/pulls/2", 42, 2, lambda _: reviews, BOT_ID)

    def test_malformed_marker_does_not_reset_history(self):
        row = {"id": 1, "user": {"id": BOT_ID}, "commit_id": HEAD,
               "body": history.MARKER + "broken -->", "state": "COMMENTED"}
        with self.assertRaises(ValueError):
            history.load("/pulls/1", 42, 1, lambda _: [row], BOT_ID)

    def test_legacy_inline_finding_survives_a_later_incomplete_empty_review(self):
        reviews = [
            {"id": 1, "user": {"id": BOT_ID}, "commit_id": HEAD, "state": "CHANGES_REQUESTED",
             "body": f"Coverage: 1/1 changed files. Findings: 1.\n<!-- altinkaya-ocr:{HEAD} -->"},
            {"id": 2, "user": {"id": BOT_ID}, "commit_id": NEXT, "state": "COMMENTED",
             "body": f"Coverage: 0/1 changed files. Findings: 0.\nReview incomplete\n<!-- altinkaya-ocr:{NEXT} -->"},
        ]
        def request(path):
            if "/reviews/1/comments?" in path:
                return [{"user": {"id": BOT_ID}, "body": "**HIGH**\n\nA concrete bug", "path": "example.py", "line": 2}]
            return reviews
        state, revision = history.load("/pulls/1", 42, 1, request, BOT_ID)
        self.assertEqual(revision, 2)
        self.assertEqual(len(state["findings"]), 1)
        self.assertEqual(state["findings"][0]["last_seen_head"], HEAD)
        same = history.reconcile(state, [], 42, 1, HEAD, "new-format-input", True)
        self.assertEqual(same["findings"][0]["state"], "open")

    def test_fingerprint_is_stable_across_line_moves_and_whitespace(self):
        first = history.identify(finding("A   concrete BUG"), [])
        second = history.identify({**finding("a concrete bug"), "start_line": 90}, [])
        self.assertEqual(first["finding_id"], second["finding_id"])

    def test_active_history_blocks_approval_on_an_unchanged_clean_rerun(self):
        pr = {"head": {"sha": HEAD}, "base": {"sha": BASE, "repo": {"id": 42}}}
        context = {"snapshots": [], "warnings": [], "complete": True}
        fingerprint = history.digest(review.linked_prs.revision(pr, context))
        previous = history.reconcile({"findings": []}, [history.identify(finding(), [])], 42, 1, HEAD, fingerprint, True)
        payload, complete = review.tracked_payload(complete_result(), 0, PATCHES, BASE, HEAD,
                                                  "developer", "https://github.com/run", previous, context, pr, 1)
        self.assertTrue(complete)
        self.assertEqual(payload["event"], "REQUEST_CHANGES")
        self.assertIn("earlier findings remain open", payload["body"])

    def test_incomplete_related_context_preserves_previous_findings(self):
        pr = {"head": {"sha": NEXT}, "base": {"sha": BASE, "repo": {"id": 42}}}
        context = {"snapshots": [], "warnings": ["Context unavailable"], "complete": False}
        result = complete_result()
        result["manifest"]["input"]["resolved_head"] = NEXT
        payload, complete = review.tracked_payload(result, 0, PATCHES, BASE, NEXT, "developer",
            "https://github.com/run", state_with_finding(), context, pr, 1)
        self.assertFalse(complete)
        self.assertEqual(payload["event"], "REQUEST_CHANGES")
        self.assertIn("Related context incomplete", payload["body"])

    def test_resolved_history_budget_keeps_newest_resolutions(self):
        previous = {"findings": [], "sequence": 0}
        for i in range(25):
            item = history.identify(finding(f"Bug {i}"), [])
            previous = history.reconcile(previous, [item], 42, 1, HEAD, str(i), True)
        state = history.reconcile(previous, [], 42, 1, NEXT, "fixed", True)
        resolved = [item for item in state["findings"] if item["state"] == "resolved"]
        self.assertEqual(len(resolved), 20)
        self.assertIn("Bug 24", {item["content"] for item in resolved})


if __name__ == "__main__":
    unittest.main()
