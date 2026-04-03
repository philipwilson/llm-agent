"""Tests for web_search tool."""

from types import SimpleNamespace

import pytest

import llm_agent.tools.web_search as web_search


SAMPLE_HTML = """
<div class="result">
  <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fjson.html">Python JSON docs</a>
  <a class="result__snippet">Official &amp; standard library docs.</a>
</div>
<div class="result">
  <a class="result__a" href="https://blog.example.com/post">Example Blog</a>
  <a class="result__snippet">A third-party guide.</a>
</div>
<div class="result">
  <a class="result__a" href="https://news.python.org/item">Python News</a>
  <a class="result__snippet">Latest release update.</a>
</div>
"""


def _install_fake_curl(monkeypatch, html=SAMPLE_HTML, returncode=0):
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append({
            "args": args,
            "capture_output": capture_output,
            "text": text,
            "timeout": timeout,
        })
        return SimpleNamespace(returncode=returncode, stdout=html)

    times = iter([100.0, 100.25])
    monkeypatch.setattr(web_search.subprocess, "run", fake_run)
    monkeypatch.setattr(web_search.time, "monotonic", lambda: next(times))
    return calls


class TestWebSearch:
    def test_build_context_tracks_provider(self):
        anthropic_context = web_search.build_context(object(), "claude-sonnet-4-6")
        openai_context = web_search.build_context(object(), "gpt-5.2")
        gemini_context = web_search.build_context(object(), "gemini-2.5-flash")

        assert anthropic_context["provider"] == "anthropic"
        assert anthropic_context["user_location"]["country"] == "US"
        assert openai_context["provider"] == "openai"
        assert openai_context["user_location"]["country"] == "US"
        assert gemini_context["provider"] == "gemini"
        assert "user_location" not in gemini_context

    def test_formats_results_with_sources_and_reminder(self, monkeypatch):
        calls = _install_fake_curl(monkeypatch)

        result = web_search.handle({"query": "python json", "max_results": 2})

        assert '[2 results for "python json" via DuckDuckGo in 250ms]' in result
        assert "Python JSON docs" in result
        assert "Official & standard library docs." in result
        assert "Sources:" in result
        assert "- [Python JSON docs](https://docs.python.org/3/library/json.html)" in result
        assert "Source links (JSON):" in result
        assert '"title": "Python JSON docs"' in result
        assert "REMINDER: Cite relevant links" in result
        assert "--data-urlencode" in calls[0]["args"]

    def test_allowed_domains_filters_results(self, monkeypatch):
        _install_fake_curl(monkeypatch)

        result = web_search.handle({
            "query": "python",
            "allowed_domains": ["https://python.org/docs"],
        })

        assert "[allowed domains: python.org]" in result
        assert "docs.python.org/3/library/json.html" in result
        assert "news.python.org/item" in result
        assert "blog.example.com/post" not in result

    def test_blocked_domains_filters_results(self, monkeypatch):
        _install_fake_curl(monkeypatch)

        result = web_search.handle({
            "query": "python",
            "blocked_domains": ["python.org"],
        })

        assert "[blocked domains: python.org]" in result
        assert "blog.example.com/post" in result
        assert "docs.python.org/3/library/json.html" not in result
        assert "news.python.org/item" not in result

    def test_conflicting_domain_filters(self):
        result = web_search.handle({
            "query": "python",
            "allowed_domains": ["python.org"],
            "blocked_domains": ["example.com"],
        })

        assert "cannot specify both allowed_domains and blocked_domains" in result

    def test_invalid_domain_filter_type(self):
        result = web_search.handle({
            "query": "python",
            "allowed_domains": "python.org",
        })

        assert "allowed_domains must be a list of domains" in result

    def test_no_results_after_filter(self, monkeypatch):
        _install_fake_curl(monkeypatch)

        result = web_search.handle({
            "query": "python",
            "allowed_domains": ["no-such-domain.example"],
        })

        assert '[0 results for "python" via DuckDuckGo in 250ms]' in result
        assert "(no results found for: python)" in result

    def test_recency_query_adds_current_year(self, monkeypatch):
        calls = _install_fake_curl(monkeypatch)
        monkeypatch.setattr(web_search, "_current_year", lambda: 2026)

        result = web_search.handle({"query": "latest react docs"})

        assert "[executed query: latest react docs 2026]" in result
        assert "q=latest react docs 2026" in calls[0]["args"]

    def test_explicit_year_is_not_rewritten(self, monkeypatch):
        calls = _install_fake_curl(monkeypatch)
        monkeypatch.setattr(web_search, "_current_year", lambda: 2026)

        result = web_search.handle({"query": "latest react docs 2025"})

        assert "[executed query:" not in result
        assert "q=latest react docs 2025" in calls[0]["args"]

    def test_native_anthropic_backend_used_when_supported(self, monkeypatch):
        monkeypatch.setattr(web_search, "_current_year", lambda: 2026)
        monkeypatch.setattr(
            web_search,
            "_supports_native_anthropic_web_search",
            lambda context: True,
        )

        native_calls = []

        def fake_native(query, max_results, allowed_domains=None, blocked_domains=None, context=None):
            native_calls.append({
                "query": query,
                "max_results": max_results,
                "allowed_domains": allowed_domains,
                "blocked_domains": blocked_domains,
                "context": context,
            })
            return web_search.SearchResponse(
                query=query,
                executed_query=query,
                results=[
                    web_search.SearchResult(
                        title="React Docs",
                        url="https://react.dev",
                        snippet="",
                        domain="react.dev",
                    ),
                ],
                duration_seconds=0.25,
                backend="anthropic_native",
                allowed_domains=[],
                blocked_domains=[],
                commentary=["- Official React docs are current on react.dev."],
            ), None

        monkeypatch.setattr(web_search, "_search_native_anthropic", fake_native)
        monkeypatch.setattr(web_search.subprocess, "run", lambda *a, **k: pytest.fail("fallback should not run"))

        result = web_search.handle(
            {"query": "latest react docs"},
            context={"client": object(), "model": "claude-sonnet-4-6"},
        )

        assert '[1 results for "latest react docs" via Anthropic web search in 250ms]' in result
        assert "[executed query: latest react docs 2026]" in result
        assert "- Official React docs are current on react.dev." in result
        assert native_calls[0]["query"] == "latest react docs 2026"

    def test_openai_backend_parses_citations(self, monkeypatch):
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                error=None,
                output=[
                    SimpleNamespace(
                        type="web_search_call",
                        action=SimpleNamespace(
                            type="search",
                            query="latest react docs 2026",
                            sources=[SimpleNamespace(type="url", url="https://react.dev/reference")],
                        ),
                        status="completed",
                    ),
                    SimpleNamespace(
                        type="message",
                        content=[
                            SimpleNamespace(
                                type="output_text",
                                text="React docs are current.",
                                annotations=[
                                    SimpleNamespace(
                                        type="url_citation",
                                        start_index=0,
                                        end_index=len("React docs are current."),
                                        title="React Docs",
                                        url="https://react.dev/reference",
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            )

        times = iter([200.0, 200.4])
        monkeypatch.setattr(web_search.time, "monotonic", lambda: next(times))

        client = SimpleNamespace(responses=SimpleNamespace(create=fake_create))
        response, error = web_search._search_native_openai(
            "latest react docs 2026",
            5,
            allowed_domains=["react.dev"],
            context=web_search.build_context(client, "gpt-5.2"),
        )

        assert error is None
        assert response.backend == "openai_native"
        assert response.executed_query == "latest react docs 2026"
        assert response.results[0].title == "React Docs"
        assert response.results[0].url == "https://react.dev/reference"
        assert response.commentary == ["React docs are current.[1]"]
        assert calls[0]["include"] == web_search.OPENAI_INCLUDE_FIELDS
        assert calls[0]["tools"][0]["filters"] == {"allowed_domains": ["react.dev"]}

    def test_gemini_backend_parses_grounding_metadata(self, monkeypatch):
        calls = []

        def fake_generate_content(*, model, contents, config):
            calls.append({
                "model": model,
                "contents": contents,
                "config": config,
            })
            return SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[SimpleNamespace(text="Python docs are current.")]
                        ),
                        grounding_metadata=SimpleNamespace(
                            web_search_queries=["latest python docs 2026"],
                            grounding_chunks=[
                                SimpleNamespace(
                                    web=SimpleNamespace(
                                        uri="https://docs.python.org/3/",
                                        title="Python Docs",
                                    )
                                ),
                            ],
                            grounding_supports=[
                                SimpleNamespace(
                                    segment=SimpleNamespace(
                                        start_index=0,
                                        end_index=len("Python docs are current.".encode("utf-8")),
                                    ),
                                    grounding_chunk_indices=[0],
                                ),
                            ],
                        ),
                    ),
                ],
            )

        times = iter([300.0, 300.2])
        monkeypatch.setattr(web_search.time, "monotonic", lambda: next(times))

        client = SimpleNamespace(models=SimpleNamespace(generate_content=fake_generate_content))
        response, error = web_search._search_native_gemini(
            "latest python docs 2026",
            5,
            blocked_domains=["example.com"],
            context=web_search.build_context(client, "gemini-2.5-flash"),
        )

        assert error is None
        assert response.backend == "gemini_native"
        assert response.executed_query == "latest python docs 2026"
        assert response.results[0].title == "Python Docs"
        assert response.commentary == ["Python docs are current.[1]"]
        assert calls[0]["model"] == "gemini-2.5-flash"
        tools = web_search._get_value(calls[0]["config"], "tools", default=[]) or []
        google_search = web_search._get_value(tools[0], "google_search", "googleSearch")
        assert web_search._get_value(
            google_search, "exclude_domains", "excludeDomains", default=[]
        ) == ["example.com"]

    def test_local_only_filtering_strips_commentary(self):
        response = web_search.SearchResponse(
            query="python",
            executed_query="python",
            results=[
                web_search.SearchResult(
                    title="Blocked Result",
                    url="https://blocked.example/article",
                    snippet="",
                    domain="blocked.example",
                ),
            ],
            duration_seconds=0.1,
            backend="openai_native",
            allowed_domains=[],
            blocked_domains=["blocked.example"],
            commentary=["This summary came from a blocked domain."],
        )

        response = web_search._finalize_search_response(
            response,
            8,
            supports_allowed_domains=True,
            supports_blocked_domains=False,
        )

        assert response.results == []
        assert response.commentary == []
        assert any("Blocked-domain filtering was applied locally" in note for note in response.notes)

    def test_native_failure_falls_back_to_duckduckgo(self, monkeypatch):
        monkeypatch.setattr(
            web_search,
            "_supports_native_anthropic_web_search",
            lambda context: True,
        )
        monkeypatch.setattr(
            web_search,
            "_search_native_anthropic",
            lambda *a, **k: (None, "(native web search error: unavailable)"),
        )
        _install_fake_curl(monkeypatch)

        result = web_search.handle(
            {"query": "python json"},
            context={"client": object(), "model": "claude-sonnet-4-6"},
        )

        assert '[3 results for "python json" via DuckDuckGo in 250ms]' in result

    @pytest.mark.parametrize("query", ["", "   "])
    def test_query_required(self, query):
        result = web_search.handle({"query": query})
        assert "query is required" in result

    def test_max_results_must_be_positive(self):
        result = web_search.handle({"query": "python", "max_results": 0})
        assert "max_results must be >= 1" in result
