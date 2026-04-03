# Web Search Approaches

This note compares four web-search designs:

1. Codex in `~/src/codex`
2. The Anthropic-backed wrapper in `~/src/aitool`
3. Gemini CLI in `~/src/gemini-cli`
4. This repo's current `llm_agent.tools.web_search` implementation

The goal is to separate three concerns that often get mixed together:

- where search actually executes
- how much orchestration lives in the local client
- what contract the rest of the agent sees

## Summary

| System | Search execution | Local orchestration | Main controls | Result contract |
| --- | --- | --- | --- | --- |
| Codex | OpenAI Responses API `web_search` tool in the main model request | Thin | cached/live mode, allowed domains, user location, context size, content types | upstream search actions and events |
| `aitool` Anthropic wrapper | Anthropic `web_search_20250305` server tool inside a secondary Claude call | Thick | allowed/blocked domains, provider gating, prompt rules, progress reporting | normalized local tool result plus progress events |
| Gemini CLI | Gemini `googleSearch` grounding tool inside a dedicated secondary Gemini call | Medium | query only, model alias selection, local citation formatting | synthesized grounded summary with inline citations and sources |
| This repo | Anthropic, OpenAI, or Gemini native search in a secondary call when supported, otherwise DuckDuckGo HTML scraping | Hybrid | allowed/blocked domains, max results, recency rewrite, backend fallback | stable local text format with sources and citation reminder |

## 1. Codex

### Core idea

Codex treats web search as a provider-native tool. The local client does not implement its own search engine or scraper. Instead, it serializes a top-level `web_search` tool into the OpenAI Responses API request and lets the provider handle the search workflow.

### Where it lives

- `~/src/codex/codex-rs/tools/src/tool_spec.rs`
- `~/src/codex/codex-rs/tools/src/tool_registry_plan.rs`
- `~/src/codex/codex-rs/core/tests/suite/web_search.rs`
- `~/src/codex/codex-rs/protocol/src/config_types.rs`
- `~/src/codex/codex-rs/app-server-protocol/src/protocol/thread_history.rs`
- `~/src/codex/codex-rs/app-server/README.md`

### How it works

- `ToolSpec::WebSearch` serializes to a Responses API tool with `type: "web_search"`.
- `create_web_search_tool(...)` maps `WebSearchMode::Cached` to `external_web_access: false` and `WebSearchMode::Live` to `external_web_access: true`.
- `build_tool_registry_plan(...)` includes the spec in the tool list sent upstream.
- The app and protocol layers treat search as a first-class response item with actions such as `search`, `open_page`, and `find_in_page`.

### What the local client controls

- `external_web_access` for cached vs live search
- `allowed_domains`
- `user_location`
- `search_context_size`
- `search_content_types`

### Strengths

- Very little local complexity
- Search stays inside the provider's native tool model
- Rich upstream action model, not just a flattened string
- Policy can influence defaults cleanly; Codex ties cached/live defaults to sandbox policy

### Weaknesses

- Strongly coupled to provider-native tool support
- Less local control over output formatting and citation enforcement
- If another provider exposes a different search contract, the client needs another integration path

## 2. Anthropic-Backed Wrapper in `aitool`

### Core idea

`aitool` exposes web search as a local tool, but that tool is really a wrapper around Anthropic's `web_search_20250305` server tool. The wrapper makes a second Claude call, forces web search in that call, parses the returned content blocks, and converts the result into the local tool contract.

This is still provider-native search, but the orchestration lives in the local tool layer rather than the main agent loop.

### Where it lives

- `~/src/aitool/src/tools/WebSearchTool/WebSearchTool.ts`
- `~/src/aitool/src/tools/WebSearchTool/prompt.ts`
- `~/src/aitool/src/tools/WebSearchTool/UI.tsx`

### How it works

- The local tool schema accepts `query`, `allowed_domains`, and `blocked_domains`.
- The tool checks provider/model support before enabling itself.
- On invocation, it creates a new Claude request and passes Anthropic's `web_search_20250305` schema through `extraToolSchemas`.
- It watches the streaming response for:
  - `server_tool_use`
  - `input_json_delta`
  - `web_search_tool_result`
  - normal text blocks
