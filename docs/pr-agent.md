# PR-Agent engine

Set the repository Actions variable `OCR_REVIEW_ENGINE` to `pr-agent` to select
[PR-Agent](https://github.com/The-PR-Agent/pr-agent) (MIT). An unset variable
continues to select Alibaba OCR. Existing callers and credentials stay compatible.

The adapter keeps GitHub API access outside Bubblewrap. PR-Agent receives a frozen
diff, a read-only checkout, related PR context and previous findings. Repository
configuration cannot override the provider or run commands. The existing publisher
retains finding history, severity decisions, freshness checks and close cancellation.

## Runtime

An administrator provisions the revision from `ocr/pr_agent_version.json` at
`/opt/pr-agent/<revision>`, with Python 3.12 or newer. In that checkout, use the
upstream-required uv version and `uv sync --frozen --no-dev --no-editable`.
The resulting `.venv` must be readable, but not writable, by runner accounts.
Preload tiktoken's `cl100k_base` and `o200k_base` encodings with
`TIKTOKEN_CACHE_DIR=/opt/pr-agent/<revision>/tiktoken`. The runtime is mounted
read-only; no Docker membership is needed.

The pin includes upstream recovery that retains successful chunks and retries
pending chunks. Upgrade the pin and installed runtime together after validation.
The existing daily Alibaba OCR updater does not update PR-Agent.

## Review limits

Trusted settings are in `ocr/pr_agent_settings.toml`:

- Up to six diff chunks, with a 32,000-token context budget per model call.
- At most two concurrent provider requests per PR.
- A 600-second HTTP timeout. The SDK retries a transient request once; one
  additional PR-Agent attempt uses the same configured model for pending work.
- `OCR_LLM_REASONING_EFFORT` is forwarded unchanged. No alternate provider is used.
- Input/output tokens and provider-reported cache usage are shown when available.

The chunk limit is not a guarantee that every possible PR fits. Omitted or failed
chunks remain incomplete and cannot resolve earlier findings or approve a PR.
Binary/generated files excluded by PR-Agent are not counted as selected files.

The check is named **PR-Agent result**. Critical/high findings request changes,
medium findings comment, and low findings are ignored. Complete reviews with open
findings are neutral; complete clean reviews succeed.

Unset `OCR_REVIEW_ENGINE` to return one repository to Alibaba OCR.
