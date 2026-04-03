"""web_search tool: search the web with provider-native backends when available."""

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from urllib.parse import parse_qs, urlparse

from llm_agent.formatting import bold

SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web using provider-native search when available, with a "
        "DuckDuckGo fallback. Returns result titles, URLs, and snippets. Use "
        "this for general web searches; use read_url to fetch a specific page."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results to return (default: 8).",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of domains to include. Matches the listed domains and their subdomains.",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of domains to exclude. Matches the listed domains and their subdomains.",
            },
        },
        "required": ["query"],
    },
}

SEARCH_TIMEOUT = 15
DEFAULT_MAX_RESULTS = 8
NATIVE_SEARCH_MAX_USES = 4
NATIVE_SEARCH_MAX_TOKENS = 2048
OPENAI_SEARCH_CONTEXT_SIZE = "high"
OPENAI_INCLUDE_FIELDS = ["web_search_call.action.sources"]
RECENCY_SIGNAL_RE = re.compile(
    r"\b(latest|current|today|recent|newest|news|docs|documentation|release notes?)\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
NATIVE_SEARCH_SYSTEM_PROMPT = (
    "You are a web search assistant. Search for the user's query and return 2-4 "
    "concise bullet points summarizing the most relevant findings. Prefer "
    "authoritative sources and make sure the result includes source links."
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    page_age: str | None = None


@dataclass
class SearchResponse:
    query: str
    executed_query: str
    results: list[SearchResult]
    duration_seconds: float
    backend: str
    allowed_domains: list[str]
    blocked_domains: list[str]
    commentary: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def log(params):
    from llm_agent.display import get_display

    get_display().tool_log(f"  {bold('web_search')}: \"{params.get('query', '')}\"")


LOG = log


def _get_value(obj, *names, default=None):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def _normalize_domain(domain):
    """Normalize a user-supplied domain or URL to a bare lowercase hostname."""
    if not isinstance(domain, str):
        return ""

    value = domain.strip().lower()
    if not value:
        return ""

    if "://" in value:
        hostname = urlparse(value).hostname or ""
    else:
        # Handle values like example.com/path or example.com:8443
        hostname = urlparse("https://" + value).hostname or value

    hostname = hostname.strip(".")
    if hostname.startswith("*."):
        hostname = hostname[2:]
    return hostname


def _normalize_domain_list(raw_domains, field_name):
    """Validate and normalize a domain filter list."""
    if raw_domains is None:
        return [], None
    if not isinstance(raw_domains, list):
        return None, f"{field_name} must be a list of domains"

    normalized = []
    seen = set()
    for item in raw_domains:
        if not isinstance(item, str):
            return None, f"{field_name} entries must be strings"
        domain = _normalize_domain(item)
        if not domain:
            return None, f"{field_name} entries must not be empty"
        if domain not in seen:
            normalized.append(domain)
            seen.add(domain)

    return normalized, None


def _matches_domain(hostname, domain):
    if not hostname or not domain:
        return False
    return hostname == domain or hostname.endswith("." + domain)


def _passes_domain_filters(url, allowed_domains, blocked_domains):
    hostname = (urlparse(url).hostname or "").lower()
    if allowed_domains and not any(_matches_domain(hostname, domain) for domain in allowed_domains):
        return False
    if blocked_domains and any(_matches_domain(hostname, domain) for domain in blocked_domains):
        return False
    return True


def _extract_result_url(raw_url):
    parsed = urlparse(raw_url)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    return raw_url


def _format_duration(duration_seconds):
    if duration_seconds >= 1:
        return f"{duration_seconds:.1f}s"
    return f"{round(duration_seconds * 1000)}ms"


def _current_year():
    return datetime.now().year


def _prepare_search_query(query):
    """Optionally expand recency-sensitive queries with the current year."""
    if YEAR_RE.search(query):
        return query
    if not RECENCY_SIGNAL_RE.search(query):
        return query
    return f"{query} {_current_year()}"


def _provider_for_model(model):
    if not isinstance(model, str):
        return "unknown"
    if model.startswith("ollama:"):
        return "ollama"
    if model.startswith("gemini-"):
        return "gemini"
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gpt-") or model.startswith("o"):
        return "openai"
    return "unknown"


def _backend_label(backend):
    labels = {
        "anthropic_native": "Anthropic web search",
        "openai_native": "OpenAI web search",
        "gemini_native": "Gemini Google Search",
        "duckduckgo_html": "DuckDuckGo",
    }
    return labels.get(backend, backend)


def _supports_native_anthropic_web_search(context):
    if not isinstance(context, dict):
        return False
    if context.get("provider") != "anthropic":
        return False

    client = context.get("client")
    model = context.get("model", "")
    if not client or not isinstance(model, str):
        return False

    if not re.match(r"^claude-(opus|sonnet|haiku)-4", model):
        return False

    messages_api = getattr(client, "messages", None)
    return messages_api is not None and hasattr(messages_api, "create")


def _supports_native_openai_web_search(context):
    if not isinstance(context, dict):
        return False
    if context.get("provider") != "openai":
        return False

    client = context.get("client")
    responses_api = getattr(client, "responses", None)
    return responses_api is not None and hasattr(responses_api, "create")


def _supports_native_gemini_web_search(context):
    if not isinstance(context, dict):
        return False
    if context.get("provider") != "gemini":
        return False

    client = context.get("client")
    models_api = getattr(client, "models", None)
    return models_api is not None and hasattr(models_api, "generate_content")


def build_context(client, model):
    """Build runtime context used to select the native search backend."""
    provider = _provider_for_model(model)
    context = {
        "client": client,
        "model": model,
        "provider": provider,
    }
    if provider in {"anthropic", "openai"}:
        context["user_location"] = {
            "type": "approximate",
            "country": "US",
        }
    return context


def _append_search_result(results, url_to_index, title, url, snippet="", page_age=None):
    if not title or not url:
        return None

    if url in url_to_index:
        index = url_to_index[url]
        existing = results[index]
        if existing.title == existing.url and title:
            existing.title = title
        if not existing.snippet and snippet:
            existing.snippet = snippet
        if not existing.page_age and page_age:
            existing.page_age = page_age
        return index + 1

    index = len(results)
    url_to_index[url] = index
    results.append(
        SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            domain=(urlparse(url).hostname or "").lower(),
            page_age=page_age,
        )
    )
    return index + 1


def _apply_domain_filters_to_results(results, allowed_domains, blocked_domains):
    filtered = []
    removed = 0
    for result in results:
        if _passes_domain_filters(result.url, allowed_domains, blocked_domains):
            filtered.append(result)
        else:
            removed += 1
    return filtered, removed


def _finalize_search_response(
    response,
    max_results,
    *,
    supports_allowed_domains=True,
    supports_blocked_domains=True,
):
    filtered_results, removed = _apply_domain_filters_to_results(
        response.results,
        response.allowed_domains,
        response.blocked_domains,
    )

    if response.allowed_domains and not supports_allowed_domains:
        response.notes.append(
            "Allowed-domain filtering was applied locally after the native search response was returned."
        )
        response.commentary = []
    if response.blocked_domains and not supports_blocked_domains:
        response.notes.append(
            "Blocked-domain filtering was applied locally after the native search response was returned."
        )
        response.commentary = []
    if removed and not response.notes:
        response.notes.append("Some results were removed by local domain filtering.")

    response.results = filtered_results[:max_results]
    return response


def _insert_text_markers(text, insertions, *, utf8_offsets=False):
    if not text or not insertions:
        return text

    insertions = sorted(insertions, key=lambda item: item[0], reverse=True)
    if utf8_offsets:
        encoded = text.encode("utf-8")
        chunks = []
        last = len(encoded)
        for index, marker in insertions:
            position = max(0, min(index, last))
            chunks.insert(0, encoded[position:last])
            chunks.insert(0, marker.encode("utf-8"))
            last = position
        chunks.insert(0, encoded[0:last])
        return b"".join(chunks).decode("utf-8")

    updated = text
    for index, marker in insertions:
        position = max(0, min(index, len(updated)))
        updated = updated[:position] + marker + updated[position:]
    return updated


def _openai_tool_types(model, allowed_domains):
    if allowed_domains:
        return ["web_search"]
    if isinstance(model, str) and model.startswith("gpt-4o"):
        return ["web_search_preview", "web_search"]
    return ["web_search", "web_search_preview"]


def _search_native_anthropic(query, max_results, allowed_domains=None, blocked_domains=None, context=None):
    start = time.monotonic()
    client = (context or {}).get("client")
    model = (context or {}).get("model")
    user_location = (context or {}).get("user_location")

    tool_schema = {
        "name": "web_search",
        "type": "web_search_20250305",
        "max_uses": NATIVE_SEARCH_MAX_USES,
    }
    if allowed_domains:
        tool_schema["allowed_domains"] = allowed_domains
    if blocked_domains:
        tool_schema["blocked_domains"] = blocked_domains
    if user_location:
        tool_schema["user_location"] = user_location

    response = client.messages.create(
        model=model,
        max_tokens=NATIVE_SEARCH_MAX_TOKENS,
        system=NATIVE_SEARCH_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Perform a web search for this query and summarize the results: {query}",
        }],
        tools=[tool_schema],
        tool_choice={
            "type": "tool",
            "name": "web_search",
            "disable_parallel_tool_use": True,
        },
    )

    commentary = []
    results = []
    url_to_index = {}

    for block in response.content:
        if block.type == "web_search_tool_result":
            if not isinstance(block.content, list):
                error_code = getattr(block.content, "error_code", "unknown_error")
                return None, f"(native web search error: {error_code})"
            for entry in block.content:
                _append_search_result(
                    results,
                    url_to_index,
                    entry.title,
                    entry.url,
                    page_age=getattr(entry, "page_age", None),
                )
        elif block.type == "text":
            text = block.text.strip()
            if text:
                commentary.append(text)
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", None)
                if not url:
                    continue
                _append_search_result(
                    results,
                    url_to_index,
                    getattr(citation, "title", None) or url,
                    url,
                )

    response = SearchResponse(
        query=query,
        executed_query=query,
        results=results,
        duration_seconds=time.monotonic() - start,
        backend="anthropic_native",
        allowed_domains=allowed_domains or [],
        blocked_domains=blocked_domains or [],
        commentary=commentary,
    )
    return _finalize_search_response(
        response,
        max_results,
        supports_allowed_domains=True,
        supports_blocked_domains=True,
    ), None