- It emits local progress updates as queries are resolved and results arrive.
- It converts the final result into a local output object and then into a `tool_result` string with an explicit citation reminder.

### What the local client controls

- `allowed_domains`
- `blocked_domains`
- `max_uses`
- enablement by provider/model
- local prompt instructions, including a mandatory `Sources:` section
- progress UI shown during the search

### Strengths

- Strong local control over UX and output shape
- Good streaming progress model
- Explicit citation enforcement
- Domain filtering and validation live in one place

### Weaknesses

- More local complexity than Codex
- Requires a second model call inside the tool
- Still provider-specific under the hood
- The wrapper duplicates some orchestration that the provider already understands

## 3. Gemini CLI

### Core idea

Gemini CLI exposes a local `google_web_search` tool to the agent, but the actual search runs through a dedicated Gemini utility-model call configured with Gemini's native `googleSearch` tool.

This is closer to the `aitool` pattern than to Codex: the provider supplies native search, but the local client still wraps it in a tool and reformats the result for the main agent loop.

### Where it lives

- `~/src/gemini-cli/packages/core/src/tools/web-search.ts`
- `~/src/gemini-cli/packages/core/src/config/defaultModelConfigs.ts`
- `~/src/gemini-cli/packages/core/src/config/config.ts`
- `~/src/gemini-cli/packages/core/src/tools/definitions/model-family-sets/gemini-3.ts`
- `~/src/gemini-cli/docs/tools/web-search.md`
- `~/src/gemini-cli/integration-tests/google_web_search.test.ts`

### How it works

- The agent sees a local tool named `google_web_search`.
- That tool accepts only one argument: `query`.
- On execution, the tool calls `geminiClient.generateContent(...)` using model alias `web-search`.
- The `web-search` alias is configured with `tools: [{ googleSearch: {} }]`, so the Gemini API performs grounded Google search inside that utility call.
- The tool reads Gemini's synthesized text answer plus `groundingMetadata`.
- It rewrites the answer locally to insert inline citation markers such as `[1]` using `groundingSupports`.
- It appends a `Sources:` section derived from `groundingChunks`.

### What the local client controls

- exposure of the local `google_web_search` tool
- the dedicated model alias used for search
- post-processing of grounding metadata into inline citations
- final formatting of the grounded answer

### Strengths

- Uses a provider-native search capability rather than scraping
- Keeps a simple local tool contract
- Good citation quality because Gemini returns grounding metadata directly
- Clean separation between broad search and `web_fetch` for deeper URL analysis

### Weaknesses

- No explicit domain filters in the local tool contract
- No fallback backend if Gemini-native search is unavailable
- The tool returns a synthesized grounded answer, not a raw list of search hits
- Still requires a secondary model call rather than a top-level tool in the main request

## 4. This Repo (`llm_agent`)

### Core idea

This repo currently uses a hybrid approach. `llm_agent.tools.web_search` presents one stable tool contract to the rest of the agent, but it can execute through two different backends:

1. Native Anthropic web search for supported Claude 4 models
2. Native OpenAI web search for OpenAI-hosted models
3. Native Gemini Google Search grounding for Gemini models
4. DuckDuckGo HTML scraping via `curl`, as a portable fallback

The tool layer owns query rewriting, domain filtering, output normalization, and final formatting.

### Where it lives

- `llm_agent/tools/web_search.py`
- `tests/tools/test_web_search.py`
- `llm_agent/session.py`
- `llm_agent/agents.py`

### How it works

- The public tool schema accepts:
  - `query`
  - `max_results`
  - `allowed_domains`
  - `blocked_domains`
- The tool rewrites clearly recency-sensitive queries by appending the current year when no year is already present.
- `session.py` and `agents.py` inject runtime context into the tool so it can see the active client and model.
- If the context looks like a supported Anthropic client and model, the tool makes a secondary Anthropic call using `web_search_20250305`.
- If the context looks like an OpenAI client and model, the tool makes a secondary OpenAI Responses API call using the native web-search tool.
- If the context looks like a Gemini client and model, the tool makes a secondary Gemini call using `googleSearch`.
- If native provider search is unavailable or fails, the tool falls back to a local DuckDuckGo HTML search implemented with `curl --data-urlencode`.
- Both backends produce the same `SearchResponse` structure before formatting.

