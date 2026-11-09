# cu-local

Runs OpenAI Codex Computer Use (the `cua_repl` MCP server from the user's own ChatGPT.app) with a self-hosted model behind any OpenAI-compatible endpoint. Success: on a Mac with ChatGPT.app, `cu-local doctor` is green and the three reference tasks pass, checked by machine, for every backend class we test.

## Commands
- Test: `python3 -m unittest discover -s tests -v` (offline, Linux or macOS, Python 3.9+)
- Run: `./cu_local.py run --endpoint http://HOST:PORT/v1 "task"` (macOS + ChatGPT.app only)
- Doctor: `./cu_local.py doctor --endpoint URL [--save]`
- Bench: `./cu_local.py bench --endpoint URL --repeats 2` (writes `~/.cu-local/bench/<ts>/results.json`)
- Shim only: `./cu_local.py shim --upstream URL --port 8077`
- Leak scan: `scripts/leak-scan.sh` (must be clean before any push to the public repo)

## Layout
- `cu_local.py` is the whole tool, a single stdlib file. `install.sh` copies it to `~/.local/bin/cu-local`.
- `tests/` holds offline tests on synthetic fixtures. `docs/` holds recipes and architecture. `assets/` holds SVGs.

## Invariants (do not violate)
- Python 3.9 compatible, stdlib only: no `X | Y` hints, no `match`, no pip deps. The macOS system python is 3.9.
- Never commit OpenAI content: no plugin manifests, model catalog or instruction text, not even in fixtures. cu-local reads them from the user's install at runtime.
- The generated Codex config removes the shell tool unless `--allow-shell` is passed. Keep it that way: the permissive sandbox setting is only safe because no shell exists.
- The shim is stateless. Codex resends the full history with `store:false`, so don't implement `previous_response_id`.
- Codex rejects `null` for some catalog fields. Remove keys instead of nulling them; only `availability_nux` and `upgrade` may be null.
- No internal hostnames, IPs, usernames or home paths anywhere in the repo.

## Workflow gates
- Work one plan phase at a time from `tasks/todo.md`. A phase is done only when its verify skill passes.
- Bump `__version__` in `cu_local.py` in the same commit, with a `vX.Y.Z:` subject prefix. Push GitHub (canonical) and the Gitea mirror.
- Encode corrections here or in `.claude/skills/`, not only in `tasks/lessons.md`.

## Anti-patterns
- Don't verify Mac app state over SSH with `osascript`: it pops an Automation consent dialog on the user's screen.
- Don't trust one bench run. Calculator has been seen passing once and then producing a wrong product.
- Don't copy `tool_mode: code_mode_only` or `supports_search_tool: true` from OpenAI profiles: they need OpenAI's server-side tool_search.
