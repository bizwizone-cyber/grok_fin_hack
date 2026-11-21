# Release notes and open issues

## Verified

- Development output has 819 hit rows and passes `validate.py` with zero problems.
- Practice labels: 50/50 decisions correct, 100.0 decision score, zero unsafe clearances.
- Only the `system/` code and `output/dev.jsonl` belong in the release bundle. The screening dataset and `shared.txt` are excluded.

## Follow-up issues

1. Complete an explicit byte-for-byte comparison of two fresh runs. The implementation uses stable ordering and deterministic serialization, but the final two-run check was interrupted before completion.
2. Benchmark a cold development run and optimize if the measured runtime is high. A profiling pass was started but stopped before producing a useful profile.
3. The available accuracy check covers 50 practice labels. Review misses and novel free-text formats when unseen evaluation results become available.

## Publication status

- Local source commit: `72097b7 Implement deterministic screening pipeline` (the shareable archive includes the updated README and this issue list).
- Push to `bizwizone-cyber/grok_fin_hack` was rejected with HTTP 403 because the authenticated GitHub account does not have write permission.
- `output/dev.jsonl` is included in this user-requested local bundle. It is derived from the participant screening dataset; the dataset itself is not included.
