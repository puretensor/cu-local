# cu-local — master plan

Phases are ordered by risk. A phase is done only when its exit check passes.
Plan review (advisor, 2026-11-09) is folded in; the waived items are listed at the end.

## Phase 1 — Core CLI + shim (riskiest first) → v0.2.0  ✅ 2026-11-09
Results:
- Unit tests 25/25 on Python 3.9.6 (macOS system python) and 3.12.
- Calculator 2/2 native (9.6 s, 7.4 s) and 2/2 through the shim over Chat (7.7 s, 5.8 s).
- Baseline prompt is 8,045 tokens (it was ~143k with a full ~/.codex).
- Exit revised: Codex always adds its MCP-resource and request_user_input helpers, which have no feature flag. That makes 5 tools, not ≤3. Shell, patch, web, goal and agent tools are all removed.

- [ ] Single-file Python 3.9+ stdlib CLI `cu-local` with `run`, `doctor`, `shim`, `bench`. No `X|Y` type hints.
- [ ] Slim CODEX_HOME generated from the installed unified-computer-use plugin, with a neutral cwd. Never commit plugin or catalog content.
- [ ] Tool surface cut to `cua_repl` only. Use feature flags to disable shell, apply_patch, web_search, goals, collaboration and request_user_input. Measure the per-request tool count through a logging proxy. (Today's trace shows 14 tools.)
- [ ] Catalog builder:
  - clone the user's cached GPT profile, preferring the Sol slug and falling back to any entry with `tool_mode`;
  - set tool_mode direct, no tool search, a 10k-token tool-output cap, and `--context`;
  - `--text-only` sets input_modalities to text;
  - on an unknown schema, print a clear error, not a StopIteration.
- [ ] `shim`: stateless Responses→Chat translator, built from the captured trace.
  - Requests: map developer to system; translate message parts (input_text, output_text, input_image); `function_call` / `function_call_output`; output arrays with images hoisted into a following user message (or dropped as text in text-only mode); reasoning items replayed as `reasoning_content`.
  - Tools: flatten `namespace` tools and map names back on return; drop custom and web_search tools.
  - Streaming: emit created, in_progress, output_item.added/done, reasoning_text and output_text deltas, function_call_arguments deltas, and completed with usage (`stream_options.include_usage`, synthesised if the backend omits it).
- [ ] Safety defaults: sandbox and approval stay permissive only because the shell tool is removed; README documents it. `--allow-shell` is opt-in and restores the shell tool with a warning.
- [ ] Offline unit tests on synthetic fixtures covering catalog builder, config builder and shim translation, both request and SSE.
- **Exit:** `python3 -m unittest` green. On the test Mac:
  - the logging proxy shows ≤3 tools per request;
  - `cu-local run` passes Calculator 2/2 via vLLM `/v1/responses`;
  - it passes Calculator 2/2 via the shim over vLLM `/v1/chat/completions`.

## Phase 2 — Doctor + backend matrix → v0.3.0  ✅ 2026-11-09
Results:
- A, DeepSeek native: doctor green, 6/6.
- B, shim + text-only + 32k: doctor green, 5/6. The one failure was a digit the model mistyped.
- C, Ollama qwen3-vl:8b on CPU: 1/3. Calculator passed; the 8B model failed the multi-step tasks.
- D, llama-server Qwen3-8B on CPU: Finder timed out (10 calls in 600 s). The Calculator and TextEdit runs were voided because the Mac's screen locked; doctor and bench now detect a locked screen.
- Bugs found and fixed:
  - orphaned Codex processes after a bench timeout (now killed by process group);
  - shared-config race between concurrent runs (homes are now keyed by a hash of their settings);
  - a hidden bench folder making Save dialogs harder;
  - n_ctx_train being read as the serving context.

- [ ] `doctor` checks:
  - ChatGPT.app, the plugin, a real python3 (not the CLT stub), and macOS version;
  - the endpoint: `/v1/responses`, a function-call round-trip, streaming, image input and time to first token;
  - the baseline prompt size against the context window;
  - a one-turn Codex smoke test (catches catalog schema drift).
  It then recommends native or shim and vision or text-only.
- [ ] `bench`: three reference tasks × N repeats (default 2), machine-verified, pass/fail table plus JSON
- [ ] Matrix, A and B first:
  - A: DS-1017 via vLLM `/v1/responses` (multi-GPU class)
  - B: DS-1017 via shim over chat, `--text-only --context 32768` (Spark / ds4 emulation). First measure whether the baseline prompt leaves room in 32k.
  - C: Ollama with a small vision model (laptop / single-card path)
  - D: llama-server native `/v1/responses` with a small GGUF
- **Exit:**
  - doctor green for A and B;
  - bench ≥5/6 for A and ≥4/6 for B;
  - C and D documented as pass, or as fail with a reason and workaround.
  - If B can't be made representative, drop the Spark recipe claims instead of shipping guesses.

## Phase 3 — Repo polish → v1.0.0  ✅ 2026-11-09 (CI is checked on first push)
- [ ] README:
  - a hero diagram (one SVG that works on light and dark);
  - a 30-second quickstart;
  - a benchmark chart rendered from bench JSON;
  - recipes: multi-GPU vLLM/SGLang; DGX Spark (ds4 / llama.cpp, community-verified); single RTX PRO (llama.cpp with expert offload, community-verified); Ollama;
  - how it works, FAQ, safety, and a "not affiliated with OpenAI" notice.
- [ ] `docs/` (architecture, troubleshooting), issue templates (bug, hardware report), CONTRIBUTING, MIT LICENSE, `install.sh`
- [ ] GitHub Actions: unit tests (ubuntu, python 3.9 and 3.12) and shellcheck
- [ ] Leak scan: no internal IPs, hostnames, `/home` or `/Users` paths, or tokens (gitleaks plus a custom regex)
- **Exit:** CI green. A fresh clone plus `install.sh` plus `cu-local doctor` works on the test Mac. The leak scan is clean.

## Phase 4 — Publish
- [ ] Create public `puretensor/cu-local` (GitHub canonical) plus the Gitea mirror; tag v1.0.0 with a release, topics and description
- [ ] Fetch the GitHub page and compare it against the source (README renders, SVG loads)
- **Exit:** the repo is public, CI green, and the release exists.

## Waived review items
- No brew formula or macOS CI matrix for v1. Ubuntu CI is enough for the stdlib translator tests.
- Light/dark hero SVG: kept as one theme-neutral SVG, not two.
