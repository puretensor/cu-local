# How cu-local works

```
cu-local run "task"
  │
  ├─ reads ~/.codex/plugins/…/unified-computer-use/*/.mcp.json   (your installed plugin)
  ├─ reads ~/.codex/models_cache.json                           (Codex's cached model profiles)
  ├─ writes ~/.cu-local/homes/<hash>/{config.toml,catalog.json} (one per distinct setting)
  ├─ starts the shim on 127.0.0.1:<random>                       (only when the server needs it)
  └─ CODEX_HOME=~/.cu-local/homes/<hash>  ChatGPT.app/…/codex exec -C ~/.cu-local/work "task"
         │
         ├── cua_repl MCP server (from the plugin) ── drives Mac apps via the accessibility tree
         └── Responses API ──► your server, or the shim ──► your server's /v1/chat/completions
```

## Why a separate CODEX_HOME

With your normal `~/.codex`, each request to the model carries your whole Codex setup: global instructions, skills, every MCP server's tool schemas, and the shell and patch tools. Codex's own models get much of this deferred behind server-side tool search. A self-hosted model gets all of it inline, on every step.

The generated home contains one MCP server, `cua_repl`, copied from your plugin's manifest. Feature flags turn off shell, apply_patch, web search, goals, sub-agents and skill descriptions. Codex is run from `~/.cu-local/work`, because running from `~` makes it load `~/.codex/config.toml` as a project-level config.

## The model profile

Codex tunes its prompts per model through catalog entries. cu-local clones the entry for Codex's current GPT model from your cache (it never ships one) and changes the following:

| Field | Set to | Why |
|---|---|---|
| `tool_mode` | `direct` | `code_mode_only` needs OpenAI's runtime |
| `supports_search_tool` | `false` | server-side `tool_search` is OpenAI-only, and vLLM rejects the payload |
| `multi_agent_version`, `web_search_tool_type`, `apply_patch_tool_type` | removed | Codex rejects `null` for these |
| `context_window`, `auto_compact_token_limit` | from `/v1/models` or `--context` | so Codex compacts before your server truncates |
| `truncation_policy.limit` | up to 10k tokens, or context/8 | caps a single accessibility-tree dump |
| `input_modalities` | `["text"]` with `--text-only` | Codex then never sends screenshots |

Keeping Sol's instructions matters. With generic fallback instructions, DeepSeek needed 2–3× more tool calls per task in our runs (TextEdit: 19 calls against 8).

## The shim (Responses → Chat Completions)

The shim is stateless: Codex sends `store: false` and the full history on every request.

| Responses input | Chat Completions message |
|---|---|
| `instructions`, leading `developer`/`system` messages | one leading `system` message |
| later `developer` messages | `user` message wrapped in `<developer>` |
| `user` with `input_text` / `input_image` | `user` with `text` / `image_url` parts |
| `reasoning` items | dropped, or `reasoning_content` with `--replay-reasoning` |
| `function_call` (with `namespace`) | assistant `tool_calls`, name `namespace__name` |
| `function_call_output` with images | `tool` message (text), then a `user` message carrying the images |
| `namespace` tools | flattened to `namespace__name` functions |
| `custom`, `web_search` tools | dropped |

| Chat stream delta | Responses events |
|---|---|
| (start) | `response.created`, `response.in_progress` |
| `reasoning_content` / `reasoning` | `output_item.added` (reasoning), `reasoning_text.delta`, then `.done` |
| `content` | `output_item.added` (message), `content_part.added`, `output_text.delta`, then `.done` |
| `tool_calls[i]` | `output_item.added` (function_call, namespace restored), `function_call_arguments.delta`, then `.done` |
| `usage` (via `stream_options.include_usage`) | `response.completed` with `usage` (zeros if the server sends none) |
| `finish_reason: length` | `response.incomplete` |

## Endpoint probing

`doctor` (and the first `run` against a new server) sends one `/v1/responses` request with a namespaced `ping` tool. If a namespaced call comes back, Codex can use the server directly. If not, it retries once, then falls back to the shim. The decision is cached in `~/.cu-local/config.json`, and `--shim on|off` overrides it.
