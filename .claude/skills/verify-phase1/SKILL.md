---
name: verify-phase1
description: Verify cu-local Phase 1 (core CLI + shim) — unit tests on 3.9+ and a live Calculator pass native and via shim.
---
1. `python3 -m unittest discover -s tests -v`. Must be green, with no failures.
2. On a Mac with ChatGPT.app and an endpoint that serves both `/v1/responses` and `/v1/chat/completions` (for example vLLM):
   - `./cu_local.py bench --endpoint URL --shim off --tasks calculator --repeats 2` must print `2/2 passed`.
   - `./cu_local.py bench --endpoint URL --shim on --tasks calculator --repeats 2` must print `2/2 passed`.
3. In `~/.cu-local/bench/<ts>/results.json`, every result must have `shell_calls == 0`.
