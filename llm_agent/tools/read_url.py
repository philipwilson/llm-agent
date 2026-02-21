"""read_url tool: fetch a web page and return plain text."""

import re
import subprocess

from llm_agent.formatting import bold, cyan

SCHEMA = {
    "name": "read_url",
    "description": (
        "Fetch a web page and return its text content (HTML converted to "
        "plain text). Returns the page title and final URL after redirects. "
        "Prefer this over curl via run_command."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch (must be http:// or https://).",
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters of content to return (default: 10000).",
            },
        },
        "required": ["url"],
    },
}

WEB_TIMEOUT = 15
MAX_DOWNLOAD_BYTES = 1_000_000  # 1MB


def log(params):
    print(f"  {bold('read_url')}: {cyan(params.get('url', ''))}")

LOG = log


def handle(params):
    url = params.get("url", "")
    max_length = params.get("max_length", 10000)

    # Only allow http/https
    if not url.startswith(("http://", "https://")):
        return "(error: only http:// and https:// URLs are supported)"

    # Fetch with curl
    try:
        fetch = subprocess.run(
            [
                "curl", "-sL",
                "--max-filesize", str(MAX_DOWNLOAD_BYTES),
                "--max-time", str(WEB_TIMEOUT),
                "-H", "User-Agent: llm-agent/1.0",
                "-w", "\n__STATUS__:%{http_code}\n__URL__:%{url_effective}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=WEB_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        return f"(fetch timed out after {WEB_TIMEOUT}s)"
    except FileNotFoundError:
        return "(error: curl not found)"
    except Exception as e:
        return f"(error fetching URL: {e})"

    if fetch.returncode != 0:
        return f"(error: curl returned exit code {fetch.returncode}: {fetch.stderr.strip()})"

    # Parse status and final URL from curl -w output
    output = fetch.stdout
    status_code = ""
    final_url = url
    for line in output.splitlines()[-5:]:
        if line.startswith("__STATUS__:"):
            status_code = line.split(":", 1)[1]
        elif line.startswith("__URL__:"):
            final_url = line.split(":", 1)[1]

    if status_code and not status_code.startswith(("2", "3")):
        return f"(HTTP {status_code} error fetching {url})"

    # Strip the __STATUS__ and __URL__ lines from the content
    content_lines = []
    for line in output.splitlines():
        if line.startswith(("__STATUS__:", "__URL__:")):
            continue
        content_lines.append(line)
    html = "\n".join(content_lines)

    # Convert HTML to plain text using lynx, w3m, or fallback to basic stripping
    text = None
    for converter in [
        ["lynx", "-stdin", "-dump", "-nolist", "-width=120"],
        ["w3m", "-T", "text/html", "-dump", "-cols", "120"],
    ]:
        try:
            conv = subprocess.run(
                converter,
                input=html,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if conv.returncode == 0 and conv.stdout.strip():
                text = conv.stdout
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    if text is None:
        # Basic fallback: strip HTML tags with a regex
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n", "\n\n", text)
        text = text.strip()

    # Extract title from HTML
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else "(no title)"

    # Truncate
    total_len = len(text)
    if total_len > max_length:
        text = text[:max_length] + f"\n\n... (truncated, {total_len} total characters)"

    header = f"[{title}]"
    if final_url != url:
        header += f"\n[redirected to: {final_url}]"
    header += f"\n[{total_len} characters]"

    return header + "\n\n" + text
