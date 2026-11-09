"""Offline tests for cu-local. Synthetic fixtures only: no OpenAI content is committed.

Run: python3 -m unittest discover -s tests -v
"""
import http.server
import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("cu_local", os.path.join(HERE, "..", "cu_local.py"))
cu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cu)  # type: ignore[union-attr]


def fake_profile(**over):
    p = {
        "slug": "gpt-6.1-sol", "display_name": "x", "tool_mode": "code_mode_only", "supports_search_tool": True,
        "use_responses_lite": True, "multi_agent_version": "v2", "multi_agent_reasoning_effort": "xhigh",
        "apply_patch_tool_type": "freeform", "web_search_tool_type": "text_and_image",
        "experimental_supported_tools": ["clock"], "service_tiers": [{"id": "priority"}],
        "model_messages": {"instructions_template": "TEMPLATE", "multi_agent": {"x": 1}, "guardian_v2": {}},
        "context_window": 500000, "input_modalities": ["text", "image"],
    }
    p.update(over)
    return p


class CatalogTests(unittest.TestCase):
    def test_entry_disables_openai_only_machinery(self):
        e = cu.build_catalog_entry(fake_profile(), "my-model", 65536, text_only=False)
        self.assertEqual(e["slug"], "my-model")
        self.assertEqual(e["tool_mode"], "direct")
        self.assertFalse(e["supports_search_tool"])
        self.assertFalse(e["use_responses_lite"])
        self.assertEqual(e["service_tiers"], [])
        for k in ("multi_agent_version", "multi_agent_reasoning_effort", "web_search_tool_type", "apply_patch_tool_type"):
            self.assertNotIn(k, e, k)
        self.assertNotIn("multi_agent", e["model_messages"])
        self.assertNotIn("guardian_v2", e["model_messages"])
        self.assertEqual(e["model_messages"]["instructions_template"], "TEMPLATE")
        self.assertEqual(e["context_window"], 65536)
        self.assertLess(e["auto_compact_token_limit"], 65536)
        self.assertEqual(e["input_modalities"], ["text", "image"])
        # Codex accepts null only for these two optional UI fields (verified against ChatGPT.app 26.1103).
        self.assertEqual({k for k, v in e.items() if v is None}, {"availability_nux", "upgrade"})

    def test_text_only_and_small_context(self):
        e = cu.build_catalog_entry(fake_profile(), "m", 32768, text_only=True)
        self.assertEqual(e["input_modalities"], ["text"])
        self.assertLessEqual(e["truncation_policy"]["limit"], 32768 // 8)

    def test_allow_shell_keeps_apply_patch(self):
        e = cu.build_catalog_entry(fake_profile(), "m", 65536, text_only=False, allow_shell=True)
        self.assertEqual(e["apply_patch_tool_type"], "freeform")

    def test_base_not_mutated(self):
        base = fake_profile()
        cu.build_catalog_entry(base, "m", 65536, text_only=False)
        self.assertEqual(base["tool_mode"], "code_mode_only")
        self.assertIn("multi_agent", base["model_messages"])

    def test_pick_profile_prefers_then_falls_back(self):
        self.assertEqual(cu.pick_profile([fake_profile(slug="a"), fake_profile(slug="gpt-6-sol")])["slug"], "gpt-6-sol")
        self.assertEqual(cu.pick_profile([{"slug": "z"}, fake_profile(slug="renamed-2027")])["slug"], "renamed-2027")
        with self.assertRaises(ValueError):
            cu.pick_profile([{"slug": "bare"}])

    def test_profile_without_instructions_rejected(self):
        p = fake_profile()
        del p["model_messages"]
        with self.assertRaises(ValueError):
            cu.build_catalog_entry(p, "m", 65536, text_only=False)


class ConfigTests(unittest.TestCase):
    CUA = {"command": "/bin/sh", "args": ["-c", 'exec "$@"', "cua", "/path/node_repl"],
           "enabled_tools": ["js", "js_reset"], "env_vars": ["A"],
           "env": {"PLAIN": "x", "EMOJI": "\U0001F50E task", "QUOTE": 'say "hi"\n'}}

    def render(self, **kw):
        args = dict(model="m", base_url="http://h:1/v1", effort="low", catalog_path="/c.json",
                    api_key_env=None, allow_shell=False, idle_timeout_ms=600000)
        args.update(kw)
        return cu.render_codex_config(self.CUA, **args)

    def test_valid_toml_when_parser_available(self):
        txt = self.render()
        self.assertNotIn("\\ud83d", txt)  # surrogate escapes are invalid TOML
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            self.skipTest("tomllib needs Python 3.11")
        d = tomllib.loads(txt)
        self.assertEqual(d["model_providers"]["cu_local"]["wire_api"], "responses")
        self.assertFalse(d["features"]["shell_tool"])
        self.assertEqual(d["mcp_servers"]["cua_repl"]["env"]["EMOJI"], "\U0001F50E task")
        self.assertEqual(d["web_search"], "disabled")

    def test_shell_flag(self):
        self.assertIn("shell_tool = false", self.render())
        self.assertNotIn("shell_tool", self.render(allow_shell=True))

    def test_api_key_env(self):
        self.assertIn('env_key = "MY_KEY"', self.render(api_key_env="MY_KEY"))


NS_TOOLS = [
    {"type": "function", "name": "exec_command", "parameters": {"type": "object"}},
    {"type": "custom", "name": "apply_patch"},
    {"type": "web_search"},
    {"type": "namespace", "name": "mcp__cua_repl", "description": "ui", "tools": [
        {"type": "function", "name": "js", "description": "run js", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}}},
        {"type": "function", "name": "js_reset", "parameters": {"type": "object"}}]},
]

