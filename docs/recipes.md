# Recipes

cu-local talks to any server with an OpenAI-compatible `/v1` API. It uses `/v1/responses` when the server handles namespaced tool calls there, and otherwise falls back to `/v1/chat/completions` through the built-in shim. `cu-local doctor` works out which applies, so for most setups you only need the URL.

Every recipe ends the same way:

```bash
cu-local doctor --endpoint http://SERVER:PORT/v1 --save   # probes, then saves the settings it recommends
cu-local bench --repeats 2                                # optional: reference tasks, checked by machine
cu-local run "Open Calculator and compute 48271 × 9137"
```

Status meanings: **tested** means we ran the reference bench on it. **Emulated** means we reproduced its protocol, context and text-only limits on other hardware. **Community** means it's untested here, and a [hardware report](../.github/ISSUE_TEMPLATE/hardware-report.yml) is welcome.

## DeepSeek V4.1 Flash on a multi-GPU server (vLLM or SGLang) — tested

This is what the benchmark numbers come from: DeepSeek-V4.1-Flash-1017 on 4× RTX PRO 6000 Blackwell (96 GB each), served by vLLM with tensor parallelism 4.

The server needs three things:
- tool calling enabled (in vLLM, `--enable-auto-tool-choice` plus the tool-call parser your vLLM release documents for DeepSeek V4.x);
- reasoning parsing, so thinking arrives as `reasoning_content` and not inline in the answer;
- a context of 64k or more (cu-local's first step is about 8k tokens, and accessibility trees add up).

```bash
vllm serve deepseek-ai/DeepSeek-V4.1-Flash-1017 \
  --tensor-parallel-size 4 \
  --enable-auto-tool-choice --tool-call-parser <deepseek-v4 parser for your vLLM> \
  --reasoning-parser <deepseek-v4 reasoning parser> \
  --max-model-len 262144 --host 0.0.0.0 --port 8000
```

vLLM and SGLang serve `/v1/responses` with namespaced tools, so cu-local runs natively with no shim. Expect Finder, Calculator and TextEdit in roughly 10–40 seconds each.

## DeepSeek V4.1 Flash on one DGX Spark (128 GB) — emulated

V4.1 Flash only fits a single Spark at about 1.6 bits per weight, with the Engram tables left on the SSD. Two community builds do this:
- [dedene/DeepSeek-V4.1-Flash-1017-DGX-Spark](https://github.com/dedene/DeepSeek-V4.1-Flash-1017-DGX-Spark): a vector-quantised GGUF served by `ds4-dfm-rs`, using about 110 GB in total;
- [unsloth/DeepSeek-V4.1-Flash-1017-GGUF](https://huggingface.co/unsloth/DeepSeek-V4.1-Flash-1017-GGUF): `UD-IQ1_S` on llama.cpp.

These builds are usually text-only, have a smaller context, and are slow (around 10–15 tokens/s). Tell cu-local:

```bash
cu-local doctor --endpoint http://spark.local:8080/v1 --save
# doctor will usually pick these on its own; to force them:
cu-local run --shim on --text-only --context 32768 --idle-timeout 1200 "…"
```

Our emulation ran the same model through the shim, text-only, with a 32k context. It passed 5/6 reference runs. The one failure was a digit the model mistyped and couldn't see to correct: without screenshots, it relies only on the accessibility tree. We expect the Spark to be several times slower per step than the multi-GPU numbers.

## DeepSeek V4.1 Flash on one RTX PRO 6000 (96 GB) — community

The model doesn't fit in 96 GB of VRAM. llama.cpp can keep the attention and shared weights on the GPU and stream the routed experts from system RAM (256 GB or more recommended):

```bash
llama-server -m DeepSeek-V4.1-Flash-1017-UD-IQ1_S.gguf --jinja -c 32768 \
  -ngl 99 -ot "exps=CPU" --host 0.0.0.0 --port 8080
```

Then follow the Spark flags above (`--text-only` unless your build maps the vision tower). llama-server's own `/v1/responses` drops namespaced tools, so cu-local uses the shim. doctor detects this.

## Ollama or LM Studio — tested (Ollama)

```bash
OLLAMA_HOST=0.0.0.0:11434 OLLAMA_CONTEXT_LENGTH=32768 ollama serve
ollama pull qwen3-vl:8b
cu-local doctor --endpoint http://SERVER:11434/v1 --model qwen3-vl:8b --save
```

Ollama's `/v1/responses` also drops namespaced tools, so cu-local uses the shim over `/v1/chat/completions`. Set the context explicitly: Ollama's `/v1/models` doesn't report it. Small models work as a smoke test, but they succeed far less often than DeepSeek-class models on multi-step tasks.

## llama.cpp with any GGUF — tested

```bash
llama-server -m model.gguf --jinja -c 32768 --host 0.0.0.0 --port 8080
```

`--jinja` is required for tool calls. Without it, llama-server returns tool calls as plain text.

## Servers that need an API key

```bash
export MY_SERVER_KEY=…
cu-local doctor --endpoint https://… --api-key-env MY_SERVER_KEY --save
```

cu-local stores the variable's name, never the key itself.