### Native Anthropic path

- Uses the active client and model from runtime context
- Supplies `allowed_domains`, `blocked_domains`, and `user_location`
- Pulls results from `web_search_tool_result` blocks
- Also harvests cited URLs from returned text blocks
- Returns commentary and structured links to the local formatter

### Native OpenAI path

- Uses the active OpenAI client and model from runtime context
- Calls the Responses API with the native web-search tool
- Pulls URLs from `web_search_call` actions and URL citations from output text
- Normalizes OpenAI output into the same local result shape used by other backends

### Native Gemini path

- Uses the active Gemini client and model from runtime context
- Calls `generate_content(...)` with Gemini's native `googleSearch` tool
- Pulls sources from grounding metadata and injects inline citation markers into commentary
- Normalizes Gemini grounding output into the same local result shape used by other backends

### DuckDuckGo fallback path

- Sends a POST request to `https://html.duckduckgo.com/html/`
- URL-encodes the query with `--data-urlencode`
- Regex-parses result title links and snippets from the returned HTML
- Applies local allowed/blocked domain filters
- Returns the same normalized response shape used by the native path

### Output contract

The formatter returns a stable plain-text shape with:

- result count
- backend label
- executed query if the tool rewrote the original query
- optional domain-filter metadata
- optional commentary from the native backend
- numbered results
- a `Sources:` section with markdown links
- a JSON source block
- a final citation reminder

### Strengths

- One stable tool contract regardless of backend
- Works across providers because there is a non-Anthropic fallback
- Keeps local control over source formatting and citation reminders
- Supports both allowed and blocked domain filters

### Weaknesses

- More complex than a thin native integration
- The DuckDuckGo scraper is brittle by nature because it depends on HTML structure
- The Anthropic path is still a secondary model call, not a top-level provider tool in the main loop
- No live progress model today
- Native integration still depends on provider-specific adapters and response parsing

## Design Differences

### Where the complexity lives

- Codex puts complexity at the provider boundary and keeps the local client thin.
- `aitool` puts complexity in the local tool wrapper.
- Gemini CLI also uses a local wrapper, but it is narrower than `aitool` because it mostly reformats Gemini grounding output instead of managing multi-step provider tool events.
- This repo splits complexity between the tool wrapper and a scraper fallback.

### What the rest of the agent sees

- Codex sees provider-native web-search actions and events.
- `aitool` sees a local tool result synthesized from a secondary Claude call.
- Gemini CLI sees a local tool result synthesized from a secondary grounded Gemini call.
- This repo sees a local formatted text result, no matter which backend produced it.

### Fallback story

- Codex depends on native provider support.
- `aitool` depends on Anthropic support for the feature and supported models.
- Gemini CLI depends on Gemini-native grounding support.
- This repo can still search without native provider support because it falls back to DuckDuckGo scraping.

### Citation discipline

- Codex mostly relies on the provider and app rendering.
- `aitool` explicitly instructs and reminds the model to include sources.
- Gemini CLI derives citations from grounding metadata and injects them into the returned text.
- This repo also enforces citation behavior locally by always emitting a `Sources:` block and reminder text.

## Practical Takeaways

If the goal is the simplest architecture and the provider offers a stable first-class search tool, the Codex pattern is the cleanest.

If the goal is a tightly controlled local UX with progress reporting and citation rules, the `aitool` wrapper pattern is stronger.

If the goal is grounded summaries with provider-supplied citations and a minimal local tool contract, the Gemini CLI pattern is strong, but it gives up direct control over result-set structure.

If the goal is a single tool contract that keeps working even when provider-native search is unavailable, this repo's hybrid approach is the most resilient, but it also carries the most maintenance burden because it has to own a scraper fallback.

## Recommended Direction for This Repo

The current implementation is a reasonable transitional design:

- keep the shared local formatter and citation enforcement
- keep the fallback path for providers without native support
- prefer provider-native search when a provider exposes a stable top-level tool interface

In practice, that suggests moving closer to the Codex model for providers that support native web search in the main request, while retaining this repo's local formatting and DuckDuckGo fallback as compatibility layers.
