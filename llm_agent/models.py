"""Canonical model registry: aliases, provider detection, and metadata.

This is the single source of truth for all model-related data.  Every
other module imports from here instead of maintaining its own copy.
"""

import os

# ---------------------------------------------------------------------------
# Aliases: short name → full model name
# ---------------------------------------------------------------------------

MODELS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-3.1-pro-preview",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "gpt-5.2": "gpt-5.2",
    "o3": "o3",
    "o4-mini": "o4-mini",
    "qwen3": "ollama:qwen3.5:122b",
    "qwen3-cloud": "ollama:qwen3.5:cloud",
    "qwen3-coder": "ollama:qwen3.5:35b-a3b-coding-nvfp4",
    "gemma4-31b": "ollama:gemma4:31b",
    "nemotron-nano": "ollama:nemotron-3-nano:latest",
}

DEFAULT_MODEL = "sonnet"

# ---------------------------------------------------------------------------
# Provider detection
# ---------------------------------------------------------------------------

# OpenAI model names that don't follow a prefix convention.
_OPENAI_MODELS = {"gpt-4o", "gpt-4o-mini", "gpt-5.2", "o3", "o4-mini", "o3-mini"}

# Reasoning models that use max_completion_tokens instead of max_tokens.
REASONING_MODELS = {"o3", "o4-mini", "o3-mini", "gpt-5.2"}


def provider(model):
    """Return the provider name for a fully-resolved model name."""
    if model.startswith("ollama:"):
        return "ollama"
    if model.startswith("gemini-"):
        return "gemini"
    if model in _OPENAI_MODELS:
        return "openai"
    return "anthropic"


def is_gemini_model(model):
    return model.startswith("gemini-")


def is_openai_model(model):
    return model in _OPENAI_MODELS


def is_ollama_model(model):
    return model.startswith("ollama:")


# ---------------------------------------------------------------------------
# Alias resolution and model name helpers
# ---------------------------------------------------------------------------

def resolve_alias(name):
    """Resolve a model alias to the full model name.

    Returns the alias value if found, passes through ollama: names and
    unknown names unchanged.
    """
    return MODELS.get(name, name)


def ollama_model_name(model):
    """Strip the 'ollama:' prefix to get the API model name."""
    if model.startswith("ollama:"):
        return model[len("ollama:"):]
    return model


# ---------------------------------------------------------------------------
# Context windows
# ---------------------------------------------------------------------------

CONTEXT_WINDOWS = {
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-3.1-pro-preview": 1_000_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-5.2": 400_000,
    "o3": 200_000,
    "o4-mini": 200_000,
}

OLLAMA_DEFAULT_CONTEXT = 32_768
CONTEXT_BUDGET = 0.80


def context_window(model):
    """Return the context window size for a model.

    Ollama models check OLLAMA_CONTEXT_WINDOW env var, then fall back
    to OLLAMA_DEFAULT_CONTEXT.  All others fall back to 200_000.
    """
    if is_ollama_model(model):
        return int(os.environ.get("OLLAMA_CONTEXT_WINDOW", OLLAMA_DEFAULT_CONTEXT))
    return CONTEXT_WINDOWS.get(model, 200_000)


# ---------------------------------------------------------------------------
# Max output tokens
# ---------------------------------------------------------------------------

_MAX_OUTPUT_TOKENS = {
    # Anthropic
    "claude-opus-4-6": 128_000,
    "claude-sonnet-4-6": 64_000,
    "claude-haiku-4-5": 64_000,
    # OpenAI
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "gpt-5.2": 128_000,
    "o3": 100_000,
    "o4-mini": 100_000,
}

# Default per provider when model isn't in the dict.
_DEFAULT_MAX_OUTPUT = {
    "anthropic": 64_000,
    "openai": 16_384,
    "gemini": 65_536,
    "ollama": 8_192,
}


def max_output_tokens(model):
    """Return the max output token limit for a model."""
    if model in _MAX_OUTPUT_TOKENS:
        return _MAX_OUTPUT_TOKENS[model]
    return _DEFAULT_MAX_OUTPUT.get(provider(model), 64_000)


# ---------------------------------------------------------------------------
# Per-model defaults
# ---------------------------------------------------------------------------

DEFAULT_THINKING = {
    "gemini-3.1-pro-preview": "high",
}

# Step limits for the agent loop.
MAX_STEPS = 20
# Gemini models tend to make single tool calls per turn rather than batching,
# so they burn through steps faster and need a higher limit.
MAX_STEPS_GEMINI = 50
