# Review behavior and configuration

[← Project overview](../README.md)

Automatic pull request reviews powered by [Alibaba's Open Code Review (OCR)](https://github.com/alibaba/open-code-review).

- Reviews run when a pull request is opened, reopened, or updated.
- Closing or merging a PR cancels its pending/running review.
- Critical and high findings request changes.
- Medium findings produce comments.
- Low findings are ignored: no finding comments, history entries, or neutral check.
  A complete review with only low findings passes like a clean review.
- Clean reviews approve only when all files selected by OCR were reviewed.
- Intentional file exclusions do not fail CI. If OCR selects no files, CI passes
  without automatic approval. Previously open findings on excluded files stay open.
- The **OCR result** check is neutral (gray) when a complete review has open
  findings, successful (green) when none remain, and failed (red) when the review
  is incomplete or errors. Retained findings from earlier reviews also count.
  The Actions execution job and GitHub's native review decision remain separate.
- Findings on changed lines are posted as inline comments.
- Review summaries end with reported input/output tokens, cache hit rate, and OCR duration.
  Missing metrics are omitted. Cache hit rate is reported cached input divided by
  total input, using OpenAI-compatible token accounting; OCR duration excludes
  workflow queue and repository checkout time.

## Adapt it for your organization

This repository contains Altinkaya's reference deployment. Its organization and
bot checks intentionally reject unrelated repositories. Fork it to run with your
own accounts and infrastructure.

1. Replace the organization and bot identities in [review.py](../ocr/review.py),
   [linked_prs.py](../ocr/linked_prs.py), [check_upstream.py](../ocr/check_upstream.py)
   and the [workflow files](../.github/workflows). Update the repository-name
   validation as well as the constants, and point both reusable-workflow and
   composite-action references to your fork.
2. Prepare a self-hosted Linux x64 runner with Python 3, Git and Bubblewrap.
   Match its labels to the workflow and restrict its runner group to trusted
   workflow references. Configure network restrictions for your environment;
   Bubblewrap's network access is enabled for LLM requests.
3. Add Actions secrets `OCR_BOT_TOKEN` and `OCR_LLM_TOKEN`, plus variables
   `OCR_LLM_URL`, `OCR_LLM_MODEL` and optional `OCR_LLM_REASONING_EFFORT`.
   Use a bot credential that can read the intended repositories, submit reviews
   and dispatch dependent workflows. The job token provides `checks: write`.
4. Test on one disposable PR, publish your tested `latest` tag, then add the
   [caller workflow](../.github/workflows/automatic-ocr-review.yml) to the
   repositories you intend to review.

The configured LLM provider receives review source and context. Keep credentials
in Actions secrets and choose a provider appropriate for the code you review.

## Related pull requests

Link a PR in the same organization from the PR description, or use explicit
declarations (short repository names use the configured organization):

```text
Related-PR: example-api#123
Depends-On: #45
```

Full GitHub PR URLs are also recognized. Ordinary links to external upstream
changelogs are ignored; explicitly declared external dependencies are reported
as unsupported. Self-links are ignored and links are not followed recursively.

The review receives the target description and each linked PR's description,
state, frozen commit identities and diff. Up to five direct links fit in a
bounded context budget. Missing, denied or truncated dependency diffs prevent
automatic approval. Private source is never supplied to a public PR; private
cross-repository context also requires the target PR author to have read access
to the source repository. Only link private repositories whose content may be
discussed in the consuming private PR.

Editing a PR description reruns its review. Updates and closure of a linked PR
refresh its directly dependent open PRs through `workflow_dispatch`; dispatched
reviews do not fan out again, preventing cycles. This reads current open PR
declarations instead of relying on a delayed search index. Disabled or missing
OCR workflows are skipped. A related revision changing during analysis discards
the stale result and queues a fresh review.

The caller must subscribe to `edited` and declare the `workflow_dispatch` string
input `pull_request_number`, as shown in the [caller workflow](../.github/workflows/automatic-ocr-review.yml). The input
can also be used to request a fresh review manually.

The caller must grant `checks: write` as well as `contents: read`. The reusable
workflow uses the short-lived job token for the result check; this token never
enters the OCR sandbox. Superseded or closed PR reviews do not publish a new
result check. Result checks are created when analysis finishes, so cancellation
does not leave an extra pending check behind.

## Finding history

OCR reads its previous GitHub reviews before each analysis. Only reviews
authenticated as the configured bot account are accepted. Existing reviews in
the older OCR format are imported, including inline findings. Structured state
is carried in an encoded marker in each new review; no separate database or
issue comment is required. Encoding is not encryption: state has the same
visibility as the PR review.

Previous findings are supplied to the model with stable IDs. The model can
reuse an ID when describing the same issue differently; matching also falls
back to the file path and normalized finding text. Each review shows open
findings and a collapsed resolved section; a returning finding is marked reopened.

An absent finding resolves only after a complete review of changed code or
dependency evidence. Same-input reruns and incomplete reviews do not close
findings or downgrade blocking severity. Description-only edits do not count
as code fixes. An earlier unresolved critical/high finding still requests
changes even if a same-input rerun reports no new findings. Resolution means
the issue was not found in that complete new review, not that a separate repair
test proved its absence.

OCR's manifest defines the selected files. Coverage compares completed/reused
files with that selected set, not every Git diff path. Summaries show selected,
changed and excluded counts. A failed, waived or unfinished selected file still
fails CI, as do invalid commit identities and finding-delivery errors. Excluding
a file does not resolve or downgrade a previous finding on it.

The latest 20 resolved records are retained subject to the review body limit;
active findings are never silently discarded. Malformed or oversized active
history prevents automatic approval. Finding history survives runner cache
cleanup because it is stored in GitHub reviews.

Low findings from earlier reviews are excluded from future context and history;
they are not labelled as fixed. Existing published comments are left in place.

## Model reasoning

Set the GitHub Actions variable `OCR_LLM_REASONING_EFFORT` to `minimal`, `low`,
`medium`, `high`, or `max`. An empty value keeps the provider default. The model
and provider must support the selected value.

This controls the model's `reasoning_effort` request field. OCR's separate
`--effort` option controls the number of review rounds.

Automatic reviews use `--effort low`: at most one review round per file group.
This does not lower the configured model reasoning effort. Review rules focus
on concrete regressions in the selected diff, direct callers, linked contracts
and previous findings; they discourage unrelated audits and repeated exploration.
These scope instructions guide the model, not a hard cap on tool calls.

The workflow explicitly uses `--timeout 30` to retain the previous effective
30-minute group deadline when reducing OCR from two rounds to one. The pinned
CLI multiplies its timeout by the round count. Reducing rounds therefore does
not accidentally halve the time available to finish the first pass.

Each LLM HTTP request has a 600-second timeout. The review-group and workflow-job
timeouts are separate limits.

## Upstream version updates

`ocr/version.json` pins the upstream CLI version and its official Linux binary
SHA-256. Reviews verify a preinstalled or cached binary against that pin, and
download the pinned release when necessary. Downloads are checked before use.

The `Check upstream OCR release` workflow runs daily at 03:17 UTC and can also be
run manually. It opens or updates a single pull request on
`codex/ocr-version-update`. Prereleases and downgrades are ignored; missing
checksums and unexpected same-version asset replacements fail the check.

After reviewing and merging the version PR, publish the tested commit through
`latest` to activate the new binary. The daily check does not merge or publish it.

## Repository caching and incomplete reviews

Each runner keeps a local Git object cache, keyed by GitHub's numeric repository
identity. Reviews use a fresh checkout populated from cached objects; only missing
commits and blobs need fetching. An unchanged base/head pair can be prepared
without contacting the Git remote. GitHub credentials are never stored in the
cache, and the sandbox receives only the read-only review checkout.

Incomplete reviews report the CLI exit code, terminal state and failure categories.
Detailed failure evidence stays in private runner-local files for troubleshooting;
it is not uploaded to GitHub. Runner maintenance should remove unused repository
caches after 30 days and failure reports after 7 days.

## Checks

```sh
python3 -m unittest discover -s ocr -p 'test_*.py'
```

## Publishing updates

Consumers reference the reusable workflow with `@latest`. The workflow also
loads this repository's action through `@latest`, so consuming repositories
do not need an update for each release.

After the selected commit passes its checks, publish it by moving the tag:

```sh
git tag -f latest <tested-commit>
git push --force origin refs/tags/latest
```

Pushing to `main` alone does not move `latest`. To roll back, move the tag to
the previous tested commit with the same commands.
