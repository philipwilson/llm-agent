"""Subagent definitions and execution."""

import json
import os
import threading
import time

from llm_agent.display import get_display
from llm_agent.formatting import bold, dim, format_tokens, yellow, red
from llm_agent.models import MODELS as MODEL_ALIASES, provider as _provider


# ---------- Built-in agent definitions ----------

DEFAULT_SUBAGENT_MAX_STEPS = 100

BUILTIN_AGENTS = {
    "explore": {
        "name": "explore",
        "description": "Fast read-only research agent (uses haiku)",
        "model": "haiku",
        "max_steps": DEFAULT_SUBAGENT_MAX_STEPS,
        "tools": [
            "read_file", "read_many_files", "list_directory", "search_files",
            "glob_files", "file_outline", "read_url", "web_search",
        ],
        "system_prompt": (
            "You are a fast research assistant. Explore the filesystem and code "
            "to answer questions. You have read-only tools.\n\n"
            "Efficiency rules:\n"
            "- Use file_outline to understand structure before reading full files.\n"
            "- Use read_many_files to read several files in one call instead of "
            "separate read_file calls.\n"
            "- When reading large files, set limit to 500+ lines to avoid "
            "needing multiple reads.\n"
            "- Batch parallel tool calls whenever possible.\n\n"
            "Output rules:\n"
            "- Summarize findings concisely — do not reproduce large blocks of "
            "source code.\n"
            "- Focus on answering the question, not narrating your exploration.\n"
            "- Keep your final answer under 200 lines."
        ),
    },
    "code": {
        "name": "code",
        "description": "Full-capability coding agent (inherits parent model)",
        "model": None,  # inherit from parent
        "max_steps": DEFAULT_SUBAGENT_MAX_STEPS,
        "tools": [
            "read_file", "read_many_files", "list_directory", "search_files", "glob_files",
            "file_outline",
            "read_url", "web_search", "write_file", "edit_file", "apply_patch",
            "run_command", "check_task", "start_session", "write_stdin",
        ],
        "system_prompt": None,  # inherit from parent
    },
}

_EMPTY_USAGE = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}


def _extract_final_text(messages):
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                block["text"]
                for block in content
                if block.get("type") == "text" and block.get("text")
            ]
            if texts:
                return "\n".join(texts)
        break
    return "(subagent produced no text output)"


def _resolve_subagent_definition(agent_name):
    agents = load_all_agents()
    defn = agents.get(agent_name)
    if defn is None:
        available = ", ".join(sorted(agents.keys()))
        return None, (
            f"(error: unknown agent '{agent_name}'. Available agents: {available})"
        )
    return defn, None


def _resolve_subagent_model(defn, parent_model, model_override=None):
    sub_model = model_override or defn.get("model") or parent_model
    if sub_model in MODEL_ALIASES:
        sub_model = MODEL_ALIASES[sub_model]
    return sub_model


def _normalize_positive_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _resolve_subagent_max_steps(defn):
    for key in ("max_steps", "max_turns"):
        resolved = _normalize_positive_int(defn.get(key))
        if resolved is not None:
            return resolved
    return DEFAULT_SUBAGENT_MAX_STEPS


class BackgroundSubagentTask:
    """Tracks a delegated subagent running in a background thread."""

    def __init__(
        self,
        task_id,
        agent_name,
        task,
        client,
        parent_model,
        auto_approve,
        thinking_level=None,
        model_override=None,
    ):
        self.task_id = task_id
        self.agent = agent_name
        self.task = task
        self.parent_model = parent_model
        self.model_override = model_override
        self.started_at = time.time()
        self.finished_at = None
        self._lock = threading.Lock()
        self.max_steps = DEFAULT_SUBAGENT_MAX_STEPS
        self._metadata = {
            "agent": agent_name,
            "model": None,
            "status": "running",
            "steps": 0,
            "max_steps": self.max_steps,
            "duration_seconds": 0.0,
            "usage": dict(_EMPTY_USAGE),
            "result": "",
        }
        defn, error = _resolve_subagent_definition(agent_name)
        if defn is not None:
            self.max_steps = _resolve_subagent_max_steps(defn)
            self._metadata["model"] = _resolve_subagent_model(
                defn, parent_model, model_override=model_override
            )
            self._metadata["max_steps"] = self.max_steps
        else:
            self._metadata["status"] = "error"
            self._metadata["result"] = error
            self.finished_at = self.started_at
        self._thread = threading.Thread(
            target=self._run,
            args=(client, auto_approve, thinking_level),
            daemon=True,
        )

    def start(self):
        if self.finished_at is None:
            self._thread.start()

    def _run(self, client, auto_approve, thinking_level):
        try:
            result = run_subagent(
                self.agent,
                self.task,
                client,
                self.parent_model,
                auto_approve,
                thinking_level=thinking_level,
                model_override=self.model_override,
                return_metadata=True,
            )
        except Exception as e:
            result = {
                "agent": self.agent,
                "model": self._metadata.get("model"),
                "status": "error",
                "steps": 0,
                "duration_seconds": 0.0,
                "usage": dict(_EMPTY_USAGE),
                "result": f"(error in background subagent '{self.agent}': {e})",
            }
        with self._lock:
            self._metadata = result
            self.finished_at = time.time()

    def snapshot(self):
        with self._lock:
            metadata = dict(self._metadata)
            usage = dict(metadata.get("usage") or _EMPTY_USAGE)
        finished_at = self.finished_at
        status = metadata.get("status", "running")
        if finished_at is None and status != "running":
            finished_at = time.time()
        duration = max(
            0.0,
            (finished_at or time.time()) - self.started_at,
        )
        return {
            "task_id": self.task_id,
            "type": "delegate",
            "agent": self.agent,
            "task": self.task,
            "requested_model": self.model_override,
            "model": metadata.get("model"),
            "status": status,
            "steps": metadata.get("steps", 0),
            "max_steps": metadata.get("max_steps", self.max_steps),
            "started_at": self.started_at,
            "finished_at": finished_at,
            "duration_seconds": duration,
            "usage": usage,
            "result": metadata.get("result", ""),
        }


