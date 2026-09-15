"""Prepare an upstream version update; the workflow manages one persistent PR."""

import json
import os
from pathlib import Path
import re
import urllib.request

from ocr_binary import ASSET_NAME, binary_url, load_pin, version_tuple


def github_json(path, token):
    """Read GitHub metadata without printing credentials or arbitrary response bodies."""
    request = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def proposed_pin(current, release):
    """Propose stable upgrades only, and reject ambiguous same-version replacements."""
    if release.get("draft") or release.get("prerelease"):
        return None
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Upstream release does not have a stable version tag")
    version = tag[1:]
    if version_tuple(version) < version_tuple(current["version"]):
        return None
    assets = [item for item in release.get("assets", []) if item.get("name") == ASSET_NAME]
    if len(assets) != 1 or assets[0].get("browser_download_url") != binary_url(version):
        raise ValueError("Upstream release is missing the expected official Linux binary")
    digest = assets[0].get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Upstream release asset has no verified SHA-256 digest")
    candidate = {"version": version, "sha256": digest.removeprefix("sha256:")}
    if version == current["version"]:
        if candidate["sha256"] != current["sha256"]:
            raise ValueError("Upstream asset digest changed without a version change")
        return None
    return candidate


def main():
    """Write only the version manifest and validated workflow outputs."""
    token = os.environ["GH_TOKEN"]
    bot = github_json("/user", token)
    if bot.get("login") != "altinkaya-bot" or type(bot.get("id")) is not int:
        raise ValueError("The updater token must belong to altinkaya-bot")
    path = Path(__file__).with_name("version.json")
    current = load_pin(path)
    release = github_json("/repos/alibaba/open-code-review/releases/latest", token)
    candidate = proposed_pin(current, release)
    target = candidate or current
    if candidate:
        path.write_text(json.dumps(candidate, indent=2) + "\n")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"version={target['version']}\n")
        output.write(f"previous_version={current['version']}\n")
        output.write(f"release_url=https://github.com/alibaba/open-code-review/releases/tag/v{target['version']}\n")
        output.write(f"bot_committer=altinkaya-bot <{bot['id']}+altinkaya-bot@users.noreply.github.com>\n")
    print(f"OCR version check: {current['version']} -> {target['version']}")


if __name__ == "__main__":
    main()
