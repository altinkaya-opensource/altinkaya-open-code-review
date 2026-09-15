# Open Code Review

Automatic pull request reviews powered by [Alibaba's Open Code Review (OCR)](https://github.com/alibaba/open-code-review).

- Reviews run when a pull request is opened, reopened, or updated.
- Critical and high findings request changes.
- Medium and low findings produce comments.
- Clean reviews approve only when the complete diff was reviewed.
- Findings on changed lines are posted as inline comments.
- Review summaries end with reported input/output tokens, cache hit rate, and OCR duration.
  Missing metrics are omitted. Cache hit rate is reported cached input divided by
  total input, using OpenAI-compatible token accounting; OCR duration excludes
  workflow queue and repository checkout time.

## Model reasoning

Set the GitHub Actions variable `OCR_LLM_REASONING_EFFORT` to `minimal`, `low`,
`medium`, `high`, or `max`. An empty value keeps the provider default. The model
and provider must support the selected value.

This controls the model's `reasoning_effort` request field. OCR's separate
`--effort` option controls the number of review rounds.

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
