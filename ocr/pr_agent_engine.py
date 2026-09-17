"""Run the pinned PR-Agent installation without exposing GitHub credentials."""

import json
import os
from pathlib import Path
import re
import subprocess


def runtime_path(action):
    """Resolve the administrator-installed, immutable Python environment."""
    revision = json.loads((action / "pr_agent_version.json").read_text())["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid PR-Agent revision")
    runtime = Path("/opt/pr-agent") / revision
    if not (runtime / ".venv/bin/python").is_file():
        raise RuntimeError("The pinned PR-Agent runtime is not installed")
    return runtime


def sandbox_command(repo, output, action, runtime, input_path):
    """Mount trusted dependencies and read-only input in a fresh sandbox."""
    return [
        "bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--share-net",
        "--cap-drop", "ALL", "--ro-bind", "/usr", "/usr",
        "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
        "--tmpfs", "/tmp", "--dir", "/home/ocr",
        "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", str(runtime), str(runtime),
        "--ro-bind", str(action), "/adapter",
        "--ro-bind", str(repo), "/work", "--bind", str(output), "/output",
        "--ro-bind", str(input_path), "/input.json", "--chdir", "/tmp",
        str(runtime / ".venv/bin/python"), "-I", "/adapter/pr_agent_runner.py",
    ]


def run(repo, output, action, base, head, patches, context):
    """Keep the provider credential ephemeral and all source-bearing logs private."""
    runtime = runtime_path(action)
    input_path = output.parent / "pr-agent-input.json"
    input_path.write_text(json.dumps({
        "base": base, "head": head, "diff": "\n".join(patches.values()), "context": context,
    }))
    env = {
        "PATH": "/usr/bin:/bin", "HOME": "/home/ocr", "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0", "TIKTOKEN_CACHE_DIR": str(runtime / "tiktoken"),
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "OCR_LLM_URL": os.environ["OCR_LLM_URL"],
        "OCR_LLM_MODEL": os.environ["OCR_LLM_MODEL"],
        "OCR_LLM_TOKEN": os.environ["OCR_LLM_TOKEN"],
        "OCR_LLM_REASONING_EFFORT": os.environ.get("OCR_LLM_REASONING_EFFORT", ""),
    }
    with (output / "diagnostics.log").open("w") as diagnostics:
        result = subprocess.run(
            sandbox_command(repo, output, action, runtime, input_path), env=env,
            stdout=diagnostics, stderr=subprocess.STDOUT, check=False,
        )
    result_path = output / "review.json"
    if not result_path.is_file():
        from review import save_failure_report
        save_failure_report({"status": "failed"}, result.returncode, output)
        raise RuntimeError(f"PR-Agent produced no JSON result (exit {result.returncode})")
    return json.loads(result_path.read_text()), result.returncode
