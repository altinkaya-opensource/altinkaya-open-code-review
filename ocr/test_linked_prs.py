"""Verify linked-PR context boundaries and direct dependent refreshes."""

import copy
import unittest

import dependent_reviews
import linked_prs

BASE = "a" * 40
HEAD = "b" * 40
NEXT = "c" * 40
ORG = "altinkaya-opensource"


def pull(repo="source", number=2, private=False):
    return {"number": number, "title": "Change API contract", "body": "Description", "state": "open", "merged": False,
            "head": {"sha": HEAD}, "base": {"sha": BASE, "repo": {
                "id": number, "full_name": f"{ORG}/{repo}", "private": private, "default_branch": "main"}},
            "user": {"login": "developer"}, "changed_files": 1}


class LinkedPRTests(unittest.TestCase):
    def test_reference_formats_and_issue_numbers(self):
        body = (f"Related-PR: {ORG}/backend#2, #3\nDepends-On: frontend#4\n"
                f"See https://github.com/{ORG}/another/pull/5#discussion_r123\nOrdinary issue #99")
        self.assertEqual(linked_prs.references(body, f"{ORG}/current"), [
            (f"{ORG}/another", 5), (f"{ORG}/backend", 2), (f"{ORG}/current", 3), (f"{ORG}/frontend", 4),
        ])

    def target(self, private=False):
        target = pull("target", 1, private)
        target["body"] = f"Related-PR: {ORG}/source#2"
        return target

    def request_for(self, source, *, permission="read", patch="+new API", seen=None):
        def request(path, payload=None):
            if seen is not None:
                seen.append(path)
            if "/collaborators/" in path:
                return {"permission": permission}
            if "/files?" in path:
                return [{"filename": "api.py", "status": "modified", "patch": patch,
                         "additions": sum(line.startswith("+") for line in (patch or "").splitlines()), "deletions": 0}]
            return copy.deepcopy(source)
        return request

    def test_cross_repo_context_contains_frozen_diff(self):
        context = linked_prs.load(f"{ORG}/target", 1, self.target(), self.request_for(pull()))
        self.assertTrue(context["complete"])
        self.assertIn("+new API", context["text"])
        self.assertEqual(context["snapshots"][0]["snapshot"]["head"], HEAD)
        self.assertTrue(linked_prs.unchanged(context, self.request_for(pull())))

    def test_private_source_is_not_sent_to_a_public_pr(self):
        source = pull(private=True)
        source.update(title="PRIVATE_TITLE", body="PRIVATE_DESCRIPTION")
        calls = []
        context = linked_prs.load(f"{ORG}/target", 1, self.target(), self.request_for(source, seen=calls))
        self.assertFalse(context["complete"])
        self.assertNotIn("PRIVATE_", context["text"])
        self.assertFalse(any("/files?" in call for call in calls))
        self.assertEqual(context["snapshots"], [])

    def test_private_cross_repo_requires_author_access(self):
        for permission, expected in (("read", True), ("none", False)):
            with self.subTest(permission=permission):
                context = linked_prs.load(f"{ORG}/target", 1, self.target(True),
                                         self.request_for(pull(private=True), permission=permission))
                self.assertEqual(context["complete"], expected)

    def test_large_or_unavailable_patch_disables_automatic_approval(self):
        for patch in (None, "+large change\n" * 10000):
            with self.subTest(binary=patch is None):
                context = linked_prs.load(f"{ORG}/target", 1, self.target(), self.request_for(pull(), patch=patch))
                self.assertFalse(context["complete"])
                self.assertLessEqual(len(context["text"].encode()), 56000)

    def test_changed_dependency_invalidates_review(self):
        context = linked_prs.load(f"{ORG}/target", 1, self.target(), self.request_for(pull()))
        changed = pull()
        changed["head"]["sha"] = NEXT
        self.assertFalse(linked_prs.unchanged(context, self.request_for(changed)))

    def test_description_edits_do_not_count_as_code_fixes(self):
        target = self.target()
        context = {"snapshots": []}
        original = linked_prs.revision(target, context)
        target["body"] = "Different explanation"
        self.assertEqual(original, linked_prs.revision(target, context))

    def test_foreign_organization_is_not_fetched(self):
        target = self.target()
        target["body"] = "Related-PR: https://github.com/outsider/project/pull/9"
        def unexpected(_):
            raise AssertionError("Foreign repository was fetched")
        context = linked_prs.load(f"{ORG}/target", 1, target, unexpected)
        self.assertFalse(context["complete"])

    def test_external_changelog_links_are_not_implicit_dependencies(self):
        self.assertEqual(linked_prs.references("Upstream changelog: https://github.com/outsider/project/pull/9",
                                               f"{ORG}/target"), [])

    def test_a_partial_github_patch_is_not_treated_as_complete(self):
        request = self.request_for(pull())
        def truncated(path, payload=None):
            data = request(path, payload)
            if "/files?" in path:
                data[0]["additions"] = 20
            return data
        self.assertFalse(linked_prs.load(f"{ORG}/target", 1, self.target(), truncated)["complete"])

    def test_visibility_change_during_fetch_discards_related_contents(self):
        count = 0
        source = pull()
        request = self.request_for(source)
        def changed(path, payload=None):
            nonlocal count
            data = request(path, payload)
            if "/files?" not in path:
                count += 1
                if count > 1:
                    data["base"]["repo"]["private"] = True
            return data
        context = linked_prs.load(f"{ORG}/target", 1, self.target(), changed)
        self.assertFalse(context["complete"])
        self.assertNotIn("+new API", context["text"])
        self.assertEqual(context["snapshots"], [])

    def test_direct_source_update_dispatches_dependent_but_not_itself(self):
        source = pull()
        rows = [
            {"nameWithOwner": f"{ORG}/target", "databaseId": 1, "isPrivate": False, "isArchived": False,
             "defaultBranchRef": {"name": "main"}, "pullRequests": {
                 "nodes": [{"number": 1, "body": f"Related-PR: {ORG}/source#2", "author": {"login": "developer"}}],
                 "pageInfo": {"hasNextPage": False}}},
            {"nameWithOwner": f"{ORG}/source", "databaseId": 2, "isPrivate": False, "isArchived": False,
             "defaultBranchRef": {"name": "main"}, "pullRequests": {
                 "nodes": [{"number": 2, "body": "Related-PR: #2", "author": {"login": "developer"}}],
                 "pageInfo": {"hasNextPage": False}}},
        ]
        dispatched = []
        def request(path, payload=None):
            if path == "/graphql":
                return {"data": {"organization": {"repositories": {"nodes": rows, "pageInfo": {"hasNextPage": False}}}}}
            if path.endswith("automatic-ocr-review.yml"):
                return {"state": "active"}
            dispatched.append((path, payload))
        count = dependent_reviews.refresh(f"{ORG}/source", 2, source, request)
        self.assertEqual(count, 1)
        self.assertEqual(dispatched, [(f"/repos/{ORG}/target/actions/workflows/automatic-ocr-review.yml/dispatches",
                                       {"ref": "main", "inputs": {"pull_request_number": "1"}})])

    def test_private_dependency_cannot_signal_its_updates_to_public_pr(self):
        row = {"nameWithOwner": f"{ORG}/target", "databaseId": 1, "isPrivate": False, "isArchived": False,
               "defaultBranchRef": {"name": "main"}, "pullRequests": {
                   "nodes": [{"number": 1, "body": f"Related-PR: {ORG}/source#2", "author": {"login": "outsider"}}],
                   "pageInfo": {"hasNextPage": False}}}
        def request(path, payload=None):
            self.assertEqual(path, "/graphql")
            return {"data": {"organization": {"repositories": {"nodes": [row], "pageInfo": {"hasNextPage": False}}}}}
        self.assertEqual(dependent_reviews.refresh(f"{ORG}/source", 2, pull(private=True), request), 0)


if __name__ == "__main__":
    unittest.main()
