# cu-local — Brief
**Date:** 2026-11-09 · **Status:** confirmed (G1, operator 2026-11-09)

## Problem
OpenAI's Codex Computer Use on macOS is the best Mac computer-use harness, but out of the box it only runs OpenAI's models. People running their own models (DeepSeek on a GPU box, a DGX Spark, or a single RTX PRO) can point Codex at them. Two things go wrong when they do. Each step drags a 150–220k-token prompt, so a 30-second task takes 2–5 minutes. And many local servers speak only Chat Completions, or an incomplete Responses API, so Codex breaks.

## Success criterion
A Mac user with ChatGPT.app and any OpenAI-compatible endpoint serving DeepSeek runs `cu-local doctor` green, then passes the three reference tasks (Finder, Calculator, TextEdit), each checked by machine. The endpoint can be vLLM or SGLang on multiple GPUs, or llama.cpp, ds4 or Ollama on one Spark or one RTX PRO.

## Non-goals
- Bundling or modifying any OpenAI code. cu-local reads the user's own ChatGPT.app install at runtime.
- Windows or Linux. Codex Computer Use is macOS-only.
- Serving models. cu-local is a client; we ship recipes, not servers.
- Beating Sol on quality. The target is a usable local alternative.

## Constraints
- macOS 15+ with ChatGPT.app (Codex built in) and the Computer Use plugin enabled once.
- Endpoint must be reachable from the Mac over HTTP and serve `/v1/responses` or `/v1/chat/completions` (through cu-local's shim).
- Zero runtime dependencies beyond the macOS system `python3`.
- Public repo, MIT licence, "not affiliated with OpenAI" notice, no internal hostnames or IPs.

## Prior art
- Internal: a bash prototype, 2026-11-09. DS-V4.1-Flash-1017 on 4x RTX PRO 6000: 37 / 13 / 25 s against gpt-6.1-sol 29 / 22 / 38 s on the same Mac.
- External: Ollama `launch codex-app` (local models in the Codex app, no Computer Use), open-responses-server (generic Responses shim), Unsloth Studio proxy, `dedene/DeepSeek-V4.1-Flash-1017-DGX-Spark` (1.6-bit VQ-GGUF), `unsloth/DeepSeek-V4.1-Flash-1017-GGUF` (UD-IQ1_S for 128 GB).

## Kill criteria
- OpenAI changes how the plugin is wired, so `cua_repl` can't be loaded outside the app, with no workaround.
- Through the shim, DeepSeek can't pass the reference tasks at all.

## Decisions (research, 2026-11-09)
- **Protocol.** Codex speaks only the Responses API; it rejects `wire_api="chat"`. We ship a stdlib Responses→Chat shim (`cu-local shim`) for Chat-only backends such as ds4 and older llama.cpp. Revisit if llama-server's `/v1/responses` becomes Codex-complete.
- **Tool surface.** Expose only `cua_repl`: no shell, no apply_patch, no web search. It keeps prompts small, avoids the custom-tool types llama.cpp skips, and makes "used only computer use" true by construction. Revisit if users want shell inside cu-local runs.
- **Model metadata.** Use a catalog entry cloned from the user's own cached GPT profile, with `tool_mode=direct`, no tool search (vLLM rejects OpenAI's server-side `tool_search`), and a 10k-token tool-output cap. A text-only switch drops image input for models without vision.
- **Hardware.** No Spark on hand and every RTX PRO is busy. We emulate the Spark and single-card path: the real DeepSeek-1017 driven through its Chat Completions endpoint via the shim, in text-only mode, with a small context, plus Ollama and llama-server with a small model. Spark and single-card recipes ship marked "community-verified"; hardware reports come in through an issue template.
- **Language.** One Python 3.9+ stdlib CLI, single file, installable with `curl | sh` or `brew` later. No pip dependencies, because the macOS system python has none.
