"""read_url tool: fetch a URL and return cleaned markdown or text."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

from llm_agent.formatting import bold, cyan

SCHEMA = {
    "name": "read_url",
    "description": (
        "Fetch a URL and return cleaned content. HTML is converted to markdown; "
        "plain text, markdown, and JSON are returned as text. Returns the page "
        "title, final URL after safe redirects, content type, and truncated "
        "content. Prefer this over curl via run_command."
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
MAX_REDIRECTS = 10
CACHE_TTL_SECONDS = 15 * 60
CACHE_MAX_ENTRIES = 32
USER_AGENT = "llm-agent/1.0"


@dataclass(frozen=True)
class FetchResponse:
    original_url: str
    final_url: str
    status_code: int
    reason: str
    content_type: str
    charset: str
    body: bytes


@dataclass(frozen=True)
class PageContent:
    original_url: str
    final_url: str
    title: str
    content_type: str
    render_format: str
    byte_count: int
    content: str


@dataclass(frozen=True)
class RedirectNotice:
    original_url: str
    redirect_url: str
    status_code: int
    reason: str


@dataclass
class _CacheEntry:
    value: PageContent | RedirectNotice
    expires_at: float


_READ_URL_CACHE: OrderedDict[str, _CacheEntry] = OrderedDict()


def log(params):
    from llm_agent.display import get_display

    get_display().tool_log(f"  {bold('read_url')}: {cyan(params.get('url', ''))}")


LOG = log


class _CrossHostRedirect(Exception):
    def __init__(self, original_url, redirect_url, status_code, reason):
        super().__init__(reason)
        self.original_url = original_url
        self.redirect_url = redirect_url
        self.status_code = status_code
        self.reason = reason


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url = urllib.parse.urljoin(req.full_url, newurl)
        if not _is_permitted_redirect(req.full_url, redirect_url):
            raise _CrossHostRedirect(req.full_url, redirect_url, code, msg)
        return super().redirect_request(req, fp, code, msg, headers, redirect_url)


class _MarkdownConverter(HTMLParser):
    SKIP_TAGS = {"head", "script", "style", "noscript", "svg"}
    BLOCK_TAGS = {
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "header",
        "main",
        "nav",
        "p",
        "section",
        "table",
        "tr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.link_stack = []
        self.list_stack = []
        self.skip_depth = 0
        self.in_pre = False
        self.blockquote_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        attr_map = dict(attrs)

        if tag in self.BLOCK_TAGS:
            self._ensure_separator(2)
            if tag == "blockquote":
                self.blockquote_depth += 1
                self._append_to_current("> " * self.blockquote_depth)
            return

        if tag == "br":
            self._append_to_current("\n" if self.in_pre else "  \n")
            return

        if tag == "hr":
            self._ensure_separator(2)
            self._append_to_current("---\n\n")
            return

        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_separator(2)
            self._append_to_current("#" * int(tag[1]) + " ")
            return

        if tag == "ul":
            self.list_stack.append({"ordered": False, "index": 0})
            self._ensure_separator(1)
            return

        if tag == "ol":
            self.list_stack.append({"ordered": True, "index": 0})
            self._ensure_separator(1)
            return

        if tag == "li":
            self._ensure_separator(1)
            depth = max(0, len(self.list_stack) - 1)
            indent = "  " * depth
            if self.list_stack and self.list_stack[-1]["ordered"]:
                self.list_stack[-1]["index"] += 1
                prefix = f"{self.list_stack[-1]['index']}. "
            else:
                prefix = "- "
            self._append_to_current(indent + prefix)
            return

        if tag == "pre":
            self._ensure_separator(2)
            self._append_to_current("```\n")
            self.in_pre = True
            return

        if tag == "code" and not self.in_pre:
            self._append_to_current("`")
            return

        if tag in {"strong", "b"}:
            self._append_to_current("**")
            return

        if tag in {"em", "i"}:
            self._append_to_current("*")
            return

        if tag == "a":
            self.link_stack.append({"href": attr_map.get("href", "").strip(), "parts": []})
            return

        if tag == "img":
            alt = (attr_map.get("alt") or "").strip()
            if alt:
                self._append_inline_text(f"[Image: {alt}]")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return

        if tag == "blockquote":
            self.blockquote_depth = max(0, self.blockquote_depth - 1)
            self._ensure_separator(2)
            return

        if tag in self.BLOCK_TAGS or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._ensure_separator(2)
            return

        if tag == "li":
            self._ensure_separator(1)
            return

        if tag in {"ul", "ol"}:
            if self.list_stack:
                self.list_stack.pop()
            self._ensure_separator(1)
            return

        if tag == "pre":
            if not self._endswith("\n"):
                self._append_to_current("\n")
            self._append_to_current("```\n\n")
            self.in_pre = False
            return

        if tag == "code" and not self.in_pre:
            self._append_to_current("`")
            return

        if tag in {"strong", "b"}:
            self._append_to_current("**")
            return

        if tag in {"em", "i"}:
            self._append_to_current("*")
            return

        if tag == "a" and self.link_stack:
            link = self.link_stack.pop()
            text = "".join(link["parts"]).strip() or link["href"]
            rendered = f"[{text}]({link['href']})" if link["href"] else text
            self._append_to_current(rendered)

    def handle_data(self, data):
        if self.skip_depth or not data:
            return
        if self.in_pre:
            self._append_to_current(data)
            return
        self._append_inline_text(data)

    def get_markdown(self):
        markdown = "".join(self.parts)
        markdown = markdown.replace("\xa0", " ")
        markdown = re.sub(r"[ \t]+\n", "\n", markdown)
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        return markdown.strip()

    def _append_to_current(self, text):
        if not text:
            return
        if self.link_stack:
            self.link_stack[-1]["parts"].append(text)
        else:
            self.parts.append(text)

    def _append_inline_text(self, text):
        text = re.sub(r"\s+", " ", text)
        if not text.strip():
            return
        stripped = text.strip()
        tail = self._tail_text()
        if tail and not tail.endswith(("\n", " ", "(", "[", "`", "*")) and not stripped.startswith(
            (".", ",", ":", ";", "!", "?", ")", "]")
        ):
            self._append_to_current(" ")
        self._append_to_current(stripped)

    def _ensure_separator(self, newline_count):
        if self.link_stack:
            return
        if not self.parts:
            return
        existing = 0
        index = len(self.parts) - 1
        while index >= 0:
            part = self.parts[index]
            if not part:
                index -= 1
                continue
            match = re.search(r"\n*$", part)
            existing = len(match.group(0))
            break
        if existing < newline_count:
            self.parts.append("\n" * (newline_count - existing))

    def _tail_text(self):
        target = self.link_stack[-1]["parts"] if self.link_stack else self.parts
        for part in reversed(target):
            if part:
                return part
        return ""

    def _endswith(self, suffix):
        return self._tail_text().endswith(suffix)


def handle(params):
    url = params.get("url", "")
    max_length = params.get("max_length", 10000)

    validation_error = _validate_url(url)
    if validation_error:
        return validation_error

    try:
        page = _get_page(url)
    except urllib.error.HTTPError as exc:
        return f"(HTTP {exc.code} error fetching {url})"
    except urllib.error.URLError as exc:
        return f"(error fetching URL: {exc.reason})"
    except TimeoutError:
        return f"(fetch timed out after {WEB_TIMEOUT}s)"
    except FileNotFoundError:
        return "(error: URL fetcher unavailable)"
    except Exception as exc:
        return f"(error fetching URL: {exc})"

    if isinstance(page, RedirectNotice):
        return _format_redirect_notice(page)
    return _format_page(page, max_length)


def _validate_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return "(error: invalid URL)"

    if parsed.scheme not in ("http", "https"):
        return "(error: only http:// and https:// URLs are supported)"
    if not parsed.hostname:
        return "(error: URL must include a hostname)"
    if parsed.username or parsed.password:
        return "(error: URLs with embedded credentials are not supported)"
    return None


def _get_page(url):
    now = time.monotonic()
    cached = _READ_URL_CACHE.get(url)
    if cached and cached.expires_at > now:
        _READ_URL_CACHE.move_to_end(url)
        return cached.value
    if cached:
        _READ_URL_CACHE.pop(url, None)

    page = _load_page_uncached(url)
    _READ_URL_CACHE[url] = _CacheEntry(value=page, expires_at=now + CACHE_TTL_SECONDS)
    while len(_READ_URL_CACHE) > CACHE_MAX_ENTRIES:
        _READ_URL_CACHE.popitem(last=False)
    return page


def _load_page_uncached(url):
    response = _fetch_url(url)
    if isinstance(response, RedirectNotice):
        return response

    title, render_format, content = _render_response_body(response)
    return PageContent(
        original_url=response.original_url,
        final_url=response.final_url,
        title=title,
        content_type=response.content_type or "unknown",
        render_format=render_format,
        byte_count=len(response.body),
        content=content or "(no content)",
    )


def _fetch_url(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html, text/markdown, text/plain, application/json;q=0.9, */*;q=0.5",
        },
    )
    opener = urllib.request.build_opener(_SafeRedirectHandler)

    try:
        with opener.open(request, timeout=WEB_TIMEOUT) as response:
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
            if len(body) > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"response exceeded {MAX_DOWNLOAD_BYTES} bytes")

            content_type = response.headers.get_content_type() or "application/octet-stream"
            charset = response.headers.get_content_charset() or "utf-8"
            return FetchResponse(
                original_url=url,
                final_url=response.geturl(),
                status_code=getattr(response, "status", 200),
                reason=getattr(response, "reason", "OK"),
                content_type=content_type.lower(),
                charset=charset,
                body=body,
            )
    except _CrossHostRedirect as exc:
        return RedirectNotice(
            original_url=exc.original_url,
            redirect_url=exc.redirect_url,
            status_code=exc.status_code,
            reason=exc.reason,
        )


def _render_response_body(response):
    text = response.body.decode(response.charset or "utf-8", errors="replace")

    if _is_html_content_type(response.content_type):
        title = _extract_html_title(text)
        content = _html_to_markdown(text)
        return title, "markdown", content

    if _is_json_content_type(response.content_type):
        try:
            content = json.dumps(json.loads(text), indent=2, ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            content = text.strip()
        return "(no title)", "text", content

    if _is_textual_content_type(response.content_type):
        if response.content_type in {"text/markdown", "text/x-markdown"}:
            title = _extract_markdown_title(text) or "(no title)"
        else:
            title = "(no title)"
        return title, "text", text.strip()

    message = (
        "Binary or non-text content not rendered. "
        f"Content-Type: {response.content_type or 'unknown'}. "
        f"Download size: {len(response.body)} bytes."
    )
    return "(no title)", "binary", message


def _is_html_content_type(content_type):
    return content_type in {"text/html", "application/xhtml+xml"}


def _is_json_content_type(content_type):
    return content_type == "application/json" or content_type.endswith("+json")


def _is_textual_content_type(content_type):
    return (
        content_type.startswith("text/")
        or _is_json_content_type(content_type)
        or content_type.endswith("+xml")
        or content_type in {"application/xml", "application/javascript"}
    )


def _extract_html_title(html_text):
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not match:
        return "(no title)"
    title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return title or "(no title)"


def _extract_markdown_title(markdown):
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _html_to_markdown(html_text):
    cleaned = re.sub(r"(?is)<!DOCTYPE.*?>", "", html_text)
    converter = _MarkdownConverter()
    converter.feed(cleaned)
    converter.close()
    markdown = converter.get_markdown()
    if markdown:
        return markdown

    fallback = re.sub(r"(?is)<script[^>]*>.*?</script>", "", html_text)
    fallback = re.sub(r"(?is)<style[^>]*>.*?</style>", "", fallback)
    fallback = re.sub(r"(?s)<[^>]+>", " ", fallback)
    fallback = re.sub(r"[ \t]+", " ", unescape(fallback))
    fallback = re.sub(r"\n\s*\n", "\n\n", fallback)
    return fallback.strip()


def _is_permitted_redirect(original_url, redirect_url):
    try:
        original = urllib.parse.urlsplit(original_url)
        redirect = urllib.parse.urlsplit(redirect_url)
    except Exception:
        return False

    if redirect.scheme != original.scheme:
        return False
    if redirect.port != original.port:
        return False
    if redirect.username or redirect.password:
        return False

    return _strip_www(redirect.hostname or "") == _strip_www(original.hostname or "")


def _strip_www(hostname):
    return hostname.lower().removeprefix("www.")


def _format_redirect_notice(redirect):
    lines = [
        f"[redirect: {redirect.status_code} {redirect.reason}]",
        f"[original URL: {redirect.original_url}]",
        f"[redirect URL: {redirect.redirect_url}]",
        "",
        "Cross-host redirects are not followed automatically.",
        "Call read_url again with the redirect URL if you want to fetch the destination page.",
    ]
    return "\n".join(lines)


def _format_page(page, max_length):
    try:
        max_length = int(max_length)
    except (TypeError, ValueError):
        max_length = 10000
    if max_length < 0:
        max_length = 0

    content = page.content
    total_len = len(content)
    if total_len > max_length:
        content = content[:max_length] + f"\n\n... (truncated, {total_len} total characters)"

    header = [
        f"[{page.title}]",
        f"[content-type: {page.content_type}]",
        f"[format: {page.render_format}]",
        f"[{page.byte_count} bytes, {total_len} characters]",
    ]
    if page.final_url != page.original_url:
        header.insert(1, f"[redirected to: {page.final_url}]")

    return "\n".join(header) + "\n\n" + content