class BackgroundSubagentStore:
    """Per-session background delegated subagent tasks."""

    def __init__(self):
        self._tasks = {}
        self._counter = 0
        self._lock = threading.Lock()

    def start(
        self,
        agent_name,
        task,
        client,
        parent_model,
        auto_approve,
        thinking_level=None,
        model_override=None,
    ):
        with self._lock:
            self._counter += 1
            task_id = f"sub-{self._counter}"
            bg_task = BackgroundSubagentTask(
                task_id,
                agent_name,
                task,
                client,
                parent_model,
                auto_approve,
                thinking_level=thinking_level,
                model_override=model_override,
            )
            self._tasks[task_id] = bg_task
        bg_task.start()
        return bg_task.snapshot()

    def get_task(self, task_id):
        task = self._tasks.get(task_id)
        if task is None:
            return None
        return task.snapshot()

    def list_tasks(self):
        return [task.snapshot() for task in self._tasks.values()]


def _load_custom_agents():
    """Load custom agent definitions from ~/.agents/ and .agents/ directories.

    Project-level (.agents/) takes priority over user-level (~/.agents/).
    """
    agents = {}
    dirs = [
        os.path.expanduser("~/.agents"),
        os.path.join(os.getcwd(), ".agents"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(d, fname)
            try:
                with open(path) as f:
                    defn = json.load(f)
                name = defn.get("name", fname[:-5])
                # Never allow delegate or ask_user in subagent tools
                tools = defn.get("tools")
                if tools:
                    excluded = {"delegate", "ask_user"}
                    filtered = [t for t in tools if t not in excluded]
                    if len(filtered) != len(tools):
                        defn["tools"] = filtered
                for key in ("max_steps", "max_turns"):
                    if key not in defn:
                        continue
                    normalized = _normalize_positive_int(defn.get(key))
                    if normalized is None:
                        get_display().error(
                            f"{yellow(f'Warning: ignoring invalid {key} in {path}; expected a positive integer')}"
                        )
                        defn.pop(key, None)
                    else:
                        defn[key] = normalized
                agents[name] = defn
            except (json.JSONDecodeError, OSError) as e:
                get_display().error(f"{yellow(f'Warning: skipping {path}: {e}')}")
    return agents


def load_all_agents():
    """Return a dict of all available agents (built-in + custom)."""
    agents = dict(BUILTIN_AGENTS)
    agents.update(_load_custom_agents())
    return agents


def run_subagent(
    agent_name,
    task,
    client,
    model,
    auto_approve,
    thinking_level=None,
    model_override=None,
    return_metadata=False,
):
    """Run a subagent to completion and return its final text answer or metadata."""
    from llm_agent.tools import build_tool_set
    from llm_agent.tools.base import FileObservationStore

    def finish(status, result_text, *, resolved_model=None, turns=0, max_steps=None, usage=None, started_at=None):
        duration = 0.0
        if started_at is not None:
            duration = max(0.0, time.monotonic() - started_at)
        metadata = {
            "agent": agent_name,
            "model": resolved_model,
            "status": status,
            "steps": turns,
            "max_steps": max_steps,
            "duration_seconds": round(duration, 2),
            "usage": dict(usage or _EMPTY_USAGE),
            "result": result_text,
        }
        return metadata if return_metadata else result_text

    defn, error = _resolve_subagent_definition(agent_name)
    if error:
        return finish(
            "error",
            error,
        )

    # Resolve model
    sub_model = _resolve_subagent_model(defn, model, model_override=model_override)
    max_steps = _resolve_subagent_max_steps(defn)

    # Resolve tools
    tool_names = defn.get("tools")
    if tool_names:
        # Always exclude delegate and ask_user from subagents
        excluded = {"delegate", "ask_user"}
        tool_names = [t for t in tool_names if t not in excluded]
        tools, tool_registry = build_tool_set(tool_names)
    else:
        # Default: all tools except delegate and ask_user
        tools, tool_registry = build_tool_set(exclude=["delegate", "ask_user"])

    # Resolve system prompt
    system_prompt = defn.get("system_prompt")  # None means inherit parent default

    # Create a separate client if the provider differs
    sub_client = client
    if _provider(sub_model) != _provider(model):
        from llm_agent.cli import make_client
        try:
            sub_client = make_client(sub_model)
        except SystemExit:
            return finish(
                "error",
                (
                    f"(error: cannot create {_provider(sub_model)} client for subagent "
                    f"'{agent_name}' — missing API key or SDK)"
                ),
                resolved_model=sub_model,
            )

    if "web_search" in tool_registry:
        from llm_agent.tools import web_search
        tool_registry["web_search"] = {
            **tool_registry["web_search"],
            "context": web_search.build_context(sub_client, sub_model),
        }
    file_context = {"file_observations": FileObservationStore()}
    for tool_name in ("read_file", "read_many_files", "edit_file", "write_file", "apply_patch"):
        if tool_name in tool_registry:
            tool_registry[tool_name] = {
                **tool_registry[tool_name],
                "context": file_context,
            }

    # Pick the right turn function
    if _provider(sub_model) == "openai":
        from llm_agent.openai_agent import openai_agent_turn
        turn_fn = openai_agent_turn
    elif _provider(sub_model) == "gemini":
        from llm_agent.gemini_agent import gemini_agent_turn
        turn_fn = gemini_agent_turn
    elif _provider(sub_model) == "ollama":
        from llm_agent.ollama_agent import ollama_agent_turn
        turn_fn = ollama_agent_turn
    else:
        from llm_agent.agent import agent_turn
        turn_fn = agent_turn

    # Run the agent loop
    messages = [{"role": "user", "content": task}]
    sub_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
    steps = 0
    started_at = time.monotonic()

    display = get_display()
    display.status(
        f"  [{agent_name} subagent starting: model {sub_model}, max {max_steps} steps]"
    )
    display.subagent_started()

    extra_kwargs = {}
    if _provider(sub_model) == "gemini" and thinking_level:
        extra_kwargs["thinking_level"] = thinking_level

    # Suppress streaming for subagents — their final answer is returned to
    # the parent rather than streamed.  This prevents garbled output when
    # multiple subagents run concurrently via dispatch_tool_calls().
    try:
        with display.suppress_streaming():
            try:
                while True:
                    messages, done = turn_fn(
                        sub_client, sub_model, messages, auto_approve,
                        usage_totals=sub_usage,
                        tools=tools, tool_registry=tool_registry,
                        system_prompt=system_prompt,
                        **extra_kwargs,
                    )
                    steps += 1
                    if done:
                        break
                    display.status(
                        f"  [{agent_name} subagent progress: step {steps}, continuing]"
                    )
                    if steps >= max_steps:
                        display.error(f"\n{yellow(f'  (subagent hit step limit of {max_steps})')}")
                        break
            except KeyboardInterrupt:
                display.status(f"  (subagent interrupted)")
                return finish(
                    "interrupted",
                    "(subagent was interrupted by the user)",
                    resolved_model=sub_model,
                    turns=steps,
                    max_steps=max_steps,
                    usage=sub_usage,
                    started_at=started_at,
                )
    finally:
        display.subagent_finished()

    # Print subagent usage
    cache_info = ""
    if sub_usage.get("cache_read", 0) > 0:
        cache_info = f", {format_tokens(sub_usage['cache_read'])} cached"
    duration_seconds = max(0.0, time.monotonic() - started_at)
    status = "completed"
    if steps >= max_steps:
        status = "step_limit"
    display.status(
        f"  [{agent_name} subagent done: "
        f"{steps} step{'s' if steps != 1 else ''}, "
        f"{format_tokens(sub_usage['input'])} in, "
        f"{format_tokens(sub_usage['output'])} out{cache_info}, "
        f"{duration_seconds:.2f}s]"
    )

    result_text = _extract_final_text(messages)
    return finish(
        status,
        result_text,
        resolved_model=sub_model,
        turns=steps,
        max_steps=max_steps,
        usage=sub_usage,
        started_at=started_at,
    )