def _search_native_openai(query, max_results, allowed_domains=None, blocked_domains=None, context=None):
    start = time.monotonic()
    client = (context or {}).get("client")
    model = (context or {}).get("model")
    user_location = (context or {}).get("user_location")

    response = None
    last_error = None
    for tool_type in _openai_tool_types(model, allowed_domains):
        tool_schema = {
            "type": tool_type,
            "search_context_size": OPENAI_SEARCH_CONTEXT_SIZE,
        }
        if user_location:
            tool_schema["user_location"] = user_location
        if tool_type == "web_search" and allowed_domains:
            tool_schema["filters"] = {"allowed_domains": allowed_domains}

        try:
            response = client.responses.create(
                model=model,
                input=query,
                instructions=NATIVE_SEARCH_SYSTEM_PROMPT,
                max_output_tokens=NATIVE_SEARCH_MAX_TOKENS,
                max_tool_calls=1,
                parallel_tool_calls=False,
                include=OPENAI_INCLUDE_FIELDS,
                tool_choice="required",
                tools=[tool_schema],
            )
            break
        except Exception as exc:
            last_error = exc
            response = None

    if response is None:
        return None, f"(native web search error: {last_error})"

    response_error = _get_value(response, "error")
    if response_error:
        message = _get_value(response_error, "message", default=str(response_error))
        return None, f"(native web search error: {message})"

    executed_query = query
    commentary = []
    results = []
    url_to_index = {}

    for item in _get_value(response, "output", default=[]) or []:
        item_type = _get_value(item, "type")
        if item_type == "web_search_call":
            action = _get_value(item, "action")
            action_type = _get_value(action, "type")
            if action_type == "search":
                action_query = _get_value(action, "query")
                if action_query:
                    executed_query = action_query
                queries = _get_value(action, "queries", default=[]) or []
                if queries:
                    executed_query = queries[0]
                for source in _get_value(action, "sources", default=[]) or []:
                    url = _get_value(source, "url")
                    if url:
                        _append_search_result(results, url_to_index, url, url)
            continue

        if item_type != "message":
            continue

        for content in _get_value(item, "content", default=[]) or []:
            if _get_value(content, "type") != "output_text":
                continue
            text = _get_value(content, "text", default="") or ""
            insertions = []
            for annotation in _get_value(content, "annotations", default=[]) or []:
                if _get_value(annotation, "type") != "url_citation":
                    continue
                url = _get_value(annotation, "url")
                if not url:
                    continue
                number = _append_search_result(
                    results,
                    url_to_index,
                    _get_value(annotation, "title", default=url),
                    url,
                )
                end_index = _get_value(annotation, "end_index", default=None)
                if number and end_index is not None:
                    insertions.append((end_index, f"[{number}]"))
            text = _insert_text_markers(text, insertions)
            text = text.strip()
            if text:
                commentary.append(text)

    response = SearchResponse(
        query=query,
        executed_query=executed_query,
        results=results,
        duration_seconds=time.monotonic() - start,
        backend="openai_native",
        allowed_domains=allowed_domains or [],
        blocked_domains=blocked_domains or [],
        commentary=commentary,
    )
    return _finalize_search_response(
        response,
        max_results,
        supports_allowed_domains=True,
        supports_blocked_domains=False,
    ), None


