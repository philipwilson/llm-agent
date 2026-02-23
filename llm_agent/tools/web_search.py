"""web_search tool: search the web via DuckDuckGo."""

import re
import subprocess
from html import unescape

from llm_agent.formatting import bold

SCHEMA = {
    "name": "web_search",
    "description": (
        "Search the web using DuckDuckGo and return result titles, URLs, and "
        "snippets. Use this for general web searches; use read_url to fetch a "
        "specific page."
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
        },
        "required": ["query"],
    },
}

SEARCH_TIMEOUT = 15


def log(params):
    from llm_agent.display import get_display
    get_display().tool_log(f"  {bold('web_search')}: \"{params.get('query', '')}\"")

LOG = log


def _strip_html(text):
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<[^>]+>", "", text)
    return unescape(text).strip()


def handle(params):
    query = params.get("query", "")
    max_results = params.get("max_results", 8)

    if not query:
        return "(error: query is required)"

    # Fetch DuckDuckGo HTML search results via curl POST
    try:
        result = subprocess.run(
            [
                "curl", "-sL",
                "--max-time", str(SEARCH_TIMEOUT),
                "-H", "User-Agent: llm-agent/1.0",
                "-d", f"q={query}",
                "https://html.duckduckgo.com/html/",
            ],
            capture_output=True,
            text=True,
            timeout=SEARCH_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return f"(search timed out after {SEARCH_TIMEOUT}s)"
    except FileNotFoundError:
        return "(error: curl not found)"
    except Exception as e:
        return f"(error performing search: {e})"

    if result.returncode != 0:
        return f"(error: curl returned exit code {result.returncode})"

    html = result.stdout

    # Parse results from DuckDuckGo HTML
    # Each result is in a <div class="result..."> block with:
    #   <a class="result__a" href="...">title</a>
    #   <a class="result__snippet">snippet text</a>
    entries = []

    # Extract result blocks
    result_pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for match in result_pattern.finditer(html):
        url = match.group(1)
        title = _strip_html(match.group(2))
        snippet = _strip_html(match.group(3))

        # DuckDuckGo wraps URLs in a redirect; extract the actual URL
        uddg_match = re.search(r'uddg=([^&]+)', url)
        if uddg_match:
            from urllib.parse import unquote
            url = unquote(uddg_match.group(1))

        if title and url:
            entries.append((title, url, snippet))

        if len(entries) >= max_results:
            break

    if not entries:
        return f"(no results found for: {query})"

    lines = [f"[{len(entries)} results for \"{query}\"]", ""]
    for i, (title, url, snippet) in enumerate(entries, 1):
        lines.append(f"{i}. {title}")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    return "\n".join(lines).rstrip()
