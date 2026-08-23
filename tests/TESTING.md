# Test policy

- Search for the existing contract owner before adding a test. Extend its parametrization when the new case exercises the same layer.
- Add a cross-layer test only for a real serialization, authentication, or adapter seam.
- Register every new `tests/test_*.py` module in `tests/conftest.py`; every collected test must resolve to exactly one of `fast`, `subsystem`, or `scientific`.
- Use immutable module- or session-scoped fixtures for expensive frozen inputs. Give mutation and corruption cases ordinary private clone-on-write copies.
- Put reusable builders in `tests/support/`. Collected test modules must not import from other collected test modules.
- Do not use sleeps, randomness, network access, or fixed shared writable paths.
- A test expected to exceed one second needs a concise justification beside the test or its tier entry.
- Codex implementation reports must identify tests added, tests extended, tier assignments, and expected runtime impact.

Maintained commands are `scripts/test-fast`, `scripts/test-subsystem`, `scripts/test-full`, and `scripts/test-failed`. The first three run the viewer JavaScript contracts after Python succeeds; `test-failed` is Python-only because the Node test runner has no pytest last-failed state.