IMG = "data:image/png;base64,AAAA"


def convo():
    return {
        "model": "m", "stream": True, "store": False, "reasoning": {"effort": "low"}, "tool_choice": "auto",
        "parallel_tool_calls": True, "tools": NS_TOOLS,
        "input": [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "SYS1"}]},
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "SYS2"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "do it"}]},
            {"type": "reasoning", "summary": [], "content": [{"type": "reasoning_text", "text": "think"}]},
            {"type": "function_call", "name": "js", "namespace": "mcp__cua_repl", "arguments": '{"code":"1"}', "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": [
                {"type": "input_text", "text": "Output:"}, {"type": "input_image", "image_url": IMG}]},
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "LATE"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
        ],
    }


class TranslateRequestTests(unittest.TestCase):
    def test_tools_flattened_and_non_function_dropped(self):
        chat, names = cu.translate_request(convo())
        fn = [t["function"]["name"] for t in chat["tools"]]
        self.assertEqual(fn, ["exec_command", "mcp__cua_repl__js", "mcp__cua_repl__js_reset"])
        self.assertEqual(names["mcp__cua_repl__js"], ("mcp__cua_repl", "js"))
        self.assertEqual(chat["tool_choice"], "auto")
        self.assertTrue(chat["stream_options"]["include_usage"])
        self.assertEqual(chat["reasoning_effort"], "low")

    def test_top_level_combinators_flattened(self):
        schema = {"oneOf": [
            {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}, "required": ["a", "b"]},
            {"type": "object", "properties": {"a": {"type": "string"}, "c": {"type": "boolean"}}, "required": ["a"]}]}
        out = cu.flatten_schema(schema)
        self.assertEqual(out["type"], "object")
        self.assertEqual(sorted(out["properties"]), ["a", "b", "c"])
        self.assertEqual(out["required"], ["a"])
        for k in ("oneOf", "anyOf", "allOf"):
            self.assertNotIn(k, out)
        allof = cu.flatten_schema({"allOf": [{"properties": {"x": {}}, "required": ["x"]},
                                             {"properties": {"y": {}}, "required": ["y"]}]})
        self.assertEqual(allof["required"], ["x", "y"])
        plain = {"type": "object", "properties": {"q": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
        self.assertIs(cu.flatten_schema(plain), plain)  # nested combinators are left alone
        tools, _ = cu.translate_tools([{"type": "function", "name": "t", "parameters": schema}])
        self.assertNotIn("oneOf", tools[0]["function"]["parameters"])

    def test_message_sequence(self):
        chat, _ = cu.translate_request(convo())
        roles = [m["role"] for m in chat["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user", "user", "assistant"])
        m = chat["messages"]
        self.assertEqual(m[0]["content"], "SYS1\n\nSYS2")
        self.assertEqual(m[2]["tool_calls"][0]["function"]["name"], "mcp__cua_repl__js")
        self.assertEqual(m[2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(m[3]["tool_call_id"], "c1")
        self.assertIn("[image attached below]", m[3]["content"])
        self.assertEqual(m[4]["content"][1]["image_url"]["url"], IMG)  # image hoisted after the tool message
        self.assertIn("LATE", m[5]["content"])                          # late developer -> user
        self.assertEqual(m[6]["content"], "done")
        self.assertNotIn("reasoning_content", m[2])

    def test_text_only_strips_images(self):
        chat, _ = cu.translate_request(convo(), text_only=True)
        self.assertNotIn("image_url", json.dumps(chat))
        self.assertIn("image omitted", json.dumps(chat))

    def test_replay_reasoning(self):
        chat, _ = cu.translate_request(convo(), replay_reasoning=True)
        self.assertEqual(chat["messages"][2]["reasoning_content"], "think")

    def test_parallel_calls_share_one_assistant_turn(self):
        req = convo()
        req["input"] = req["input"][:5] + [
            {"type": "function_call", "name": "js_reset", "namespace": "mcp__cua_repl", "arguments": "{}", "call_id": "c2"},
            {"type": "function_call_output", "call_id": "c1", "output": "a"},
            {"type": "function_call_output", "call_id": "c2", "output": "b"}]
        chat, _ = cu.translate_request(req)
        roles = [m["role"] for m in chat["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "tool"])
        self.assertEqual(len(chat["messages"][2]["tool_calls"]), 2)

    def test_string_input_and_instructions(self):
        chat, _ = cu.translate_request({"model": "m", "instructions": "BE BRIEF", "input": "hello"})
        self.assertEqual(chat["messages"], [{"role": "system", "content": "BE BRIEF"}, {"role": "user", "content": "hello"}])
        self.assertNotIn("tools", chat)


def chunks():
    names = {"mcp__cua_repl__js": ("mcp__cua_repl", "js")}
    return names, [
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "hm"}}]},
        {"choices": [{"delta": {"reasoning_content": "m"}}]},
        {"choices": [{"delta": {"content": "Let me "}}]},
        {"choices": [{"delta": {"content": "check."}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_9", "function": {"name": "mcp__cua_repl__js", "arguments": '{"co'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'de":"x"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                                  "prompt_tokens_details": {"cached_tokens": 64},
                                  "completion_tokens_details": {"reasoning_tokens": 5}}},
    ]


class StreamTranslatorTests(unittest.TestCase):
    def run_stream(self):
        names, cs = chunks()
        tr = cu.StreamTranslator("m", names)
        evs = tr.start()
        for c in cs:
            evs += tr.feed(c)
        evs += tr.finish_events()
        return evs

    def test_event_order_and_items(self):
        evs = self.run_stream()
        types = [e["type"] for e in evs]
        self.assertEqual(types[:2], ["response.created", "response.in_progress"])
        self.assertEqual(types[-1], "response.completed")
        self.assertEqual([e["sequence_number"] for e in evs], list(range(len(evs))))
        done = [e["item"] for e in evs if e["type"] == "response.output_item.done"]
        self.assertEqual([d["type"] for d in done], ["reasoning", "message", "function_call"])
        self.assertEqual(done[0]["content"][0]["text"], "hmm")
        self.assertEqual(done[1]["content"][0]["text"], "Let me check.")
        fc = done[2]
        self.assertEqual((fc["name"], fc["namespace"], fc["call_id"]), ("js", "mcp__cua_repl", "call_9"))
        self.assertEqual(json.loads(fc["arguments"]), {"code": "x"})
        self.assertLess(types.index("response.function_call_arguments.done"), types.index("response.completed"))

    def test_usage_mapping(self):
        u = self.run_stream()[-1]["response"]["usage"]
        self.assertEqual(u, {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 64}, "output_tokens": 20,
                             "output_tokens_details": {"reasoning_tokens": 5}, "total_tokens": 120})

    def test_missing_usage_is_synthesised_as_zero(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]})
        final = tr.finish_events()[-1]
        self.assertEqual(final["type"], "response.completed")
        self.assertEqual(final["response"]["usage"]["total_tokens"], 0)

    def test_length_finish_is_incomplete(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"content": "hi"}, "finish_reason": "length"}]})
        self.assertEqual(tr.finish_events()[-1]["type"], "response.incomplete")

    def test_reasoning_field_alias(self):
        tr = cu.StreamTranslator("m", {})
        evs = tr.feed({"choices": [{"delta": {"reasoning": "r"}}]})
        self.assertEqual(evs[-1]["type"], "response.reasoning_text.delta")


