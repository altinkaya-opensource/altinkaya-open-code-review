# Open Code Review

Automatic pull request reviews powered by [Alibaba's Open Code Review (OCR)](https://github.com/alibaba/open-code-review).

- Reviews run when a pull request is opened, reopened, or updated.
- Critical and high findings request changes.
- Medium and low findings produce comments.
- Clean reviews approve only when the complete diff was reviewed.
- Findings on changed lines are posted as inline comments.

## Model reasoning

Set the GitHub Actions variable `OCR_LLM_REASONING_EFFORT` to `minimal`, `low`,
`medium`, `high`, or `max`. An empty value keeps the provider default. The model
and provider must support the selected value.

This controls the model's `reasoning_effort` request field. OCR's separate
`--effort` option controls the number of review rounds.

## Checks

```sh
python3 -m unittest discover -s ocr -p 'test_*.py'
```