def _search_native_gemini(query, max_results, allowed_domains=None, blocked_domains=None, context=None):
    try:
        from google.genai import types
    except ImportError as exc:
        return None, f"(native web search error: {exc})"

    start = time.monotonic()
    client = (context or {}).get("client")
    model = (context or {}).get("model")

    google_search = (
        types.GoogleSearch(excludeDomains=blocked_domains)
        if blocked_domains
        else types.GoogleSearch()
    )
    config = types.GenerateContentConfig(
        systemInstruction=NATIVE_SEARCH_SYSTEM_PROMPT,
        maxOutputTokens=NATIVE_SEARCH_MAX_TOKENS,
        tools=[types.Tool(googleSearch=google_search)],
    )

    response = client.models.generate_content(
        model=model,
        contents=query,
        config=config,
    )

    candidates = _get_value(response, "candidates", default=[]) or []
    candidate = candidates[0] if candidates else None
    grounding = _get_value(candidate, "grounding_metadata", "groundingMetadata")
    content = _get_value(candidate, "content")

    raw_text_parts = []
    for part in _get_value(content, "parts", default=[]) or []:
        part_text = _get_value(part, "text", default="") or ""
        if part_text:
            raw_text_parts.append(part_text)
    commentary_text = "".join(raw_text_parts)

    executed_query = query
    queries = _get_value(grounding, "web_search_queries", "webSearchQueries", default=[]) or []
    if queries:
        executed_query = queries[0]

    results = []
    url_to_index = {}
    chunk_result_numbers = {}
    for chunk_index, chunk in enumerate(
        _get_value(grounding, "grounding_chunks", "groundingChunks", default=[]) or []
    ):
        web = _get_value(chunk, "web")
        url = _get_value(web, "uri")
        title = _get_value(web, "title", default=url)
        if not url:
            continue
        chunk_result_numbers[chunk_index] = _append_search_result(
            results,
            url_to_index,
            title,
            url,
        )

    insertions = []
    for support in _get_value(grounding, "grounding_supports", "groundingSupports", default=[]) or []:
        segment = _get_value(support, "segment")
        end_index = _get_value(segment, "end_index", "endIndex", default=None)
        chunk_indices = _get_value(
            support,
            "grounding_chunk_indices",
            "groundingChunkIndices",
            default=[],
        ) or []
        if end_index is None or not chunk_indices:
            continue
        numbers = [chunk_result_numbers.get(index) for index in chunk_indices]
        numbers = [number for number in numbers if number]
        if not numbers:
            continue
        marker = "".join(f"[{number}]" for number in numbers)
        insertions.append((end_index, marker))

    commentary_text = _insert_text_markers(
        commentary_text,
        insertions,
        utf8_offsets=True,
    ).strip()
    commentary = [commentary_text] if commentary_text else []

    response = SearchResponse(
        query=query,
        executed_query=executed_query,
        results=results,
        duration_seconds=time.monotonic() - start,
        backend="gemini_native",
        allowed_domains=allowed_domains or [],
        blocked_domains=blocked_domains or [],
        commentary=commentary,
    )
    return _finalize_search_response(
        response,
        max_results,
        supports_allowed_domains=False,
        supports_blocked_domains=True,
    ), None