class ReviewRegressionTests(unittest.TestCase):
    """Defects found in the pre-release review, each with its failing input."""

    def test_same_index_different_ids_are_two_calls(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "f", "arguments": '{"k":"a"}'}}]}}]})
        tr.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "b", "function": {"name": "f", "arguments": '{"k":"b"}'}}]}, "finish_reason": "tool_calls"}]})
        calls = [o for o in tr.finish_events()[-1]["response"]["output"] if o["type"] == "function_call"]
        self.assertEqual([json.loads(c["arguments"]) for c in calls], [{"k": "a"}, {"k": "b"}])
        self.assertFalse(any(k.startswith("_") for c in calls for k in c))

    def test_non_stream_tool_calls_without_index(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"message": {"tool_calls": [
            {"id": "a", "function": {"name": "f", "arguments": "{}"}},
            {"id": "b", "function": {"name": "g", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]})
        calls = [o for o in tr.finish_events()[-1]["response"]["output"] if o["type"] == "function_call"]
        self.assertEqual([c["name"] for c in calls], ["f", "g"])

    def test_error_chunk_raises(self):
        tr = cu.StreamTranslator("m", {})
        with self.assertRaises(cu.UpstreamError):
            tr.feed({"error": {"message": "context length exceeded"}})

    def test_empty_or_truncated_stream_is_not_success(self):
        self.assertEqual(cu.StreamTranslator("m", {}).finish_events()[-1]["type"], "response.failed")
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"content": "par"}}]})
        self.assertEqual(tr.finish_events()[-1]["type"], "response.incomplete")

    def test_length_with_tool_call_is_incomplete(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "f", "arguments": '{"x":'}}]}, "finish_reason": "length"}]})
        evs = tr.finish_events()
        self.assertEqual(evs[-1]["type"], "response.incomplete")
        done = [e["item"] for e in evs if e["type"] == "response.output_item.done"]
        self.assertEqual(done[0]["status"], "incomplete")
        self.assertEqual(done[0]["arguments"], '{"x":')

    def test_refusal_is_surfaced(self):
        tr = cu.StreamTranslator("m", {})
        tr.feed({"choices": [{"delta": {"refusal": "I can't help with that."}, "finish_reason": "stop"}]})
        out = tr.finish_events()[-1]["response"]["output"]
        self.assertEqual(out[0]["content"][0]["text"], "I can't help with that.")

    def test_probe_requires_namespace(self):
        saved = cu.http_json
        try:
            def fake(url, body=None, timeout=30, api_key=None, out=None):
                return 200, {"output": out}
            for out, ok in (([{"type": "function_call", "name": "ping"}], False),
                            ([{"type": "function_call", "name": "ping", "namespace": "mcp__probe"}], True),
                            ([{"type": "function_call", "name": "mcp__probe__ping"}], True)):
                cu.http_json = lambda url, body=None, timeout=30, api_key=None, _o=out: (200, {"output": _o})
                self.assertEqual(cu.probe_responses("http://x/v1", "m", attempts=1)["ok"], ok, out)
        finally:
            cu.http_json = saved

    def test_upstream_error_body_is_redacted(self):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"error":"Invalid API key: SECRETKEY123456 for Bearer SECRETKEY123456"}'
                self.send_response(401)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        import tempfile
        up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        with tempfile.NamedTemporaryFile("r") as log:
            shim = cu.start_shim("http://127.0.0.1:%d/v1" % up.server_address[1], api_key="SECRETKEY123456", log=log.name)
            try:
                code, data = cu.http_json("http://127.0.0.1:%d/v1/responses" % shim.server_address[1], {"model": "m", "input": "hi"}, timeout=5)
                self.assertEqual(code, 401)
                self.assertNotIn("SECRETKEY123456", json.dumps(data))
                self.assertNotIn("SECRETKEY123456", open(log.name).read())
            finally:
                shim.shutdown()
                up.shutdown()

    def test_iter_sse_plain_json_body(self):
        import io
        body = json.dumps({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}, indent=2).encode() + b"\n"
        self.assertEqual(list(cu.iter_sse(io.BufferedReader(io.BytesIO(body))))[0]["choices"][0]["message"]["content"], "hi")

    def test_orphan_and_dangling_tool_messages_repaired(self):
        req = {"model": "m", "input": [
            {"type": "message", "role": "user", "content": "go"},
            {"type": "function_call_output", "call_id": "ghost", "output": "stale"},
            {"type": "function_call", "name": "f", "arguments": "{}", "call_id": "c1"},
            {"type": "message", "role": "user", "content": "next"}]}
        chat, _ = cu.translate_request(req)
        m = chat["messages"]
        self.assertEqual([x["role"] for x in m], ["user", "user", "assistant", "tool", "user"])
        self.assertIn("stale", m[1]["content"])
        self.assertEqual(m[3]["tool_call_id"], "c1")

    def test_toml_edge_cases(self):
        self.assertIn("\\u007f", cu.toml_str("a\x7fb"))
        self.assertNotIn("\x7f", cu.toml_str("a\x7fb"))
        cu.toml_str("bad \ud800 surrogate").encode("utf-8")  # must not raise
        cfg = cu.render_codex_config(dict(ConfigTests.CUA, env={"A KEY.with dot": "v"}), model="m", base_url="u",
                                     effort="low", catalog_path="c", api_key_env=None, allow_shell=False,
                                     idle_timeout_ms=1)
        self.assertIn('"A KEY.with dot" = "v"', cfg)
        try:
            import tomllib
        except ImportError:
            return
        self.assertEqual(tomllib.loads(cfg)["mcp_servers"]["cua_repl"]["env"]["A KEY.with dot"], "v")
        self.assertEqual(tomllib.loads("x = " + cu.toml_str("a\x7fb"))["x"], "a\x7fb")


