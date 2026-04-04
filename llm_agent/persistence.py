"""Session persistence: save, load, list, and find sessions on disk.

Sessions are stored as JSON files under ~/.local/share/llm-agent/sessions/.
Each file contains the full conversation, model, usage stats, and metadata.
"""

import json
import os
import uuid
from datetime import datetime, timezone

SESSIONS_DIR = os.path.expanduser("~/.local/share/llm-agent/sessions")


def new_session_id():
    """Return an 8-char hex session ID."""
    return uuid.uuid4().hex[:8]


def session_path(session_id, started_at):
    """Return the file path for a session.

    Format: {YYYYMMDD-HHMMSS}-{session_id}.json
    """
    ts = started_at.strftime("%Y%m%d-%H%M%S")
    return os.path.join(SESSIONS_DIR, f"{ts}-{session_id}.json")


def save_session(path, session_id, model, messages, usage, started_at, first_question):
    """Write session JSON to disk.

    Strips non-serializable fields and large binary data.
    Uses atomic write (tmp + rename) to prevent corruption.
    """
    data = {
        "session_id": session_id,
        "version": 1,
        "model": model,
        "cwd": os.getcwd(),
        "started_at": started_at.isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "first_question": first_question,
        "message_count": len(messages),
        "usage": dict(usage),
        "messages": _clean_messages(messages),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=None, default=str)
    os.replace(tmp_path, path)


def load_session(path):
    """Load a session JSON file. Returns the parsed dict."""
    with open(path) as f:
        return json.load(f)


def list_sessions(limit=20):
    """List recent sessions (newest first).

    Returns lightweight metadata dicts without full message content.
    """
    if not os.path.isdir(SESSIONS_DIR):
        return []
    files = sorted(
        (f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")),
        reverse=True,
    )[:limit]
    sessions = []
    for fname in files:
        path = os.path.join(SESSIONS_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            sessions.append({
                "session_id": data.get("session_id", "?"),
                "model": data.get("model", "?"),
                "cwd": data.get("cwd", ""),
                "started_at": data.get("started_at", ""),
                "updated_at": data.get("updated_at", ""),
                "first_question": data.get("first_question", ""),
                "message_count": data.get("message_count", 0),
                "path": path,
            })
        except (json.JSONDecodeError, OSError):
            continue
    return sessions


def find_session(identifier):
    """Find a session file path by ID prefix or 'last'.

    Returns the path string, or None if not found.
    """
    if not os.path.isdir(SESSIONS_DIR):
        return None
    files = sorted(
        (f for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")),
        reverse=True,
    )
    if not files:
        return None
    if identifier == "last":
        return os.path.join(SESSIONS_DIR, files[0])
    # Match by session_id prefix in filename (the part after the timestamp dash)
    for fname in files:
        # Filename format: YYYYMMDD-HHMMSS-{session_id}.json
        parts = fname.rsplit("-", 1)
        if len(parts) == 2:
            file_id = parts[1].replace(".json", "")
            if file_id.startswith(identifier):
                return os.path.join(SESSIONS_DIR, fname)
    return None


def _clean_messages(messages):
    """Prepare messages for JSON serialization.

    - Strips _gemini_parts (non-serializable protobuf objects)
    - Replaces base64 image/document data with placeholder
    """
    cleaned = []
    for msg in messages:
        msg = dict(msg)
        msg.pop("_gemini_parts", None)
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = [_clean_block(b) for b in content]
        cleaned.append(msg)
    return cleaned


def _clean_block(block):
    """Strip binary data from a content block."""
    if not isinstance(block, dict):
        return block
    source = block.get("source")
    if isinstance(source, dict) and source.get("type") == "base64":
        block = dict(block)
        block["source"] = {
            **source,
            "data": "(binary data stripped)",
        }
    return block
