"""Resolve a checksum-verified OCR binary from the reviewed version pin."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request


ASSET_NAME = "opencodereview-linux-amd64"


def version_tuple(version):
    """Accept only stable three-component versions, never paths or shell text."""
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("OCR version must be a stable major.minor.patch version")
    return tuple(int(part) for part in version.split("."))


def load_pin(path):
    """Validate the version and digest before constructing paths or URLs."""
    pin = json.loads(path.read_text())
    version_tuple(pin.get("version"))
    if not isinstance(pin.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
        raise ValueError("OCR sha256 must be a lowercase SHA-256 digest")
    return pin


def binary_url(version):
    """Use only the official upstream release asset location."""
    version_tuple(version)
    return f"https://github.com/alibaba/open-code-review/releases/download/v{version}/{ASSET_NAME}"


def valid_binary(path, digest):
    """Check cached and preinstalled files before every use."""
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == digest


def resolve_binary(action):
    """Reuse verified binaries and atomically cache downloads without root access."""
    pin = load_pin(action / "version.json")
    installed = shutil.which("ocr")
    if installed and valid_binary(Path(installed), pin["sha256"]):
        return Path(installed).resolve()
    cache = Path(os.environ.get("RUNNER_TOOL_CACHE") or Path.home() / ".cache")
    directory = cache / "ocr" / pin["version"] / pin["sha256"]
    binary = directory / "ocr"
    if valid_binary(binary, pin["sha256"]):
        return binary
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as destination:
        temporary = Path(destination.name)
        try:
            with urllib.request.urlopen(binary_url(pin["version"]), timeout=60) as response:
                shutil.copyfileobj(response, destination)
            destination.flush()
            if not valid_binary(temporary, pin["sha256"]):
                raise ValueError("Downloaded OCR binary does not match the pinned SHA-256")
            temporary.chmod(0o755)
            os.replace(temporary, binary)
        finally:
            temporary.unlink(missing_ok=True)
    return binary