class FakeChat(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen = []
    gets = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        FakeChat.gets.append(self.path)
        body = json.dumps({"object": "list", "data": [{"id": "fake-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeChat.seen.append(body)
        _, cs = chunks()
        data = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in cs) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ShimServerTests(unittest.TestCase):
    def test_end_to_end_over_http(self):
        up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        shim = cu.start_shim("http://127.0.0.1:%d/v1" % up.server_address[1])
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/v1/responses" % shim.server_address[1],
                                         data=json.dumps(convo()).encode(), headers={"Content-Type": "application/json"})
            evs = []
            with urllib.request.urlopen(req, timeout=10) as r:
                for line in r:
                    if line.startswith(b"data:"):
                        evs.append(json.loads(line[5:]))
            self.assertEqual(evs[-1]["type"], "response.completed")
            fc = [o for o in evs[-1]["response"]["output"] if o["type"] == "function_call"][0]
            self.assertEqual(fc["namespace"], "mcp__cua_repl")
            sent = FakeChat.seen[-1]
            self.assertEqual(sent["messages"][0]["role"], "system")
            self.assertTrue(sent["stream"])
        finally:
            shim.shutdown()
            up.shutdown()

    def test_models_with_query_string(self):
        up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        shim = cu.start_shim("http://127.0.0.1:%d/v1" % up.server_address[1])
        try:
            FakeChat.gets = []
            code, data = cu.http_json("http://127.0.0.1:%d/v1/models?client_version=1" % shim.server_address[1], timeout=5)
            self.assertEqual(code, 200)
            self.assertEqual(data["data"][0]["id"], "fake-model")
            self.assertEqual(FakeChat.gets, ["/v1/models"])
            code, data = cu.http_json("http://127.0.0.1:%d/v1?x=1" % shim.server_address[1], timeout=5)
            self.assertTrue(data["ok"])
        finally:
            shim.shutdown()
            up.shutdown()

    def test_non_stream_request(self):
        up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        shim = cu.start_shim("http://127.0.0.1:%d/v1" % up.server_address[1])
        try:
            body = dict(convo(), stream=False)
            code, data = cu.http_json("http://127.0.0.1:%d/v1/responses" % shim.server_address[1], body, timeout=10)
            self.assertEqual(code, 200)
            self.assertEqual(data["status"], "completed")
            self.assertEqual(data["usage"]["input_tokens"], 100)
        finally:
            shim.shutdown()
            up.shutdown()


class HomeTests(unittest.TestCase):
    def test_homes_are_per_setting_and_reused(self):
        import tempfile
        import types
        with tempfile.TemporaryDirectory() as tmp:
            man = os.path.join(tmp, "mcp.json")
            with open(man, "w") as f:
                json.dump({"mcpServers": {"cua_repl": ConfigTests.CUA}}, f)
            saved = (cu.STATE, cu.find_plugin_manifest, cu.load_model_cache)
            cu.STATE = os.path.join(tmp, "state")
            cu.find_plugin_manifest = lambda *a: man
            cu.load_model_cache = lambda *a: [fake_profile()]
            try:
                s = types.SimpleNamespace(text_only=False, allow_shell=False, effort="low", api_key_env=None,
                                          shim_mode="native", idle_timeout=600)
                h1, w = cu.prepare_home(s, "m", "http://a:1/v1", 65536)
                h2, _ = cu.prepare_home(s, "m", "http://b:1/v1", 65536)
                h3, _ = cu.prepare_home(s, "m", "http://a:1/v1", 65536)
                self.assertNotEqual(h1, h2)
                self.assertEqual(h1, h3)
                with open(os.path.join(h1, "config.toml")) as f:
                    cfg = f.read()
                self.assertIn('base_url = "http://a:1/v1"', cfg)
                self.assertIn(os.path.join(h1, "catalog.json"), cfg)
                self.assertTrue(os.path.isdir(w))
            finally:
                cu.STATE, cu.find_plugin_manifest, cu.load_model_cache = saved


class MiscTests(unittest.TestCase):
    def test_solid_png_is_png(self):
        import base64
        raw = base64.b64decode(cu.solid_png(8, (255, 0, 0)))
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_bench_verify(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(cu.bench_verify("finder", d, "", "t"))
            os.mkdir(os.path.join(d, "renamed"))
            self.assertTrue(cu.bench_verify("finder", d, "", "t"))
            self.assertTrue(cu.bench_verify("calculator", d, "441,052,127", "t"))
            self.assertFalse(cu.bench_verify("calculator", d, "44,104,299", "t"))
            with open(os.path.join(d, "bench.txt"), "w") as f:
                f.write("cu-local bench abc\n")
            self.assertTrue(cu.bench_verify("textedit", d, "", "abc"))

    def test_detect_context(self):
        self.assertEqual(cu.detect_context([{"id": "m", "max_model_len": 1000}], "m"), 1000)
        self.assertEqual(cu.detect_context([{"id": "m", "meta": {"n_ctx": 4096}}], "m"), 4096)
        self.assertEqual(cu.detect_context([{"id": "m", "meta": {"n_ctx": 32768, "n_ctx_train": 40960}}], "m"), 32768)
        self.assertEqual(cu.detect_context([{"id": "m", "meta": {"n_ctx_train": 40960}}], "m"), 40960)
        self.assertIsNone(cu.detect_context([{"id": "x"}], "m"))


if __name__ == "__main__":
    unittest.main()
