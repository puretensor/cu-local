#!/usr/bin/env python3
"""cu-local: run OpenAI Codex Computer Use on your Mac with your own model.

Drives the Computer Use plugin from your installed ChatGPT.app with any
self-hosted model behind an OpenAI-compatible endpoint (vLLM, SGLang,
llama.cpp, Ollama, LM Studio, ...). Endpoints that only speak Chat
Completions are bridged by a built-in Responses-API shim.

Not affiliated with or endorsed by OpenAI. cu-local ships none of OpenAI's
code; it reads your local ChatGPT.app installation at runtime.

Python 3.9+, standard library only.  https://github.com/puretensor/cu-local
"""
import argparse
import contextlib
import glob
import http.server
import itertools
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

__version__ = "1.1.3"

# --------------------------------------------------------------------------- paths

HOME = os.path.expanduser("~")
STATE = os.environ.get("CU_LOCAL_HOME", os.path.join(HOME, ".cu-local"))
APP = os.environ.get("CU_CHATGPT_APP", "/Applications/ChatGPT.app")
SRC_CODEX_HOME = os.environ.get("CU_SOURCE_CODEX_HOME", os.path.join(HOME, ".codex"))
USER_CONFIG = os.path.join(STATE, "config.json")

# Codex's own catalog entries we clone instructions from, best first.
PREFERRED_PROFILES = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6.1-terra", "gpt-5.6-sol", "gpt-6-astra"]

# Feature flags that strip Codex down to the computer-use tool. Unknown keys are
# ignored by Codex, so the list can carry names from several Codex versions.
SLIM_FEATURES = {
    "shell_tool": False,
    "goals": False,
    "collaboration_modes": False,
    "default_mode_request_user_input": False,
    "view_image": False,
    "multi_agent": False,
    "multi_agent_v2": False,
    "apply_patch_freeform": False,
    "skip_host_skill_discovery": True,
    "skill_search": False,
    "omit_skill_descriptions": True,
    "image_generation": False,
}

# model_messages sections that only matter for OpenAI-hosted multi-agent/guardian modes.
DROP_MODEL_MESSAGES = ("multi_agent", "guardian_v2", "auto_review", "token_budget", "collaboration_modes")


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write("cu-local: " + msg.rstrip() + "\n")
    sys.exit(code)


def info(msg: str) -> None:
    sys.stderr.write("cu-local: " + msg + "\n")


def codex_binary() -> str:
    explicit = os.environ.get("CU_CODEX")
    if explicit:
        return explicit
    return os.path.join(APP, "Contents/Resources/codex-cli/bin/codex")


def load_user_config() -> Dict[str, Any]:
    try:
        with open(USER_CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_user_config(cfg: Dict[str, Any]) -> None:
    os.makedirs(STATE, exist_ok=True)
    tmp = "%s.%d.%s.tmp" % (USER_CONFIG, os.getpid(), uuid.uuid4().hex[:6])
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, USER_CONFIG)


# --------------------------------------------------------------------------- plugin + catalog

def find_plugin_manifest(src_home: str = SRC_CODEX_HOME) -> str:
    pats = [
        os.path.join(src_home, "plugins/cache/openai-bundled/unified-computer-use/*/.mcp.json"),
        os.path.join(src_home, "plugins/cache/*/unified-computer-use/*/.mcp.json"),
    ]
    found: List[str] = []
    for p in pats:
        found.extend(glob.glob(p))
    if not found:
        raise FileNotFoundError(
            "Computer Use plugin not found under %s/plugins. Open ChatGPT.app, enable "
            "Computer Use once, then retry." % src_home)
    return max(found, key=os.path.getmtime)


def load_cua_server(manifest_path: str) -> Dict[str, Any]:
    with open(manifest_path) as f:
        data = json.load(f)
    servers = data.get("mcpServers") or {}
    srv = servers.get("cua_repl")
    if not srv or "command" not in srv:
        raise ValueError("unrecognised plugin manifest %s: no mcpServers.cua_repl" % manifest_path)
    return srv


def pick_profile(models: List[Dict[str, Any]], preferred: Iterable[str] = PREFERRED_PROFILES) -> Dict[str, Any]:
    by_slug = {m.get("slug"): m for m in models if isinstance(m, dict)}
    for slug in preferred:
        if slug in by_slug:
            return by_slug[slug]
    for m in models:
        if isinstance(m, dict) and m.get("tool_mode") and (m.get("model_messages") or m.get("base_instructions")):
            return m
    raise ValueError("no usable model profile in Codex's model cache")


