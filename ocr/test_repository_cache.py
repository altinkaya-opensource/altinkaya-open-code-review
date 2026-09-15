"""Exercise cold/warm Git preparation using a real local partial-clone server."""

import os
import errno
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import review
import repository_cache


class RepositoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.command(self.source, "init", "-q", "-b", "main")
        self.command(self.source, "config", "user.name", "Test")
        self.command(self.source, "config", "user.email", "test@example.invalid")
        (self.source / "example.py").write_text("value = 1\n")
        self.base = self.commit("base")
        (self.source / "example.py").write_text("value = 2\n")
        self.head = self.commit("head")
        self.remote = self.root / "remote.git"
        self.command(self.root, "clone", "--bare", "-q", str(self.source), str(self.remote))
        self.command(self.remote, "config", "uploadpack.allowFilter", "true")
        self.command(self.remote, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.cache = self.root / "cache"
        original_git = review.git

        def local_origin(repo, *args, token=None):
            if args[:3] == ("remote", "add", "origin"):
                args = (*args[:3], self.remote.as_uri())
            return original_git(repo, *args, token=token)

        replacement = patch.object(review, "git", side_effect=local_origin)
        replacement.start()
        self.addCleanup(replacement.stop)

    def command(self, directory, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")
        return subprocess.check_output(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=directory, env=env,
            stderr=subprocess.PIPE, text=True,
        ).strip()

    def commit(self, message):
        self.command(self.source, "add", ".")
        self.command(self.source, "commit", "-qm", message)
        return self.command(self.source, "rev-parse", "HEAD")

    def prepare(self, name, *, head=None, repository_id=42):
        root = self.root / name
        root.mkdir()
        return review.prepare_repository(
            root, "altinkaya-opensource/example", self.base, head or self.head,
            "fake-test-token", repository_id, self.cache,
        )

    def test_same_commits_work_offline_after_cold_preparation(self):
        first, first_base, first_patches = self.prepare("cold")
        self.remote.rename(self.root / "offline.git")
        second, second_base, second_patches = self.prepare("warm")
        self.assertEqual(first_base, second_base)
        self.assertEqual(first_patches, second_patches)
        self.assertEqual((second / "example.py").read_text(), "value = 2\n")
        self.assertFalse((second / ".git/objects/info/alternates").exists())
        self.assertNotIn("fake-test-token", (self.cache / "42.git/config").read_text())
        self.assertNotIn("fake-test-token", (first / ".git/config").read_text())

    def test_incremental_fetch_keeps_exact_head_and_full_diff(self):
        self.prepare("first")
        (self.source / "new.py").write_text("new = True\n")
        new_head = self.commit("next")
        self.command(self.source, "push", "-q", str(self.remote), "main")
        repo, base, patches = self.prepare("next", head=new_head)
        self.assertEqual(self.command(repo, "rev-parse", "HEAD"), new_head)
        self.assertEqual(base, self.base)
        self.assertEqual(set(patches), {"example.py", "new.py"})

    def test_recreated_repository_gets_a_separate_cache(self):
        self.prepare("old", repository_id=42)
        self.prepare("new", repository_id=43)
        self.assertTrue((self.cache / "42.git/objects").is_dir())
        self.assertTrue((self.cache / "43.git/objects").is_dir())

    def test_failed_fetch_does_not_replace_cached_refs(self):
        self.prepare("first")
        self.remote.rename(self.root / "offline.git")
        with self.assertRaises(RuntimeError):
            self.prepare("failure", head="f" * 40)
        self.assertEqual(self.command(self.cache / "42.git", "rev-parse", "refs/heads/review-head"), self.head)
        self.prepare("still-usable")

    def test_invalid_repository_identity_is_rejected(self):
        for identity in (True, -1, "../../elsewhere"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                review.prepare_repository(self.root, "altinkaya-opensource/example", self.base,
                                          self.head, "", identity, self.cache)

    def test_failed_cross_device_copy_does_not_publish_partial_objects(self):
        source = self.root / "objects"
        (source / "aa").mkdir(parents=True)
        (source / "aa" / ("b" * 38)).write_text("object contents")
        destination = self.root / "destination"

        def failed_copy(original, pending):
            pending.write_text("partial")
            raise OSError("disk full")

        with patch.object(repository_cache.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")):
            with patch.object(repository_cache.shutil, "copy2", side_effect=failed_copy):
                with self.assertRaises(OSError):
                    repository_cache.copy_objects(source, destination)
        self.assertEqual(list((destination / "aa").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
