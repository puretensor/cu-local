# Lessons

- 2026-11-09: Running Codex from `~` loads `~/.codex/config.toml` as a project-local layer, which drags the user's whole config back in. Always run from a neutral cwd (`~/.cu-local/work`).
- 2026-11-09: Codex rejects `null` for `multi_agent_version`, `web_search_tool_type` and `apply_patch_tool_type`. Remove the keys instead.
- 2026-11-09: `pkill -f <pattern>` inside a `bash -c` whose text contains the pattern kills that same shell. Kill by port (`fuser -k PORT/tcp`) or pidfile.
