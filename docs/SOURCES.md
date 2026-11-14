# Verified upstream facts (2026-11-14)

Checked against the real repos and public docs, not the AI-written spec. If the spec disagrees, this file wins.

## nace-ai/intern (Apache-2.0, v0.30.1, commit 819c8de)
- Python >=3.12. Install: `uv tool install "intern-agent[acp] @ git+https://github.com/nace-ai/intern@v0.30.1"`.
- System 2 = `provider/model` via `--system2`, `$INTERN_SYSTEM2` or `config.toml`. Providers: openai, openrouter, fireworks, together, deepinfra, groq, cerebras, anthropic, plus local ollama/llamacpp/lmstudio/vllm. Custom OpenAI-compatible providers go in `config.toml` `[providers.<name>]`.
- Qwen 3.8 27B ids: `openrouter/qwen/qwen3.8-27b`, `fireworks/accounts/fireworks/models/qwen3p8-27b`, `cerebras/qwen-3.8-27b*`.
- **No Nace-hosted System 2 exists.** Nace hosts only System 1 (Drex) at `https://console.nace.ai/v1`, key `NACE_API_KEY` (legacy `DREX_API_KEY`), default model `drex-latest` (set `drex-max` explicitly).
- Python API: `Bootstrap(Options(cwd, system2)).session(emit)`, then `session.run/start/steer/follow_up/abort`. JSON-RPC over stdio: `intern rpc` (`prompt`, `steer`, `follow_up`, `abort`, `get_state`, ...); events arrive as `method: event`. ACP: `intern acp`. Headless: `intern -p --json`.
- Custom tools: subclass `Extension`, override `tools()`; a `Tool` declares `name`, `description`, `Args` (pydantic) and `effects`. Non-read-only tools are judged by System 1 unless exempted.
- `INTERN_HOME` isolates config, sessions and memory.

## nace-ai/drex-browser-harness-pub (MIT)
- Drex Max: `POST https://console.nace.ai/v1/systemone`, model `drex-max`, bearer `DREX_API_KEY`.
- Helper LLM providers: Anthropic, OpenAI, Google, Fireworks, OpenRouter. Offered: claude-haiku-5-5 (default), claude-sonnet-5-5, claude-opus-5-5, gpt-6-luna, gemini-3.8-flash, glm-5p3-flash.
- **The MCP `browser_task` path always uses the default (Anthropic Haiku) helper.** The CLI runner and Chrome extension accept `--model`/settings for non-Anthropic helpers. Qwen is not offered.
- Needs Node >=22.18. Build: `npm ci`, `npm run build:release`, `node runner/bin/nace-browser.mjs install`. MCP: `nace-browser mcp` exposes `browser_task`, `browser_login`, `browser_profiles` (headless). The extension pairing server listens on 127.0.0.1:47615 and needs `DREX_PAIRING_CODE`.

## NDI (nace-sdk)
- `pip install nace-sdk` (Python 3.10+). `NaceClient()` reads `NACE_API_KEY`. Same balance pays for NDI and System 1.
- Python: `client.documents.parse(UrlSource|file.as_source(), output={"formats":["markdown"]})`, `client.documents.upload(path)`, `client.documents.extract(source, schema=...)`, `client.jobs.wait(job_id)`.
- Split, classify and ground are REST: `POST /v1/documents/{split,classify,ground}`. Ground takes `targets:[{id,text}]` and returns locations. The exact page/bbox shape is **unverified**; check it on the first real call.

## Not verified
Organizer rules on Claude API keys and pre-existing code. Ground response shape. Silent-speech checkpoint licence.