def _search_duckduckgo(query, max_results, allowed_domains=None, blocked_domains=None):
    start = time.monotonic()

    try:
        result = subprocess.run(
            [
                "curl", "-sL",
                "--max-time", str(SEARCH_TIMEOUT),
                "-H", "User-Agent: llm-agent/1.0",
                "--data-urlencode", f"q={query}",
                "https://html.duckduckgo.com/html/",
            ],
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return None, f"(search timed out after {SEARCH_TIMEOUT}s)"
    except FileNotFoundError:
        return None, "(error: curl not found)"
    except Exception as exc:
        return None, f"(error performing search: {exc})"

    if result.returncode != 0:
        return None, f"(error: curl returned exit code {result.returncode})"

    html = result.stdout
    entries = []
    url_to_index = {}

    result_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for match in result_pattern.finditer(html):
        url = _extract_result_url(match.group(1))
        title = _strip_html(match.group(2))
        snippet = _strip_html(match.group(3))

        if not title or not url:
            continue
        if not _passes_domain_filters(url, allowed_domains, blocked_domains):
            continue

        _append_search_result(entries, url_to_index, title, url, snippet=snippet)
        if len(entries) >= max_results:
            break

    response = SearchResponse(
        query=query,
        executed_query=query,
        results=entries,
        duration_seconds=time.monotonic() - start,
        backend="duckduckgo_html",
        allowed_domains=allowed_domains or [],
        blocked_domains=blocked_domains or [],
    )
    return response, None


def _search_native_backend(query, max_results, allowed_domains=None, blocked_domains=None, context=None):
    backends = [
        (_supports_native_anthropic_web_search, _search_native_anthropic),
        (_supports_native_openai_web_search, _search_native_openai),
        (_supports_native_gemini_web_search, _search_native_gemini),
    ]
    for supports_backend, search_backend in backends:
        if not supports_backend(context):
            continue
        try:
            return search_backend(
                query,
                max_results,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                context=context,
            )
        except Exception:
            return None, None
    return None, None


def _format_search_response(response):
    duration = _format_duration(response.duration_seconds)
    lines = [
        f"[{len(response.results)} results for \"{response.query}\" via {_backend_label(response.backend)} in {duration}]",
    ]
    if response.executed_query != response.query:
        lines.append(f"[executed query: {response.executed_query}]")

    if response.allowed_domains:
        lines.append(f"[allowed domains: {', '.join(response.allowed_domains)}]")
    if response.blocked_domains:
        lines.append(f"[blocked domains: {', '.join(response.blocked_domains)}]")
    for note in response.notes:
        lines.append(f"[note: {note}]")

    lines.append("")

    if response.commentary:
        lines.extend(response.commentary)
        lines.append("")

    if not response.results:
        lines.append(f"(no results found for: {response.query})")
        return "\n".join(lines)

    for i, result in enumerate(response.results, 1):
        lines.append(f"{i}. {result.title}")
        lines.append(f"   URL: {result.url}")
        if result.domain:
            lines.append(f"   Domain: {result.domain}")
        if result.page_age:
            lines.append(f"   Page age: {result.page_age}")
        if result.snippet:
            lines.append(f"   Snippet: {result.snippet}")
        lines.append("")

    source_links = [{"title": result.title, "url": result.url} for result in response.results]
    lines.append("Sources:")
    for result in response.results:
        lines.append(f"- [{result.title}]({result.url})")
    lines.append("")
    lines.append("Source links (JSON):")
    lines.append(json.dumps(source_links, ensure_ascii=True))
    lines.append("")
    lines.append(
        "REMINDER: Cite relevant links from the Sources section in your final answer using markdown hyperlinks."
    )

    return "\n".join(lines)


def handle(params, context=None):
    query = params.get("query", "")
    if isinstance(query, str):
        query = query.strip()
    max_results = params.get("max_results", DEFAULT_MAX_RESULTS)
    allowed_domains, allowed_error = _normalize_domain_list(
        params.get("allowed_domains"), "allowed_domains"
    )
    blocked_domains, blocked_error = _normalize_domain_list(
        params.get("blocked_domains"), "blocked_domains"
    )

    if not query:
        return "(error: query is required)"
    if allowed_error:
        return f"(error: {allowed_error})"
    if blocked_error:
        return f"(error: {blocked_error})"
    if allowed_domains and blocked_domains:
        return "(error: cannot specify both allowed_domains and blocked_domains)"

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        return "(error: max_results must be an integer)"
    if max_results < 1:
        return "(error: max_results must be >= 1)"

    executed_query = _prepare_search_query(query)

    response, error = _search_native_backend(
        executed_query,
        max_results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        context=context,
    )
    if response is None:
        response, error = _search_duckduckgo(
            executed_query,
            max_results,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
        )
    if error:
        return error

    response.query = query
    return _format_search_response(response)
