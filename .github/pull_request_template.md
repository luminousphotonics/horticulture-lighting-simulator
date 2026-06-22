## Summary

-

## Validation

- [ ] `PYTHONPATH=src python -m pytest -q tests`
- [ ] `npm ci`
- [ ] `PYTHONPATH=src python scripts/dev/baseline.py`
- [ ] `git diff --check`

## Checklist

- [ ] I kept the change scoped.
- [ ] I updated docs or tests where useful.
- [ ] I did not change protected scientific artifacts unexpectedly.
- [ ] I did not commit secrets, runtime state, caches, or expanded private data.
