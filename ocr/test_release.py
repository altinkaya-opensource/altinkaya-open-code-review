"""Validate release selection and binary integrity without network access."""

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import check_upstream
import ocr_binary


class BinaryTests(unittest.TestCase):
    def prepare(self, root, data=b"verified binary"):
        action = root / "action"
        action.mkdir()
        pin = {"version": "1.2.3", "sha256": hashlib.sha256(data).hexdigest()}
        (action / "version.json").write_text(json.dumps(pin))
        return action, pin

    def test_verified_preinstalled_binary_needs_no_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action, pin = self.prepare(root)
            installed = root / "installed"
            installed.write_bytes(b"verified binary")
            with patch.object(ocr_binary.shutil, "which", return_value=str(installed)):
                with patch.object(ocr_binary.urllib.request, "urlopen") as download:
                    self.assertEqual(ocr_binary.resolve_binary(action), installed.resolve())
                    download.assert_not_called()

    def test_download_is_verified_cached_and_checked_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action, pin = self.prepare(root)
            with patch.dict(os.environ, {"RUNNER_TOOL_CACHE": str(root / "cache")}):
                with patch.object(ocr_binary.shutil, "which", return_value=None):
                    with patch.object(ocr_binary.urllib.request, "urlopen", return_value=io.BytesIO(b"verified binary")) as download:
                        binary = ocr_binary.resolve_binary(action)
                        download.assert_called_once_with(ocr_binary.binary_url("1.2.3"), timeout=60)
                    self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
                    with patch.object(ocr_binary.urllib.request, "urlopen") as download:
                        self.assertEqual(ocr_binary.resolve_binary(action), binary)
                        download.assert_not_called()
                    binary.write_bytes(b"tampered")
                    with patch.object(ocr_binary.urllib.request, "urlopen", return_value=io.BytesIO(b"verified binary")):
                        self.assertEqual(ocr_binary.resolve_binary(action).read_bytes(), b"verified binary")

    def test_digest_mismatch_never_publishes_an_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            action, pin = self.prepare(root)
            with patch.dict(os.environ, {"RUNNER_TOOL_CACHE": str(root / "cache")}):
                with patch.object(ocr_binary.shutil, "which", return_value=None):
                    with patch.object(ocr_binary.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong binary")):
                        with self.assertRaisesRegex(ValueError, "SHA-256"):
                            ocr_binary.resolve_binary(action)
            self.assertEqual(list((root / "cache" / "ocr" / "1.2.3" / pin["sha256"]).iterdir()), [])

    def test_invalid_pin_cannot_control_a_path_or_url(self):
        with tempfile.TemporaryDirectory() as temporary:
            action = Path(temporary)
            for version, digest in (("../../outside", "a" * 64), ("1.2.3", "not-a-digest")):
                (action / "version.json").write_text(json.dumps({"version": version, "sha256": digest}))
                with self.assertRaises(ValueError):
                    ocr_binary.resolve_binary(action)


class UpstreamTests(unittest.TestCase):
    def release(self, version="1.2.4", digest="b" * 64):
        return {
            "tag_name": f"v{version}", "draft": False, "prerelease": False,
            "assets": [{"name": ocr_binary.ASSET_NAME, "digest": f"sha256:{digest}",
                        "browser_download_url": ocr_binary.binary_url(version)}],
        }

    def test_new_stable_release_updates_the_pin(self):
        current = {"version": "1.2.3", "sha256": "a" * 64}
        self.assertEqual(check_upstream.proposed_pin(current, self.release()), {"version": "1.2.4", "sha256": "b" * 64})

    def test_equal_or_older_release_does_not_propose_a_downgrade(self):
        current = {"version": "1.2.3", "sha256": "a" * 64}
        self.assertIsNone(check_upstream.proposed_pin(current, self.release("1.2.3", "a" * 64)))
        self.assertIsNone(check_upstream.proposed_pin(current, self.release("1.2.2")))

    def test_draft_and_prerelease_are_ignored(self):
        for field in ("draft", "prerelease"):
            release = self.release()
            release[field] = True
            self.assertIsNone(check_upstream.proposed_pin({"version": "1.2.3", "sha256": "a" * 64}, release))

    def test_same_version_replacement_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError, "without a version change"):
            check_upstream.proposed_pin({"version": "1.2.3", "sha256": "a" * 64}, self.release("1.2.3"))

    def test_invalid_release_data_fails_before_any_write(self):
        current = {"version": "1.2.3", "sha256": "a" * 64}
        for field, value in (("browser_download_url", "https://example.invalid/ocr"), ("digest", "sha256:invalid")):
            release = self.release()
            release["assets"][0][field] = value
            with self.assertRaises(ValueError):
                check_upstream.proposed_pin(current, release)
        release = self.release()
        release["tag_name"] = "v1.2.4\ninvalid"
        with self.assertRaises(ValueError):
            check_upstream.proposed_pin(current, release)


if __name__ == "__main__":
    unittest.main()
