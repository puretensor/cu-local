---
name: verify-phase2
description: Verify cu-local Phase 2 — doctor green and the full reference bench per backend class.
---
For each backend class in `docs/backends.md`:
1. `./cu_local.py doctor --endpoint URL` must exit 0.
2. `./cu_local.py bench --endpoint URL [--shim on] [--text-only] [--context N] --repeats 2 --label CLASS` must meet the threshold in `tasks/todo.md`: ≥5/6 for A, ≥4/6 for B.
3. Copy `results.json` to `bench-results/<class>.json` and regenerate the chart with `scripts/render-bench.py`.