def build_catalog_entry(base: Dict[str, Any], model: str, context: int, text_only: bool,
                        allow_shell: bool = False) -> Dict[str, Any]:
    """Clone a Codex model profile for a self-hosted model.

    Keeps the instructions template (it measurably cuts the number of steps) and
    switches everything that depends on OpenAI's servers off.
    """
    m = json.loads(json.dumps(base))  # deep copy
    msgs = m.get("model_messages")
    if isinstance(msgs, dict):
        for k in DROP_MODEL_MESSAGES:
            msgs.pop(k, None)
    elif not m.get("base_instructions"):
        raise ValueError("model profile has neither model_messages nor base_instructions")
    m.update({
        "slug": model,
        "display_name": model,
        "description": "self-hosted model via cu-local",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "availability_nux": None,
        "upgrade": None,
        # OpenAI-only machinery a third-party server cannot honour:
        "tool_mode": "direct",              # code_mode_only needs OpenAI's runtime
        "supports_search_tool": False,      # server-side tool_search
        "use_responses_lite": False,
        "supports_reasoning_effort_updates": False,
        "experimental_supported_tools": [],
        "include_skills_usage_instructions": False,
        "include_plugin_usage_instructions": False,
        "include_apps_usage_instructions": False,
        "context_window": int(context),
        "max_context_window": int(context),
        "effective_context_window_percent": 90,
        "auto_compact_token_limit": int(context * 0.8),
        "truncation_policy": {"mode": "tokens", "limit": min(10000, max(2000, context // 8))},
        "input_modalities": ["text"] if text_only else ["text", "image"],
    })
    # Codex rejects null for these; absent means "not offered".
    drop = ["multi_agent_version", "multi_agent_reasoning_effort", "web_search_tool_type"]
    if not allow_shell:
        drop.append("apply_patch_tool_type")
    for k in drop:
        m.pop(k, None)
    return m


def load_model_cache(src_home: str = SRC_CODEX_HOME) -> List[Dict[str, Any]]:
    path = os.path.join(src_home, "models_cache.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except OSError:
        raise FileNotFoundError(
            "%s not found. Sign in to ChatGPT.app and open Codex once so it caches its model list." % path)
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("unrecognised model cache format in %s" % path)
    return models


# --------------------------------------------------------------------------- codex home

def toml_str(s: str) -> str:
    """TOML basic string. JSON escaping is valid TOML except for DEL and lone surrogates."""
    s = s.encode("utf-8", "replace").decode("utf-8")  # lone surrogates -> "?"
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_val(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    raise TypeError("cannot encode %r as TOML" % (v,))


def render_codex_config(cua: Dict[str, Any], *, model: str, base_url: str, effort: str,
                        catalog_path: str, api_key_env: Optional[str], allow_shell: bool,
                        idle_timeout_ms: int) -> str:
    L = [
        "# Generated by cu-local %s on every run. Do not edit." % __version__,
        'model_provider = "cu_local"',
        "model = %s" % toml_str(model),
        "model_reasoning_effort = %s" % toml_str(effort),
        "model_catalog_json = %s" % toml_str(catalog_path),
        'model_verbosity = "low"',
        'approval_policy = "never"',
        # The shell tool is removed (unless --allow-shell), so the only capability
        # the model has is the Computer Use plugin, which asks per app.
        'sandbox_mode = "danger-full-access"',
        'web_search = "disabled"',
        "",
        "[features]",
    ]
    for k, v in SLIM_FEATURES.items():
        if allow_shell and k in ("shell_tool", "apply_patch_freeform"):
            continue
        L.append("%s = %s" % (k, toml_val(v)))
    L += [
        "",
        "[analytics]",
        "enabled = false",
        "",
        "[model_providers.cu_local]",
        'name = "cu-local"',
        "base_url = %s" % toml_str(base_url),
        'wire_api = "responses"',
        "stream_idle_timeout_ms = %d" % idle_timeout_ms,
        "request_max_retries = 2",
        "stream_max_retries = 2",
    ]
    if api_key_env:
        L.append("env_key = %s" % toml_str(api_key_env))
    L += [
        "",
        "[mcp_servers.cua_repl]",
        "command = %s" % toml_str(cua["command"]),
        "args = %s" % toml_val([str(a) for a in cua.get("args", [])]),
        "startup_timeout_sec = %d" % int(cua.get("startup_timeout_sec", 120)),
    ]
    if cua.get("enabled_tools"):
        L.append("enabled_tools = %s" % toml_val(list(cua["enabled_tools"])))
    if cua.get("env_vars"):
        L.append("env_vars = %s" % toml_val(list(cua["env_vars"])))
    env = cua.get("env") or {}
    if env:
        L += ["", "[mcp_servers.cua_repl.env]"]
        L += ["%s = %s" % (toml_str(str(k)), toml_str(str(v))) for k, v in env.items()]
    return "\n".join(L) + "\n"


def screen_locked() -> Optional[bool]:
    """True if the macOS login session's screen is locked (Computer Use then sees no windows)."""
    if sys.platform != "darwin":
        return None
    import plistlib
    try:
        out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, timeout=10).stdout
        data = plistlib.loads(out)
    except Exception:  # noqa: BLE001
        return None

    def find(o: Any) -> Optional[bool]:
        if isinstance(o, dict):
            if "CGSSessionScreenIsLocked" in o:
                return bool(o["CGSSessionScreenIsLocked"])
            for v in o.values():
                r = find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r is not None:
                    return r
        return None
    found = find(data)
    return False if found is None else found


# --------------------------------------------------------------------------- HTTP helpers

def http_json(url: str, body: Optional[Dict[str, Any]] = None, timeout: float = 30,
              api_key: Optional[str] = None) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    try:
        return code, json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return code, raw.decode("utf-8", "replace")


def list_models(base_url: str, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    code, data = http_json(base_url.rstrip("/") + "/models", timeout=15, api_key=api_key)
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError("GET %s/models returned %s" % (base_url, code))
    return [m for m in data.get("data", []) if isinstance(m, dict)]


def detect_context(models: List[Dict[str, Any]], model: str) -> Optional[int]:
    """Serving context window as advertised by /v1/models (vLLM, SGLang, llama.cpp, LM Studio)."""
    for m in models:
        if m.get("id") != model:
            continue
        meta = m.get("meta") or {}
        # Serving limits first; llama.cpp's n_ctx_train is only the training length.
        for v in (m.get("max_model_len"), m.get("context_length"), m.get("context_window"),
                  m.get("n_ctx"), meta.get("n_ctx"), meta.get("n_ctx_train")):
            if isinstance(v, int) and v > 0:
                return v
    return None


# --------------------------------------------------------------------------- Responses -> Chat shim

SEP = "__"  # namespace separator for flattened tool names


def _flat(ns: Optional[str], name: str) -> str:
    return "%s%s%s" % (ns, SEP, name) if ns else name


def _text_of(parts: Any) -> str:
    if isinstance(parts, str):
        return parts
    out = []
    for p in parts or []:
        if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text", "reasoning_text", "summary_text", "refusal"):
            out.append(p.get("text") or p.get("refusal") or "")
    return "".join(out)


def _image_url(p: Dict[str, Any]) -> Optional[str]:
    u = p.get("image_url")
    if isinstance(u, dict):
        u = u.get("url")
    return u if isinstance(u, str) and u else None


def _user_content(parts: Any, text_only: bool) -> Any:
    if isinstance(parts, str):
        return parts
    out: List[Dict[str, Any]] = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if t in ("input_text", "output_text", "text"):
            out.append({"type": "text", "text": p.get("text") or ""})
        elif t == "input_image":
            url = _image_url(p)
            if url and not text_only:
                out.append({"type": "image_url", "image_url": {"url": url}})
            else:
                out.append({"type": "text", "text": "[image omitted: text-only model]"})
        elif t == "input_file":
            out.append({"type": "text", "text": "[file omitted]"})
    if all(x["type"] == "text" for x in out):
        return "".join(x["text"] for x in out)
    return out


def translate_tools(tools: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Tuple[Optional[str], str]]]:
    """Responses tools -> Chat tools. Returns (chat_tools, flat_name -> (namespace, name))."""
    out: List[Dict[str, Any]] = []
    names: Dict[str, Tuple[Optional[str], str]] = {}

    def add(fn: Dict[str, Any], ns: Optional[str]) -> None:
        name = fn.get("name")
        if not name:
            return
        flat = _flat(ns, name)
        names[flat] = (ns, name)
        f = {"name": flat, "parameters": fn.get("parameters") or {"type": "object", "properties": {}}}
        desc = fn.get("description")
        if desc:
            f["description"] = desc
        out.append({"type": "function", "function": f})

    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function":
            add(t, None)
        elif t.get("type") == "namespace":
            for inner in t.get("tools") or []:
                if isinstance(inner, dict) and inner.get("type", "function") == "function":
                    add(inner, t.get("name"))
        # custom / web_search / file_search / etc. have no Chat equivalent: dropped.
    return out, names


def repair_tool_pairs(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Chat servers require every tool message to answer a call in the assistant turn just
    before it, and every call to be answered. Codex history can break this after compaction."""
    out: List[Dict[str, Any]] = []
    pending: List[str] = []
    answered: set = set()

    def close() -> None:
        for cid in pending:
            if cid not in answered:
                out.append({"role": "tool", "tool_call_id": cid, "content": "(no output recorded)"})
        del pending[:]
        answered.clear()

    for m in msgs:
        role = m.get("role")
        if role == "tool":
            cid = m.get("tool_call_id")
            if cid in pending and cid not in answered:
                answered.add(cid)
                out.append(m)
            else:  # orphan output: keep its text, as a user message
                close()
                out.append({"role": "user", "content": "(output of an earlier tool call)\n%s" % (m.get("content") or "")})
            continue
        close()
        out.append(m)
        if role == "assistant" and m.get("tool_calls"):
            pending.extend(tc["id"] for tc in m["tool_calls"])
    close()
    return out


def translate_request(req: Dict[str, Any], *, text_only: bool = False, replay_reasoning: bool = False,
                      model_override: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Tuple[Optional[str], str]]]:
    """Responses API request -> Chat Completions request (stateless; Codex resends history)."""
    msgs: List[Dict[str, Any]] = []
    system_parts: List[str] = []
    if req.get("instructions"):
        system_parts.append(str(req["instructions"]))
    seen_non_system = False
    pending_assistant: Optional[Dict[str, Any]] = None
    pending_reasoning: List[str] = []
    pending_images: List[Dict[str, Any]] = []

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            msgs.append(pending_assistant)
            pending_assistant = None

    def flush_images() -> None:
        if pending_images:
            msgs.append({"role": "user", "content": [{"type": "text", "text": "(images returned by the previous tool call)"}] + pending_images[:]})
            del pending_images[:]

    def assistant() -> Dict[str, Any]:
        nonlocal pending_assistant
        if pending_assistant is None:
            flush_images()
            pending_assistant = {"role": "assistant", "content": ""}
        if pending_reasoning:
            if replay_reasoning:
                prev = pending_assistant.get("reasoning_content")
                pending_assistant["reasoning_content"] = "\n".join(([prev] if prev else []) + pending_reasoning)
            del pending_reasoning[:]
        return pending_assistant

    items = req.get("input")
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": items}]
    for it in items or []:
        if not isinstance(it, dict):
            continue
        typ = it.get("type") or ("message" if "role" in it else None)
        if typ == "message":
            role = it.get("role")
            content = it.get("content")
            if role in ("developer", "system"):
                text = _text_of(content)
                if not seen_non_system:
                    system_parts.append(text)
                else:
                    flush_assistant(); flush_images()
                    msgs.append({"role": "user", "content": "<developer>\n%s\n</developer>" % text})
            elif role == "assistant":
                a = assistant()
                a["content"] = (a.get("content") or "") + _text_of(content)
                seen_non_system = True
            else:
                flush_assistant(); flush_images()
                msgs.append({"role": "user", "content": _user_content(content, text_only)})
                seen_non_system = True
        elif typ == "reasoning":
            # Reasoning precedes the assistant turn it belongs to; it never splits a turn.
            t = _text_of(it.get("content")) or _text_of(it.get("summary"))
            if t:
                pending_reasoning.append(t)
        elif typ in ("function_call", "custom_tool_call"):
            a = assistant()
            args = it.get("arguments") if typ == "function_call" else json.dumps({"input": it.get("input", "")})
            a.setdefault("tool_calls", []).append({
                "id": it.get("call_id") or it.get("id") or "call_" + uuid.uuid4().hex[:12],
                "type": "function",
                "function": {"name": _flat(it.get("namespace"), it.get("name") or "tool"), "arguments": args or "{}"},
            })
            seen_non_system = True
        elif typ in ("function_call_output", "custom_tool_call_output"):
            flush_assistant()
            out = it.get("output")
            if isinstance(out, list):
                text = []
                for p in out:
                    if not isinstance(p, dict):
                        continue
                    if p.get("type") in ("input_text", "output_text", "text"):
                        text.append(p.get("text") or "")
                    elif p.get("type") == "input_image":
                        url = _image_url(p)
                        if url and not text_only:
                            pending_images.append({"type": "image_url", "image_url": {"url": url}})
                            text.append("[image attached below]")
                        else:
                            text.append("[image omitted: text-only model]")
                content = "\n".join(text)
            elif isinstance(out, dict):
                content = out.get("content") if isinstance(out.get("content"), str) else json.dumps(out)
            else:
                content = "" if out is None else str(out)
            msgs.append({"role": "tool", "tool_call_id": it.get("call_id") or "", "content": content})
            seen_non_system = True
        # other item types (compaction markers, web_search_call, ...) carry nothing a chat model can use
    flush_assistant(); flush_images()
    msgs = repair_tool_pairs(msgs)
    if system_parts:
        msgs.insert(0, {"role": "system", "content": "\n\n".join(p for p in system_parts if p)})

    tools, names = translate_tools(req.get("tools") or [])
    out: Dict[str, Any] = {
        "model": model_override or req.get("model"),
        "messages": msgs,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        out["tools"] = tools
        tc = req.get("tool_choice")
        if isinstance(tc, str) and tc in ("auto", "none", "required"):
            out["tool_choice"] = tc
        elif isinstance(tc, dict) and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": _flat(tc.get("namespace"), tc["name"])}}
        if req.get("parallel_tool_calls") is not None:
            out["parallel_tool_calls"] = bool(req["parallel_tool_calls"])
    if req.get("max_output_tokens"):
        out["max_tokens"] = req["max_output_tokens"]
    for k in ("temperature", "top_p"):
        if req.get(k) is not None:
            out[k] = req[k]
    effort = (req.get("reasoning") or {}).get("effort")
    if effort:
        out["reasoning_effort"] = effort
    return out, names


class UpstreamError(RuntimeError):
    pass


class StreamTranslator:
    """Chat Completions stream chunks -> Responses API stream events."""

    def __init__(self, model: str, names: Dict[str, Tuple[Optional[str], str]]):
        self.model = model
        self.names = names
        self.id = "resp_" + uuid.uuid4().hex
        self.created = int(time.time())
        self.seq = itertools.count()
        self.output: List[Dict[str, Any]] = []
        self.open_kind: Optional[str] = None       # "reasoning" | "message"
        self.reasoning: Optional[Dict[str, Any]] = None
        self.message: Optional[Dict[str, Any]] = None
        self.calls: Dict[Any, Dict[str, Any]] = {}  # chat tool index (or index#n) -> item
        self.index_key: Dict[Any, Any] = {}
        self.usage: Optional[Dict[str, Any]] = None
        self.finish: Optional[str] = None

    @staticmethod
    def _public(it: Dict[str, Any]) -> Dict[str, Any]:
        return {k: v for k, v in it.items() if not k.startswith("_")}

    def _ev(self, typ: str, **kw: Any) -> Dict[str, Any]:
        d = {"type": typ, "sequence_number": next(self.seq)}
        d.update(kw)
        return d

    def _response(self, status: str) -> Dict[str, Any]:
        r = {"id": self.id, "object": "response", "created_at": self.created, "model": self.model,
             "status": status, "output": [self._public(o) for o in self.output] if status != "in_progress" else [],
             "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
        if status != "in_progress":
            r["usage"] = self._usage()
        return r

    def _usage(self) -> Dict[str, Any]:
        u = self.usage or {}
        pt = int(u.get("prompt_tokens") or 0)
        ct = int(u.get("completion_tokens") or 0)
        cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
        rt = int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        return {"input_tokens": pt, "input_tokens_details": {"cached_tokens": cached},
                "output_tokens": ct, "output_tokens_details": {"reasoning_tokens": rt},
                "total_tokens": int(u.get("total_tokens") or pt + ct)}

    def start(self) -> List[Dict[str, Any]]:
        return [self._ev("response.created", response=self._response("in_progress")),
                self._ev("response.in_progress", response=self._response("in_progress"))]

    # -- item lifecycle
    def _close_open(self) -> List[Dict[str, Any]]:
        ev: List[Dict[str, Any]] = []
        if self.open_kind == "reasoning" and self.reasoning is not None:
            it = self.reasoning
            idx = self.output.index(it)
            text = it["content"][0]["text"]
            ev.append(self._ev("response.reasoning_text.done", item_id=it["id"], output_index=idx, content_index=0, text=text))
            ev.append(self._ev("response.output_item.done", output_index=idx, item=it))
            self.reasoning = None
        elif self.open_kind == "message" and self.message is not None:
            it = self.message
            idx = self.output.index(it)
            part = it["content"][0]
            it["status"] = "completed"
            ev.append(self._ev("response.output_text.done", item_id=it["id"], output_index=idx, content_index=0, text=part["text"]))
            ev.append(self._ev("response.content_part.done", item_id=it["id"], output_index=idx, content_index=0, part=part))
            ev.append(self._ev("response.output_item.done", output_index=idx, item=it))
            self.message = None
        self.open_kind = None
        return ev

    def _reasoning_delta(self, text: str) -> List[Dict[str, Any]]:
        ev: List[Dict[str, Any]] = []
        if self.open_kind != "reasoning":
            ev += self._close_open()
            self.reasoning = {"type": "reasoning", "id": "rs_" + uuid.uuid4().hex[:16], "summary": [],
                              "content": [{"type": "reasoning_text", "text": ""}]}
            self.output.append(self.reasoning)
            self.open_kind = "reasoning"
            ev.append(self._ev("response.output_item.added", output_index=len(self.output) - 1,
                               item={"type": "reasoning", "id": self.reasoning["id"], "summary": [], "content": []}))
        it = self.reasoning
        it["content"][0]["text"] += text
        ev.append(self._ev("response.reasoning_text.delta", item_id=it["id"], output_index=self.output.index(it),
                           content_index=0, delta=text))
        return ev

    def _text_delta(self, text: str) -> List[Dict[str, Any]]:
        ev: List[Dict[str, Any]] = []
        if self.open_kind != "message":
            ev += self._close_open()
            self.message = {"type": "message", "id": "msg_" + uuid.uuid4().hex[:16], "role": "assistant",
                            "status": "in_progress", "content": [{"type": "output_text", "text": "", "annotations": []}]}
            self.output.append(self.message)
            self.open_kind = "message"
            idx = len(self.output) - 1
            ev.append(self._ev("response.output_item.added", output_index=idx,
                               item={"type": "message", "id": self.message["id"], "role": "assistant",
                                     "status": "in_progress", "content": []}))
            ev.append(self._ev("response.content_part.added", item_id=self.message["id"], output_index=idx,
                               content_index=0, part={"type": "output_text", "text": "", "annotations": []}))
        it = self.message
        it["content"][0]["text"] += text
        ev.append(self._ev("response.output_text.delta", item_id=it["id"], output_index=self.output.index(it),
                           content_index=0, delta=text))
        return ev

    def _tool_delta(self, tc: Dict[str, Any], pos: int = 0) -> List[Dict[str, Any]]:
        ev: List[Dict[str, Any]] = []
        index = tc.get("index", pos)
        fn = tc.get("function") or {}
        idx_key = self.index_key.get(index, index)
        it = self.calls.get(idx_key)
        if it is not None and tc.get("id") and it.get("_upstream_id") and tc["id"] != it["_upstream_id"]:
            # Same index, new id: the server reuses indexes for parallel calls.
            idx_key = "%s#%d" % (index, len(self.calls))
            self.index_key[index] = idx_key
            it = None
        if it is None:
            ev += self._close_open()
            flat = fn.get("name") or ""
            ns, name = self.names.get(flat, (None, flat))
            it = {"type": "function_call", "id": "fc_" + uuid.uuid4().hex[:16],
                  "call_id": tc.get("id") or "call_" + uuid.uuid4().hex[:16],
                  "name": name, "arguments": "", "status": "in_progress"}
            if ns:
                it["namespace"] = ns
            self.calls[idx_key] = it
            it["_upstream_id"] = tc.get("id")
            self.output.append(it)
            ev.append(self._ev("response.output_item.added", output_index=len(self.output) - 1, item=self._public(it)))
        elif fn.get("name") and not it["name"]:
            ns, name = self.names.get(fn["name"], (None, fn["name"]))
            it["name"] = name
            if ns:
                it["namespace"] = ns
        args = fn.get("arguments")
        if args:
            it["arguments"] += args
            ev.append(self._ev("response.function_call_arguments.delta", item_id=it["id"],
                               output_index=self.output.index(it), delta=args))
        return ev

    def feed(self, chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
        ev: List[Dict[str, Any]] = []
        if chunk.get("error"):
            err = chunk["error"]
            raise UpstreamError(err.get("message") if isinstance(err, dict) else str(err))
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for ch in chunk.get("choices") or []:
            d = ch.get("delta") or ch.get("message") or {}
            r = d.get("reasoning_content") or d.get("reasoning")
            if isinstance(r, str) and r:
                ev += self._reasoning_delta(r)
            c = d.get("content")
            if isinstance(c, str) and c:
                ev += self._text_delta(c)
            ref = d.get("refusal")
            if isinstance(ref, str) and ref:
                ev += self._text_delta(ref)
            for pos, tc in enumerate(d.get("tool_calls") or []):
                ev += self._tool_delta(tc, pos)
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]
        return ev

    def finish_events(self) -> List[Dict[str, Any]]:
        ev = self._close_open()
        truncated = self.finish in (None, "length")
        for it in self.calls.values():
            it["status"] = "incomplete" if truncated else "completed"
            if not it["arguments"] and not truncated:
                it["arguments"] = "{}"
            idx = self.output.index(it)
            ev.append(self._ev("response.function_call_arguments.done", item_id=it["id"], output_index=idx,
                               arguments=it["arguments"]))
            ev.append(self._ev("response.output_item.done", output_index=idx, item=self._public(it)))
        if self.finish is None and not self.output:
            resp = self._response("failed")
            resp["error"] = {"code": "server_error", "message": "upstream stream ended without any output"}
            ev.append(self._ev("response.failed", response=resp))
        elif self.finish == "length" or self.finish is None:
            resp = self._response("incomplete")
            resp["incomplete_details"] = {"reason": "max_output_tokens" if self.finish == "length" else "upstream_ended"}
            ev.append(self._ev("response.incomplete", response=resp))
        else:
            ev.append(self._ev("response.completed", response=self._response("completed")))
        return ev


def iter_sse(resp: Any) -> Iterator[Dict[str, Any]]:
    """Yield JSON payloads from an SSE (or single JSON) chat completion response."""
    def read() -> bytes:
        return resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)

    buf = read()
    if buf.lstrip().startswith(b"{"):
        # The server ignored stream=true and sent one JSON body.
        rest = resp.read()
        with contextlib.suppress(ValueError):
            yield json.loads(buf + rest)
        return
    while True:
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                return
            try:
                yield json.loads(payload)
            except ValueError:
                continue
        chunk = read()
        if not chunk:
            break
        buf += chunk
    tail = buf.strip()
    if tail.startswith(b"data:"):
        tail = tail[5:].strip()
    if tail.startswith(b"{"):
        with contextlib.suppress(ValueError):
            yield json.loads(tail)


class ShimServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: Tuple[str, int], upstream: str, *, text_only: bool, replay_reasoning: bool,
                 api_key: Optional[str], model_override: Optional[str], log: Optional[str],
                 timeout: float = 900):
        super().__init__(addr, ShimHandler)
        self.timeout = timeout
        self.upstream = upstream.rstrip("/")
        self.text_only = text_only
        self.replay_reasoning = replay_reasoning
        self.api_key = api_key
        self.model_override = model_override
        self.log = log


class ShimHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ShimServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.server.log:
            with open(self.server.log, "a") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        auth = self.headers.get("Authorization")
        if self.server.api_key:
            h["Authorization"] = "Bearer " + self.server.api_key
        elif auth:
            h["Authorization"] = auth
        return h

    def _redact(self, text: str) -> str:
        """Remove credentials from upstream text before it is logged or returned."""
        secrets = [self.server.api_key or "", (self.headers.get("Authorization") or "").replace("Bearer ", "")]
        for sec in secrets:
            if len(sec) >= 8:
                text = text.replace(sec, "[redacted]")
        return re.sub(r"(?i)(bearer\s+|api[_-]?key[\"'=: ]+)[A-Za-z0-9._\-]{8,}", r"\1[redacted]", text)

    def _send_json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _path(self) -> str:
        import urllib.parse
        return urllib.parse.urlsplit(self.path).path.rstrip("/")

    def do_GET(self) -> None:
        if self._path().endswith("/models"):
            req = urllib.request.Request(self.server.upstream + "/models", headers=self._headers())
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return self._send_json(r.status, json.loads(r.read()))
            except Exception as e:  # noqa: BLE001
                return self._send_json(502, {"error": {"message": "upstream /models failed: %s" % e}})
        if self._path() in ("", "/health", "/v1"):
            return self._send_json(200, {"ok": True, "shim": __version__, "upstream": self.server.upstream})
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if not self._path().endswith("/responses"):
            return self._send_json(404, {"error": {"message": "cu-local shim only serves /v1/responses"}})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send_json(400, {"error": {"message": "invalid JSON"}})
        chat, names = translate_request(req, text_only=self.server.text_only,
                                        replay_reasoning=self.server.replay_reasoning,
                                        model_override=self.server.model_override)
        up = urllib.request.Request(self.server.upstream + "/chat/completions",
                                    data=json.dumps(chat).encode(), headers=self._headers(), method="POST")
        try:
            resp = urllib.request.urlopen(up, timeout=self.server.timeout)
        except urllib.error.HTTPError as e:
            body = self._redact(e.read().decode("utf-8", "replace"))
            self.log_message("upstream HTTP %s", e.code)
            return self._send_json(e.code, {"error": {"message": "upstream: " + body[:2000], "type": "upstream_error"}})
        except Exception as e:  # noqa: BLE001
            return self._send_json(502, {"error": {"message": "upstream unreachable: %s" % e}})
        tr = StreamTranslator(chat.get("model") or "", names)
        if req.get("stream", True):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def emit(evs: List[Dict[str, Any]]) -> None:
                for e in evs:
                    data = ("event: %s\ndata: %s\n\n" % (e["type"], json.dumps(e))).encode()
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
            try:
                emit(tr.start())
                with resp:
                    for chunk in iter_sse(resp):
                        evs = tr.feed(chunk)
                        if evs:
                            emit(evs)
                emit(tr.finish_events())
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    emit([tr._ev("response.failed", response=dict(tr._response("failed"),
                                                                   error={"code": "server_error", "message": str(e)}))])
            with contextlib.suppress(Exception):
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        else:
            try:
                with resp:
                    for chunk in iter_sse(resp):
                        tr.feed(chunk)
            except UpstreamError as e:
                return self._send_json(502, {"error": {"message": "upstream: %s" % e, "type": "upstream_error"}})
            evs = tr.finish_events()
            self._send_json(200, evs[-1]["response"])


def start_shim(upstream: str, *, host: str = "127.0.0.1", port: int = 0, text_only: bool = False,
               replay_reasoning: bool = False, api_key: Optional[str] = None,
               model_override: Optional[str] = None, log: Optional[str] = None,
               timeout: float = 900) -> ShimServer:
    srv = ShimServer((host, port), upstream, text_only=text_only, replay_reasoning=replay_reasoning,
                     api_key=api_key, model_override=model_override, log=log, timeout=timeout)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --------------------------------------------------------------------------- endpoint probing

def solid_png(size: int, rgb: Tuple[int, int, int]) -> str:
    """Base64 PNG of one solid colour (for the vision probe)."""
    import base64
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * size
    raw = zlib.compress(row * size)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode()


def probe_responses(base_url: str, model: str, api_key: Optional[str] = None,
                    timeout: float = 120, attempts: int = 2) -> Dict[str, Any]:
    """Can this endpoint serve Codex natively? Needs /v1/responses AND namespaced tool calls.

    A model can decline to call the tool once, so a negative result is retried.
    """
    res: Dict[str, Any] = {}
    for _ in range(max(1, attempts)):
        res = _probe_responses_once(base_url, model, api_key, timeout)
        if res.get("ok") or str(res.get("reason", "")).startswith(("HTTP", "unreachable")):
            break
    return res


def _probe_responses_once(base_url: str, model: str, api_key: Optional[str], timeout: float) -> Dict[str, Any]:
    body = {
        "model": model, "stream": False, "max_output_tokens": 256, "store": False,
        "input": [{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "Call the ping tool with value 42. Do not answer in text."}]}],
        "tools": [{"type": "namespace", "name": "mcp__probe", "description": "probe tools", "tools": [
            {"type": "function", "name": "ping", "description": "Ping with a number.", "strict": False,
             "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}]}],
        "tool_choice": "auto",
    }
    t0 = time.time()
    try:
        code, data = http_json(base_url.rstrip("/") + "/responses", body, timeout=timeout, api_key=api_key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "unreachable: %s" % e}
    dt = time.time() - t0
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "reason": "HTTP %s" % code, "detail": str(data)[:300], "seconds": dt}
    calls = [o for o in data.get("output") or [] if isinstance(o, dict) and o.get("type") == "function_call"]
    kept = [c for c in calls if c.get("name") == "ping" and c.get("namespace") == "mcp__probe"]
    flat = [c for c in calls if c.get("name") == "mcp__probe__ping"]
    if kept or flat:
        return {"ok": True, "namespace_kept": bool(kept), "seconds": dt}
    if calls:
        return {"ok": False, "reason": "server dropped the tool namespace", "seconds": dt}
    return {"ok": False, "reason": "no namespaced tool call in native /responses output", "seconds": dt}


def probe_chat(base_url: str, model: str, api_key: Optional[str] = None, image: bool = False,
               timeout: float = 180) -> Dict[str, Any]:
    content: Any = "Call the ping tool with value 42. Do not answer in text."
    if image:
        content = [{"type": "text", "text": "What colour is this image? One word."},
                   {"type": "image_url", "image_url": {"url": "data:image/png;base64," + solid_png(64, (220, 20, 20))}}]
    body: Dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}],
                            "max_tokens": 400, "stream": False}
    if not image:
        body["tools"] = [{"type": "function", "function": {
            "name": "mcp__probe__ping", "description": "Ping with a number.",
            "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}}}]
    t0 = time.time()
    try:
        code, data = http_json(base_url.rstrip("/") + "/chat/completions", body, timeout=timeout, api_key=api_key)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": "unreachable: %s" % e}
    dt = time.time() - t0
    if code != 200 or not isinstance(data, dict):
        return {"ok": False, "reason": "HTTP %s" % code, "detail": str(data)[:300], "seconds": dt}
    msg = ((data.get("choices") or [{}])[0].get("message")) or {}
    if image:
        ans = (msg.get("content") or "").lower()
        return {"ok": "red" in ans, "answer": ans[:60], "seconds": dt}
    calls = msg.get("tool_calls") or []
    ok = any((c.get("function") or {}).get("name") == "mcp__probe__ping" for c in calls)
    return {"ok": ok, "reason": None if ok else "model did not emit a tool call", "seconds": dt,
            "usage": data.get("usage")}


# --------------------------------------------------------------------------- run

class Settings:
    def __init__(self, ns: argparse.Namespace):
        cfg = load_user_config()
        self.endpoint = (getattr(ns, "endpoint", None) or os.environ.get("CU_ENDPOINT") or cfg.get("endpoint") or "").rstrip("/")
        self.model = getattr(ns, "model", None) or os.environ.get("CU_MODEL") or cfg.get("model")
        self.api_key_env = getattr(ns, "api_key_env", None) or cfg.get("api_key_env")
        self.shim = getattr(ns, "shim", None) or cfg.get("shim") or "auto"
        self.text_only = bool(getattr(ns, "text_only", False) or cfg.get("text_only"))
        self.context = getattr(ns, "context", None) or cfg.get("context")
        self.effort = getattr(ns, "effort", None) or cfg.get("effort") or "low"
        self.allow_shell = bool(getattr(ns, "allow_shell", False))
        self.replay_reasoning = bool(getattr(ns, "replay_reasoning", False) or cfg.get("replay_reasoning"))
        self.idle_timeout = int(getattr(ns, "idle_timeout", None) or cfg.get("idle_timeout") or 600)
        if not self.endpoint:
            die("no endpoint. Pass --endpoint http://host:port/v1 or run: cu-local doctor --endpoint URL --save")
        if not self.endpoint.endswith("/v1"):
            info("note: endpoint does not end in /v1 (%s); using it as given" % self.endpoint)

    @property
    def api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env) if self.api_key_env else None


def resolve_model(s: Settings) -> Tuple[str, Optional[int]]:
    models: List[Dict[str, Any]] = []
    with contextlib.suppress(Exception):
        models = list_models(s.endpoint, s.api_key)
    model = s.model
    if not model:
        if not models:
            die("could not list models at %s/models; pass --model" % s.endpoint)
        model = models[0]["id"]
    return model, detect_context(models, model)


def _write_atomic(path: str, text: str) -> None:
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def prepare_home(s: Settings, model: str, base_url: str, context: int) -> Tuple[str, str]:
    """Write a Codex home for exactly these settings.

    Homes are keyed by a hash of their contents, so concurrent runs against
    different endpoints never overwrite each other's config.
    """
    import hashlib
    work = os.path.join(STATE, "work")
    homes = os.path.join(STATE, "homes")
    os.makedirs(work, exist_ok=True)
    os.makedirs(homes, exist_ok=True)
    try:
        cua = load_cua_server(find_plugin_manifest())
        base = pick_profile(load_model_cache())
        entry = build_catalog_entry(base, model, context, s.text_only, s.allow_shell)
    except (OSError, ValueError) as e:
        die(str(e))
    catalog_text = json.dumps({"models": [entry]})
    key_src = catalog_text + "|" + render_codex_config(
        cua, model=model, base_url=base_url, effort=s.effort, catalog_path="", api_key_env=s.api_key_env,
        allow_shell=s.allow_shell, idle_timeout_ms=s.idle_timeout * 1000)
    home = os.path.join(homes, hashlib.sha1(key_src.encode()).hexdigest()[:12])
    os.makedirs(home, exist_ok=True)
    catalog = os.path.join(home, "catalog.json")
    _write_atomic(catalog, catalog_text)
    _write_atomic(os.path.join(home, "config.toml"), render_codex_config(
        cua, model=model, base_url=base_url, effort=s.effort, catalog_path=catalog,
        api_key_env=s.api_key_env if s.shim_mode == "native" else None,
        allow_shell=s.allow_shell, idle_timeout_ms=s.idle_timeout * 1000))
    os.utime(home)
    _prune_homes(homes, keep=home)
    return home, work


def _prune_homes(homes: str, keep: str, max_age_days: int = 7) -> None:
    cutoff = time.time() - max_age_days * 86400
    for d in glob.glob(os.path.join(homes, "*")):
        if d != keep and os.path.isdir(d) and os.path.getmtime(d) < cutoff:
            shutil.rmtree(d, ignore_errors=True)


def decide_shim(s: Settings, model: str) -> str:
    if s.shim in ("on", "off"):
        return "shim" if s.shim == "on" else "native"
    cache = load_user_config().get("probe", {})
    key = "%s|%s" % (s.endpoint, model)
    if key in cache:
        return cache[key]
    info("probing %s/responses (first run only)..." % s.endpoint)
    mode = "native" if probe_responses(s.endpoint, model, s.api_key).get("ok") else "shim"
    cfg = load_user_config()
    cfg.setdefault("probe", {})[key] = mode
    save_user_config(cfg)
    info("using %s mode" % mode)
    return mode


def cmd_run(ns: argparse.Namespace, extra: List[str]) -> int:
    s = Settings(ns)
    codex = codex_binary()
    if not os.path.exists(codex):
        die("Codex not found at %s. Install the ChatGPT desktop app (with Codex) or set CU_CODEX." % codex)
    model, ctx = resolve_model(s)
    context = int(s.context or ctx or 131072)
    s.shim_mode = decide_shim(s, model)
    shim = None
    base_url = s.endpoint
    if s.shim_mode == "shim":
        shim = start_shim(s.endpoint, text_only=s.text_only, replay_reasoning=s.replay_reasoning,
                          api_key=s.api_key, log=os.path.join(STATE, "shim.log"), timeout=s.idle_timeout)
        base_url = "http://127.0.0.1:%d/v1" % shim.server_address[1]
    home, work = prepare_home(s, model, base_url, context)
    if screen_locked():
        info("warning: the screen is locked, so Computer Use will not see any window until you unlock it")
    env = dict(os.environ, CODEX_HOME=home)
    argv = [codex, "exec", "--skip-git-repo-check", "-C", ns.cwd or work] + extra + [ns.task]
    if ns.verbose:
        info("model=%s context=%d mode=%s text_only=%s" % (model, context, s.shim_mode, s.text_only))
    proc = subprocess.Popen(argv, env=env)

    def forward(signum: int, _frame: Any) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signum)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, forward)
    try:
        rc = proc.wait()
    except KeyboardInterrupt:
        # The terminal already sent SIGINT to Codex (same process group); just wait for it.
        try:
            rc = proc.wait(15)
        except subprocess.TimeoutExpired:
            rc = 130
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if shim:
            shim.shutdown()
            # Shim homes embed a one-off port; they are never reused.
            shutil.rmtree(home, ignore_errors=True)
    return 128 - rc if rc < 0 else rc


def cmd_shim(ns: argparse.Namespace) -> int:
    key = os.environ.get(ns.api_key_env) if ns.api_key_env else None
    srv = start_shim(ns.upstream, host=ns.host, port=ns.port, text_only=ns.text_only,
                     replay_reasoning=ns.replay_reasoning, api_key=key, model_override=ns.model)
    info("Responses shim on http://%s:%d/v1 -> %s/chat/completions" % (ns.host, srv.server_address[1], ns.upstream))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


# --------------------------------------------------------------------------- doctor

def _mark(ok: Optional[bool]) -> str:
    sym = {True: "✓", False: "✗", None: "!"}[ok]
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return sym
    return {True: "\033[32m", False: "\033[31m", None: "\033[33m"}[ok] + sym + "\033[0m"


def cmd_doctor(ns: argparse.Namespace) -> int:
    rows: List[Tuple[Optional[bool], str, str]] = []
    fatal = False

    def row(ok: Optional[bool], what: str, detail: str = "") -> None:
        nonlocal fatal
        rows.append((ok, what, detail))
        print("  %s %-34s %s" % (_mark(ok), what, detail))
        if ok is False:
            fatal = True

    print("cu-local %s doctor\n" % __version__)
    print("Mac")
    if sys.platform == "darwin":
        row(True, "macOS", subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True).stdout.strip())
        if screen_locked():
            row(False, "screen unlocked", "the screen is locked: Computer Use can't see any window until you unlock")
    else:
        row(None, "macOS", "not macOS (%s): only endpoint checks are meaningful here" % sys.platform)
    row(sys.version_info >= (3, 9), "python3", sys.version.split()[0])
    codex = codex_binary()
    row(os.path.exists(codex), "Codex in ChatGPT.app", codex if os.path.exists(codex) else "not found: install ChatGPT.app")
    try:
        man = find_plugin_manifest()
        load_cua_server(man)
        row(True, "Computer Use plugin", os.path.basename(os.path.dirname(man)))
    except (OSError, ValueError) as e:
        row(False, "Computer Use plugin", str(e))
    try:
        prof = pick_profile(load_model_cache())
        build_catalog_entry(prof, "probe", 32768, False)
        row(True, "Codex model profile", "cloning %s" % prof.get("slug"))
    except (OSError, ValueError) as e:
        row(False, "Codex model profile", str(e))

    endpoint = (ns.endpoint or os.environ.get("CU_ENDPOINT") or load_user_config().get("endpoint") or "").rstrip("/")
    key_env = ns.api_key_env or load_user_config().get("api_key_env")
    key = os.environ.get(key_env) if key_env else None
    print("\nEndpoint %s" % (endpoint or "(none)"))
    if not endpoint:
        row(False, "endpoint", "pass --endpoint http://host:port/v1")
        return 1
    try:
        models = list_models(endpoint, key)
        row(True, "GET /models", ", ".join(m["id"] for m in models[:4]))
    except Exception as e:  # noqa: BLE001
        row(False, "GET /models", str(e))
        return 1
    model = ns.model or load_user_config().get("model") or models[0]["id"]
    ctx = detect_context(models, model)
    row(True if ctx and ctx >= 32768 else None, "context window",
        "%s tokens%s" % (ctx or "unknown", "" if (ctx or 0) >= 32768 else ": cu-local needs >= 32k (use --context)"))
    pr = probe_responses(endpoint, model, key)
    if pr.get("ok"):
        row(True, "native /responses + tools", "%.1fs" % pr["seconds"])
        mode = "native"
    else:
        row(None, "native /responses + tools", "%s -> will use the built-in shim" % pr.get("reason"))
        mode = "shim"
    pc = probe_chat(endpoint, model, key)
    row(pc.get("ok") if mode == "shim" else (pc.get("ok") or None), "chat tool calling",
        "%.1fs" % pc["seconds"] if pc.get("ok") else str(pc.get("reason") or pc.get("detail")))
    pi = probe_chat(endpoint, model, key, image=True)
    vision = bool(pi.get("ok"))
    row(True if vision else None, "image input", "ok" if vision else "no vision: will run --text-only")
    if mode == "shim" and not pc.get("ok"):
        fatal = True

    print("\nRecommended: cu-local run --endpoint %s --model %s%s%s \"<task>\"" % (
        endpoint, model, " --shim on" if mode == "shim" else "", "" if vision else " --text-only"))
    if ns.save:
        cfg = load_user_config()
        cfg.update({"endpoint": endpoint, "model": model, "shim": "on" if mode == "shim" else "off",
                    "text_only": not vision})
        if ns.api_key_env:
            cfg["api_key_env"] = ns.api_key_env
        if ctx:
            cfg["context"] = ctx
        save_user_config(cfg)
        print("Saved to %s" % USER_CONFIG)
    print("\n%s" % ("All checks passed." if not fatal else "Some checks failed (see ✗ above)."))
    return 1 if fatal else 0


# --------------------------------------------------------------------------- bench

BENCH_TASKS = {
    "finder": ("Using ONLY the computer-use tools, use the Finder app: open a Finder window showing the folder "
               "{dir}, create a new folder inside it named made, then rename that folder to renamed. "
               "Confirm from the Finder UI."),
    "calculator": ("Using ONLY the computer-use tools, use the Calculator app to compute 48271 multiplied by 9137 "
                   "by operating its UI. Reply with only the number shown in the Calculator display."),
    "textedit": ("Using ONLY the computer-use tools, use the TextEdit app: create a new document, make it plain "
                 "text, type exactly the line: cu-local bench {token} and save it with the name bench.txt in the "
                 "folder {dir}. Confirm from the TextEdit UI."),
}


def bench_verify(task: str, d: str, last: str, token: str) -> bool:
    if task == "finder":
        return os.path.isdir(os.path.join(d, "renamed")) and not os.path.exists(os.path.join(d, "made"))
    if task == "calculator":
        return "441052127" in re.sub(r"[^0-9]", "", last)
    if task == "textedit":
        with contextlib.suppress(OSError):
            with open(os.path.join(d, "bench.txt"), errors="replace") as f:
                return ("cu-local bench %s" % token) in f.read()
    return False


def _kill_group(p: "subprocess.Popen[Any]") -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, sig)
        try:
            p.wait(10)
            return
        except subprocess.TimeoutExpired:
            continue


BENCH_APPS = ("Calculator", "TextEdit")


def _running(app: str) -> bool:
    return subprocess.run(["pgrep", "-x", app], capture_output=True).returncode == 0


def _quit_apps(enabled: bool) -> None:
    if not enabled:
        return
    for app in BENCH_APPS:
        subprocess.run(["pkill", "-x", app], capture_output=True)


def cmd_bench(ns: argparse.Namespace, extra: List[str]) -> int:
    def on_term(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)  # unwinds through the worker cleanup
    signal.signal(signal.SIGTERM, on_term)
    tasks = [t.strip() for t in ns.tasks.split(",") if t.strip()]
    for t in tasks:
        if t not in BENCH_TASKS:
            die("unknown task %s (choose from %s)" % (t, ", ".join(BENCH_TASKS)))
    # A visible folder: hidden paths (~/.cu-local) make Save dialogs needlessly hard.
    root = os.path.join(os.path.expanduser(ns.out), time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(root, exist_ok=True)
    me = os.path.abspath(sys.argv[0])
    results = []
    open_apps = [a for a in BENCH_APPS if _running(a)]
    if open_apps and not ns.quit_apps:
        info("%s already open. The bench works around open windows; pass --quit-apps to quit them "
             "between tasks for cleaner runs (unsaved documents in them would be lost)." % " and ".join(open_apps))
    print("cu-local bench -> %s\n" % root)
    for rep in range(1, ns.repeats + 1):
        for t in tasks:
            if screen_locked():
                info("the screen is locked: Computer Use can't see windows. Stopping the bench (unlock and re-run).")
                results.append({"task": t, "rep": rep, "ok": False, "skipped": "screen locked"})
                break
            token = uuid.uuid4().hex[:8]
            d = os.path.join(root, "%s-%d" % (t, rep))
            os.makedirs(d)
            _quit_apps(ns.quit_apps)
            time.sleep(1.5 if ns.quit_apps else 0)
            prompt = BENCH_TASKS[t].format(dir=d, token=token)
            argv = [sys.executable, me, "run", "--task", prompt]
            for flag in ("endpoint", "model", "shim", "context", "effort", "api_key_env", "idle_timeout"):
                v = getattr(ns, flag, None)
                if v:
                    argv += ["--" + flag.replace("_", "-"), str(v)]
            for flag in ("text_only", "replay_reasoning", "allow_shell"):
                if getattr(ns, flag, False):
                    argv.append("--" + flag.replace("_", "-"))
            argv += ["--", "--json", "-o", os.path.join(d, "last.txt")] + extra
            t0 = time.time()
            with open(os.path.join(d, "events.jsonl"), "w") as out, open(os.path.join(d, "stderr.txt"), "w") as err:
                # Own process group, so a timeout also stops Codex and the Computer Use server.
                p = subprocess.Popen(argv, stdout=out, stderr=err, start_new_session=True)
                rc = 124
                try:
                    rc = p.wait(timeout=ns.timeout)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    # Ctrl-C does not reach a detached session: stop it on every exit path.
                    if p.poll() is None:
                        _kill_group(p)
            secs = time.time() - t0
            last = ""
            with contextlib.suppress(OSError):
                with open(os.path.join(d, "last.txt"), errors="replace") as f:
                    last = f.read()
            ok = bench_verify(t, d, last, token)
            usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
            calls = shell = 0
            with open(os.path.join(d, "events.jsonl"), errors="replace") as f:
                for line in f:
                    with contextlib.suppress(ValueError):
                        e = json.loads(line)
                        if e.get("type") == "turn.completed":
                            for k in usage:
                                usage[k] += int((e.get("usage") or {}).get(k) or 0)
                        it = e.get("item") or {}
                        if e.get("type") == "item.completed" and it.get("type") == "mcp_tool_call":
                            calls += 1
                        if e.get("type") == "item.completed" and it.get("type") == "command_execution":
                            shell += 1
            r = {"task": t, "rep": rep, "ok": ok, "rc": rc, "seconds": round(secs, 1), "tool_calls": calls,
                 "shell_calls": shell, **usage}
            results.append(r)
            print("  %s %-10s #%d  %6.1fs  %3d calls  %8d in  %6d out" % (
                _mark(ok), t, rep, secs, calls, usage["input_tokens"], usage["output_tokens"]))
        if results and results[-1].get("skipped"):
            break
    _quit_apps(ns.quit_apps)
    passed = sum(1 for r in results if r["ok"])
    summary = {"version": __version__, "endpoint_kind": ns.label or "", "passed": passed, "total": len(results),
               "results": results}
    with open(os.path.join(root, "results.json"), "w") as f:
        json.dump(summary, f, indent=2)
    if any(r.get("skipped") for r in results):
        print("\nStopped early: the screen is locked. Unlock the Mac and run again.")
        return 2
    print("\n%d/%d passed. Results: %s" % (passed, len(results), os.path.join(root, "results.json")))
    return 0 if passed == len(results) else 1


# --------------------------------------------------------------------------- CLI

def add_endpoint_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--endpoint", help="OpenAI-compatible base URL, e.g. http://gpu-box:8000/v1 (env CU_ENDPOINT)")
    p.add_argument("--model", help="model id (default: first from /v1/models; env CU_MODEL)")
    p.add_argument("--api-key-env", help="name of the env var holding the endpoint's API key, if any")


def add_run_args(p: argparse.ArgumentParser) -> None:
    add_endpoint_args(p)
    p.add_argument("--shim", choices=["auto", "on", "off"], help="Responses->Chat shim (default auto: probe once)")
    p.add_argument("--text-only", action="store_true", help="model has no vision; never send images")
    p.add_argument("--context", type=int, help="context window to tell Codex (default: from /v1/models, else 131072)")
    p.add_argument("--effort", choices=["low", "medium", "high"], help="reasoning effort (default low)")
    p.add_argument("--replay-reasoning", action="store_true", help="shim: send prior reasoning back as reasoning_content")
    p.add_argument("--idle-timeout", type=int, help="seconds to wait for a slow backend between tokens (default 600)")
    p.add_argument("--allow-shell", action="store_true", help="also give the model a shell (off by default)")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="cu-local", description="Run Codex Computer Use on your Mac with your own model.")
    ap.add_argument("--version", action="version", version="cu-local " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="run one computer-use task")
    add_run_args(p)
    p.add_argument("--cwd", help="working directory for Codex (default ~/.cu-local/work)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("task_pos", nargs="?", metavar="TASK", help="what to do, in plain language")
    p.add_argument("--task", help=argparse.SUPPRESS)

    p = sub.add_parser("doctor", help="check this Mac and an endpoint, recommend settings")
    add_endpoint_args(p)
    p.add_argument("--save", action="store_true", help="save the endpoint and recommended settings as defaults")

    p = sub.add_parser("shim", help="serve the Responses->Chat shim standalone")
    p.add_argument("--upstream", required=True, help="Chat Completions base URL, e.g. http://localhost:8080/v1")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8077)
    p.add_argument("--model", help="override the model id sent upstream")
    p.add_argument("--text-only", action="store_true")
    p.add_argument("--replay-reasoning", action="store_true")
    p.add_argument("--api-key-env")

    p = sub.add_parser("bench", help="run the reference tasks and verify the results")
    add_run_args(p)
    p.add_argument("--tasks", default="finder,calculator,textedit")
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--timeout", type=int, default=600, help="seconds per task")
    p.add_argument("--label", help="free-text label stored in results.json")
    p.add_argument("--out", default="~/cu-local-bench", help="results folder (default ~/cu-local-bench; keep it visible)")
    p.add_argument("--quit-apps", action="store_true",
                   help="quit Calculator and TextEdit before each task (loses unsaved documents in them)")

    ns = ap.parse_args(argv)
    if ns.cmd == "run":
        ns.task = ns.task or ns.task_pos
        if not ns.task:
            die('usage: cu-local run [options] "task"')
        return cmd_run(ns, extra)
    if ns.cmd == "doctor":
        return cmd_doctor(ns)
    if ns.cmd == "shim":
        return cmd_shim(ns)
    if ns.cmd == "bench":
        return cmd_bench(ns, extra)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
