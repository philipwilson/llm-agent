"""Tests for llm_agent.models — canonical model registry."""

import os

import pytest

from llm_agent.models import (
    MODELS,
    DEFAULT_MODEL,
    REASONING_MODELS,
    context_window,
    is_gemini_model,
    is_ollama_model,
    is_openai_model,
    max_output_tokens,
    ollama_model_name,
    provider,
    resolve_alias,
)


class TestModelsDict:
    def test_default_model_in_aliases(self):
        assert DEFAULT_MODEL in MODELS

    def test_all_aliases_resolve(self):
        for alias, full in MODELS.items():
            assert isinstance(full, str) and len(full) > 0


class TestProvider:
    def test_anthropic(self):
        assert provider("claude-opus-4-6") == "anthropic"
        assert provider("claude-sonnet-4-6") == "anthropic"

    def test_gemini(self):
        assert provider("gemini-2.5-flash") == "gemini"
        assert provider("gemini-3.1-pro-preview") == "gemini"

    def test_openai(self):
        assert provider("gpt-4o") == "openai"
        assert provider("o3") == "openai"
        assert provider("gpt-5.2") == "openai"

    def test_ollama(self):
        assert provider("ollama:qwen3.5:122b") == "ollama"
        assert provider("ollama:mistral") == "ollama"

    def test_unknown_defaults_to_anthropic(self):
        assert provider("some-future-model") == "anthropic"


class TestIsHelpers:
    def test_is_gemini_model(self):
        assert is_gemini_model("gemini-2.5-flash")
        assert not is_gemini_model("gpt-4o")

    def test_is_openai_model(self):
        assert is_openai_model("gpt-4o")
        assert is_openai_model("o3")
        assert not is_openai_model("claude-opus-4-6")

    def test_is_ollama_model(self):
        assert is_ollama_model("ollama:mistral")
        assert not is_ollama_model("mistral")


class TestResolveAlias:
    def test_known_alias(self):
        assert resolve_alias("opus") == "claude-opus-4-6"
        assert resolve_alias("haiku") == "claude-haiku-4-5"

    def test_unknown_passthrough(self):
        assert resolve_alias("claude-opus-4-6") == "claude-opus-4-6"
        assert resolve_alias("some-unknown") == "some-unknown"

    def test_ollama_passthrough(self):
        assert resolve_alias("ollama:mistral") == "ollama:mistral"


class TestOllamaModelName:
    def test_strips_prefix(self):
        assert ollama_model_name("ollama:qwen3.5:122b") == "qwen3.5:122b"

    def test_no_prefix(self):
        assert ollama_model_name("mistral") == "mistral"

    def test_empty_after_prefix(self):
        assert ollama_model_name("ollama:") == ""


class TestContextWindow:
    def test_known_model(self):
        assert context_window("claude-opus-4-6") == 200_000
        assert context_window("gemini-2.5-flash") == 1_000_000

    def test_unknown_model_default(self):
        assert context_window("future-model") == 200_000

    def test_ollama_default(self):
        assert context_window("ollama:mistral") == 32_768

    def test_ollama_env_override(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "65536")
        assert context_window("ollama:mistral") == 65_536


class TestMaxOutputTokens:
    def test_known_anthropic(self):
        assert max_output_tokens("claude-opus-4-6") == 128_000

    def test_known_openai(self):
        assert max_output_tokens("gpt-4o") == 16_384
        assert max_output_tokens("o3") == 100_000

    def test_unknown_anthropic_default(self):
        assert max_output_tokens("claude-future-5") == 64_000

    def test_unknown_ollama_default(self):
        assert max_output_tokens("ollama:custom") == 8_192


class TestReasoningModels:
    def test_known_reasoning(self):
        assert "o3" in REASONING_MODELS
        assert "gpt-5.2" in REASONING_MODELS

    def test_non_reasoning(self):
        assert "gpt-4o" not in REASONING_MODELS
