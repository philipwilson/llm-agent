"""Session: owns all agent state and command routing.

Both UI layers (readline REPL and Textual TUI) delegate to this class
for state management and command handling, keeping them as thin
input/output adapters.
"""

from datetime import datetime, timezone

from llm_agent import VERSION
from llm_agent.display import get_display
from llm_agent.formatting import dim, red, yellow
from llm_agent.persistence import (
    new_session_id, session_path, save_session, load_session,
    list_sessions, find_session,
)
from llm_agent.skills import load_all_skills, render_skill, format_skill_list
from llm_agent.tools.base import FileObservationStore
from llm_agent.agents import BackgroundSubagentStore, DEFAULT_SUBAGENT_MAX_STEPS


class Session:
    """Agent session state and command routing."""

    def __init__(self, client, model, auto_approve=False, thinking_level=None):
        self.client = client
        self.model = model
        self.auto_approve = auto_approve
        self.thinking_level = thinking_level
        self.conversation = []
        self.session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
        self.skills = load_all_skills()
        self.last_response = ""
        self._file_observations = FileObservationStore()
        self._subagent_tasks = BackgroundSubagentStore()

        # Session persistence
        self._session_id = new_session_id()
        self._session_path = None
        self._started_at = datetime.now(timezone.utc)
        self._first_question = None
        self._persist = True  # disabled for -c single-shot mode

        # Per-session system prompt (Phase 3)
        from llm_agent.agent import refresh_project_context
        self._system_prompt = refresh_project_context()

        from llm_agent.debug import get_debug
        get_debug().log_system_prompt(self._system_prompt)

        self._setup_delegate()

    @classmethod
    def load_from(cls, path, client, model, auto_approve=False, thinking_level=None):
        """Resume a session from a saved file.

        The model from the file is used unless the caller provides an
        explicit override (i.e. user passed -m on the CLI).
        """
        data = load_session(path)
        session = cls(client, model, auto_approve=auto_approve,
                      thinking_level=thinking_level)
        session.conversation = data.get("messages", [])
        session.session_usage = data.get("usage", session.session_usage)
        session._session_id = data.get("session_id", session._session_id)
        session._session_path = path
        session._first_question = data.get("first_question")
        started = data.get("started_at")
        if started:
            try:
                session._started_at = datetime.fromisoformat(started)
            except (ValueError, TypeError):
                pass
        return session

    def handle_command(self, text):
        """Try to handle text as a built-in command or skill.

        Returns:
            None — not a command, text should be passed to run_question as-is.
            (messages, None) — command fully handled, display messages.
            (messages, transformed_text) — skill applied, display messages
                then run transformed_text through the agent.
        """
        stripped = text.strip()

        if stripped == "/clear":
            return self.clear(), None

        if stripped == "/version":
            return [f"llm-agent v{VERSION} (model: {self.model})"], None

        if stripped.startswith("/model"):
            return self._handle_model(stripped), None

        if stripped.startswith("/thinking"):
            return self._handle_thinking(stripped), None

        if stripped == "/mcp":
            return self._handle_mcp(), None

        if stripped == "/skills":
            return self._handle_skills(), None

        if stripped == "/sessions":
            return self._handle_sessions(), None

        # Skill invocation
        if text.startswith("/"):
            parts = text.split(None, 1)
            skill_name = parts[0][1:]
            if skill_name in self.skills:
                args_string = parts[1] if len(parts) > 1 else ""
                rendered = render_skill(self.skills[skill_name], args_string)
                return [f"  (skill: {skill_name})"], rendered
            else:
                return [f"(unknown command '/{skill_name}')"], None

        return None

    def run_question(self, user_input):
        """Run a single question through the agent loop.

        Updates self.conversation and self.session_usage on success.

        Returns (success, turn_usage) where:
            success: True if completed, False if cancelled/error.
            turn_usage: dict with input/output/cache_read/cache_create/last_input
                        and 'trimmed' count on success.
        """
        from llm_agent.cli import parse_attachments, trim_conversation, estimate_tokens
        from llm_agent.models import (
            is_gemini_model, is_openai_model, is_ollama_model,
            MAX_STEPS, MAX_STEPS_GEMINI,
        )
        from llm_agent.agent import agent_turn

        turn_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}

        if self._first_question is None:
            self._first_question = user_input[:200]

        text, attachment_blocks, error = parse_attachments(user_input)
        if error:
            get_display().error(red(error))
            return False, turn_usage

        if attachment_blocks:
            content = attachment_blocks + [{"type": "text", "text": text}]
        else:
            content = user_input

        messages = list(self.conversation) + [{"role": "user", "content": content}]
        steps = 0

        if is_ollama_model(self.model):
            from llm_agent.ollama_agent import ollama_agent_turn
            turn_fn = ollama_agent_turn
        elif is_openai_model(self.model):
            from llm_agent.openai_agent import openai_agent_turn
            turn_fn = openai_agent_turn
        elif is_gemini_model(self.model):
            from llm_agent.gemini_agent import gemini_agent_turn
            turn_fn = gemini_agent_turn
        else:
            turn_fn = agent_turn

        max_steps = MAX_STEPS_GEMINI if is_gemini_model(self.model) else MAX_STEPS

        extra_kwargs = {}
        if is_gemini_model(self.model) and self.thinking_level:
            extra_kwargs["thinking_level"] = self.thinking_level
        if self._system_prompt:
            extra_kwargs["system_prompt"] = self._system_prompt

        cancelled = False
        try:
            while True:
                messages, done = turn_fn(
                    self.client, self.model, messages, self.auto_approve,
                    usage_totals=turn_usage, **extra_kwargs
                )
                if done:
                    break
                steps += 1
                if steps >= max_steps:
                    get_display().error(
                        f"\n{yellow(f'(hit step limit of {max_steps}, stopping)')}"
                    )
                    break
        except KeyboardInterrupt:
            get_display().status("(interrupted)")
            cancelled = True

        # Accumulate session usage (even for interrupted turns)
        for key in ("input", "output", "cache_read", "cache_create"):
            self.session_usage[key] += turn_usage[key]

        if cancelled:
            return False, turn_usage

        # Extract last assistant text
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                c = msg.get("content")
                if isinstance(c, str):
                    self.last_response = c
                elif isinstance(c, list):
                    texts = [b["text"] for b in c
                             if b.get("type") == "text" and b.get("text")]
                    if texts:
                        self.last_response = "\n".join(texts)
                break

        # Update conversation and trim
        self.conversation = messages
        last_input = turn_usage.get("last_input", 0)
        old_len = len(self.conversation)
        self.conversation = trim_conversation(
            self.conversation, last_input, self.model, client=self.client
        )
        turn_usage["trimmed"] = old_len - len(self.conversation)
        if turn_usage["trimmed"] > 0:
            from llm_agent.debug import get_debug
            get_debug().log_trim(
                turn_usage["trimmed"], last_input,
                estimate_tokens(self.conversation),
            )

        # Auto-save session to disk
        if self._persist and self.conversation:
            self._save()

        return True, turn_usage

    def _save(self):
        """Persist the current session to disk."""
        if not self._session_path:
            self._session_path = session_path(self._session_id, self._started_at)
        try:
            save_session(
                self._session_path, self._session_id, self.model,
                self.conversation, self.session_usage,
                self._started_at, self._first_question,
            )
        except OSError:
            pass  # non-fatal: don't crash the agent if save fails

    def clear(self):
        """Clear conversation and session usage. Returns list of status messages."""
        self.conversation = []
        self.session_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0}
        self._file_observations.clear()
        return ["(conversation cleared)"]

    def _setup_delegate(self):
        """Configure tools that need session-specific runtime context."""
        from llm_agent.agents import load_all_agents, run_subagent
        from llm_agent.tools import TOOL_REGISTRY
        from llm_agent.tools import delegate, web_search
        from llm_agent.agent import invalidate_tool_cache

        agents = load_all_agents()

        # Update the delegate tool description with available agents
        agent_lines = []
        for name, defn in sorted(agents.items()):
            desc = defn.get("description", "")
            agent_model = defn.get("model") or "(inherits parent)"
            agent_max_steps = defn.get("max_steps", defn.get("max_turns", DEFAULT_SUBAGENT_MAX_STEPS))
            agent_lines.append(
                f"  - {name}: {desc} [model: {agent_model}, max_steps: {agent_max_steps}]"
            )
        agent_list = "\n".join(agent_lines)

        delegate.SCHEMA["description"] = (
            "Delegate a task to a specialized subagent that runs independently "
            "and returns agent/model/status metadata plus its findings. "
            "Use 'explore' for fast read-only research. Use 'code' for tasks "
            "that need file writes or commands. You may optionally set a "
            "per-run model override or run the subagent in the background "
            "for later inspection via check_task.\n\n"
            f"Available agents:\n{agent_list}"
        )

        invalidate_tool_cache()

        # Phase 2: set context on the registry entry (replaces monkey-patch)
        delegate_context = {
            "run_subagent": lambda agent_name, task, model_override=None, return_metadata=False: run_subagent(
                agent_name, task, self.client, self.model,
                self.auto_approve, thinking_level=self.thinking_level,
                model_override=model_override, return_metadata=return_metadata,
            ),
            "start_subagent": lambda agent_name, task, model_override=None: self._subagent_tasks.start(
                agent_name,
                task,
                self.client,
                self.model,
                self.auto_approve,
                thinking_level=self.thinking_level,
                model_override=model_override,
            ),
        }
        TOOL_REGISTRY["delegate"]["context"] = delegate_context
        TOOL_REGISTRY["check_task"]["context"] = {
            "subagent_tasks": self._subagent_tasks,
        }
        file_context = {"file_observations": self._file_observations}
        for tool_name in ("read_file", "read_many_files", "edit_file", "write_file", "apply_patch"):
            TOOL_REGISTRY[tool_name]["context"] = file_context
        TOOL_REGISTRY["web_search"]["context"] = web_search.build_context(
            self.client,
            self.model,
        )

    def _handle_model(self, text):
        """Handle /model command. Returns list of status messages."""
        from llm_agent.cli import make_client
        from llm_agent.models import (
            MODELS, DEFAULT_THINKING, is_ollama_model, provider,
        )

        parts = text.strip().split()
        messages = []

        if len(parts) == 1:
            messages.append(f"(model: {self.model})")
            messages.append(f"  available: {', '.join(MODELS.keys())}")
            messages.append(f"  or: ollama:<model-name> for local models")
            return messages

        # Resolve alias, pass through ollama:* names, or reject unknown
        arg = parts[1]
        if arg in MODELS:
            new_model = MODELS[arg]
        elif is_ollama_model(arg):
            new_model = arg
        else:
            messages.append(
                f"(unknown model '{arg}', available: {', '.join(MODELS.keys())})"
            )
            messages.append(f"  or: ollama:<model-name> for local models")
            return messages

        old_provider = provider(self.model)
        new_provider = provider(new_model)

        if new_provider != old_provider:
            self.client = make_client(new_model)
            self.conversation = []
            self._file_observations.clear()
            messages.append(f"(switched to {new_model}, conversation cleared)")
        else:
            messages.append(f"(switched to {new_model})")

        self.model = new_model

        # Apply per-model thinking default unless user has explicitly set one
        default_thinking = DEFAULT_THINKING.get(new_model)
        if default_thinking and not self.thinking_level:
            self.thinking_level = default_thinking
            messages.append(f"(thinking: {self.thinking_level})")

        self._setup_delegate()
        self.skills = load_all_skills()
        return messages

    def _handle_thinking(self, text):
        """Handle /thinking command. Returns list of status messages."""
        from llm_agent.models import is_gemini_model

        parts = text.strip().split()
        messages = []

        if len(parts) == 1:
            level = self.thinking_level or "off (model default)"
            messages.append(f"(thinking: {level})")
        elif parts[1] == "off":
            self.thinking_level = None
            messages.append("(thinking: off, model decides)")
        elif parts[1] in ("low", "medium", "high"):
            if not is_gemini_model(self.model):
                messages.append("(warning: --thinking is only supported for Gemini models)")
            self.thinking_level = parts[1]
            messages.append(f"(thinking: {self.thinking_level})")
        else:
            messages.append(f"(unknown thinking level '{parts[1]}', use low/medium/high/off)")

        return messages

    def _handle_mcp(self):
        """Handle /mcp command. Returns list of status messages."""
        try:
            from llm_agent.mcp_client import get_mcp_manager
            mgr = get_mcp_manager()
            if mgr._sessions:
                return ["MCP servers:", mgr.format_status()]
            else:
                return ["(no MCP servers connected)"]
        except Exception:
            return ["(MCP not available)"]

    def _handle_sessions(self):
        """Handle /sessions command. Returns list of status messages."""
        sessions = list_sessions(limit=15)
        if not sessions:
            return ["(no saved sessions)"]
        lines = ["Recent sessions:"]
        for s in sessions:
            started = s.get("started_at", "")[:16].replace("T", " ")
            q = s.get("first_question", "") or "(no question)"
            if len(q) > 60:
                q = q[:57] + "..."
            sid = s.get("session_id", "?")
            model = s.get("model", "?")
            lines.append(
                f"  {dim(f'[{sid}]')} {started}  {model}  {dim(q)}"
            )
        lines.append(dim(f"\n  Resume with: llm-agent --resume [ID]"))
        return lines

    def _handle_skills(self):
        """Handle /skills command. Returns list of status messages."""
        self.skills = load_all_skills()
        if self.skills:
            return ["Available skills:", format_skill_list(self.skills)]
        else:
            return ["(no skills found \u2014 add SKILL.md files in .skills/ or ~/.skills/)"]
