"""Tests for read_url tool."""

import pytest

import llm_agent.tools.read_url as read_url


@pytest.fixture(autouse=True)
def clear_read_url_cache():
    read_url._READ_URL_CACHE.clear()
    yield
    read_url._READ_URL_CACHE.clear()


def make_page(**overrides):
    defaults = {
        "original_url": "https://example.com/start",
        "final_url": "https://example.com/docs",
        "title": "Example Docs",
        "content_type": "text/html",
        "render_format": "markdown",
        "byte_count": 128,
        "content": "# Heading\n\nBody text.",
    }
    defaults.update(overrides)
    return read_url.PageContent(**defaults)


class TestHandle:
    def test_rejects_non_http_scheme(self):
        result = read_url.handle({"url": "ftp://example.com/file.txt"})
        assert "only http://" in result

    def test_rejects_embedded_credentials(self):
        result = read_url.handle({"url": "https://user:pass@example.com/private"})
        assert "embedded credentials" in result

    def test_formats_page_metadata_and_redirect(self, monkeypatch):
        monkeypatch.setattr(read_url, "_get_page", lambda url: make_page())

        result = read_url.handle({"url": "https://example.com/start"})

        assert "[Example Docs]" in result
        assert "[redirected to: https://example.com/docs]" in result
        assert "[content-type: text/html]" in result
        assert "[format: markdown]" in result
        assert "# Heading" in result

    def test_formats_cross_host_redirect_notice(self, monkeypatch):
        monkeypatch.setattr(
            read_url,
            "_get_page",
            lambda url: read_url.RedirectNotice(
                original_url="https://example.com/start",
                redirect_url="https://docs.example.net/landing",
                status_code=302,
                reason="Found",
            ),
        )

        result = read_url.handle({"url": "https://example.com/start"})

        assert "[redirect: 302 Found]" in result
        assert "Cross-host redirects are not followed automatically." in result
        assert "https://docs.example.net/landing" in result

    def test_truncates_rendered_content(self, monkeypatch):
        monkeypatch.setattr(
            read_url,
            "_get_page",
            lambda url: make_page(content="1234567890"),
        )

        result = read_url.handle({"url": "https://example.com/start", "max_length": 5})

        assert "12345" in result
        assert "truncated, 10 total characters" in result

    def test_uses_cache_for_repeated_reads(self, monkeypatch):
        calls = []

        def fake_load(url):
            calls.append(url)
            return make_page(final_url=url)

        monkeypatch.setattr(read_url, "_load_page_uncached", fake_load)

        first = read_url.handle({"url": "https://example.com/docs"})
        second = read_url.handle({"url": "https://example.com/docs"})

        assert calls == ["https://example.com/docs"]
        assert first == second


class TestRenderingHelpers:
    def test_html_to_markdown_converts_common_elements(self):
        html = """
        <html>
          <head><title>Example</title></head>
          <body>
            <h1>Docs</h1>
            <p>Read the <a href="https://example.com/guide">guide</a>.</p>
            <ul><li>One</li><li><strong>Two</strong></li></ul>
            <pre><code>print("hi")</code></pre>
          </body>
        </html>
        """

        result = read_url._html_to_markdown(html)

        assert "# Docs" in result
        assert "[guide](https://example.com/guide)" in result
        assert "- One" in result
        assert "- **Two**" in result
        assert "```" in result
        assert 'print("hi")' in result

    def test_render_response_body_formats_json(self):
        response = read_url.FetchResponse(
            original_url="https://example.com/data.json",
            final_url="https://example.com/data.json",
            status_code=200,
            reason="OK",
            content_type="application/json",
            charset="utf-8",
            body=b'{"b":1,"a":{"c":2}}',
        )

        title, render_format, content = read_url._render_response_body(response)

        assert title == "(no title)"
        assert render_format == "text"
        assert content.startswith("{\n")
        assert '"a": {' in content
        assert '"b": 1' in content

    def test_render_response_body_uses_markdown_title(self):
        response = read_url.FetchResponse(
            original_url="https://example.com/readme.md",
            final_url="https://example.com/readme.md",
            status_code=200,
            reason="OK",
            content_type="text/markdown",
            charset="utf-8",
            body=b"# Readme\n\nSome text.\n",
        )

        title, render_format, content = read_url._render_response_body(response)

        assert title == "Readme"
        assert render_format == "text"
        assert content.startswith("# Readme")

    def test_render_response_body_reports_binary_content(self):
        response = read_url.FetchResponse(
            original_url="https://example.com/file.pdf",
            final_url="https://example.com/file.pdf",
            status_code=200,
            reason="OK",
            content_type="application/pdf",
            charset="utf-8",
            body=b"%PDF-1.7",
        )

        title, render_format, content = read_url._render_response_body(response)

        assert title == "(no title)"
        assert render_format == "binary"
        assert "application/pdf" in content

    def test_permitted_redirects_allow_same_host_and_www_only(self):
        assert read_url._is_permitted_redirect(
            "https://example.com/docs",
            "https://www.example.com/guide",
        )
        assert not read_url._is_permitted_redirect(
            "https://example.com/docs",
            "https://docs.example.net/guide",
        )
