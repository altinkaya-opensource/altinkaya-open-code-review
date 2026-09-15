# Open Code Review

Automatic pull request reviews using the OCR CLI.

- Reviews run when a pull request is opened, reopened, or updated.
- Critical and high findings request changes.
- Medium and low findings produce comments.
- Clean reviews approve only when the complete diff was reviewed.
- Findings on changed lines are posted as inline comments.

## Checks

```sh
python3 -m unittest discover -s ocr -p 'test_*.py'
```
