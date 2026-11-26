# DX cases: moved

The 16 Dexter end-to-end cases (DX01–DX16) now live in `tests/test-queries.md` under **Dexter catalog (DX)**. Edit and verify them there.

```bash
python tests/test_queries.py --only DX --repeat 3
```

Two case-level keys were added to the runner for them (see the case format at the top of `tests/test-queries.md`):

- `xfail: true` marks a case whose answer exists only in images. It is reported as XFAIL, or XPASS once it passes.
- `baseline_fail: page-tag` marks a case that fails because of a known recorded bug. It is reported as KFAIL.

Neither kind counts toward failures or the exit code.
