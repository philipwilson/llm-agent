"""User configuration file support (~/.config/llm-agent/config.toml)."""

import os
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

CONFIG_PATH = os.path.expanduser("~/.config/llm-agent/config.toml")

# Allowed keys and their expected types.
VALID_KEYS = {
    "model": str,
    "yolo": bool,
    "timeout": int,
    "thinking": str,
    "no_tui": bool,
    "debug": bool,
}


def load_config(path=CONFIG_PATH):
    """Read the TOML config file and return a dict of validated settings.

    Returns an empty dict if the file does not exist or cannot be parsed.
    Warns on stderr for unrecognized keys or type mismatches.
    """
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"Warning: could not parse {path}: {e}", file=sys.stderr)
        return {}

    config = {}
    for key, value in data.items():
        if key not in VALID_KEYS:
            print(f"Warning: unknown config key '{key}' in {path}", file=sys.stderr)
            continue
        expected = VALID_KEYS[key]
        if not isinstance(value, expected):
            print(
                f"Warning: config key '{key}' should be {expected.__name__}, "
                f"got {type(value).__name__} in {path}",
                file=sys.stderr,
            )
            continue
        config[key] = value
    return config
