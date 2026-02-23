"""Project context detection: auto-detect project type, git status, and convention files."""

import os
import subprocess


def _run_git(args, cwd=None, timeout=2):
    """Run a git command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _detect_project(cwd):
    """Detect the project type from config files in cwd. Returns a brief string or None."""
    checks = [
        ("pyproject.toml", _parse_pyproject),
        ("setup.py", lambda p: "Python project (setup.py)"),
        ("package.json", _parse_package_json),
        ("Cargo.toml", _parse_cargo),
        ("go.mod", _parse_go_mod),
        ("Gemfile", lambda p: "Ruby project (Gemfile)"),
        ("CMakeLists.txt", lambda p: "C/C++ project (CMakeLists.txt)"),
        ("Makefile", lambda p: "Project with Makefile"),
    ]
    for filename, parser in checks:
        path = os.path.join(cwd, filename)
        if os.path.isfile(path):
            try:
                return parser(path)
            except Exception:
                return f"Project ({filename})"
    return None


def _parse_pyproject(path):
    """Extract project name from pyproject.toml."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("name") and "=" in line:
                    name = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return f"Python project: {name} (pyproject.toml)"
    except Exception:
        pass
    return "Python project (pyproject.toml)"


def _parse_package_json(path):
    """Extract project name from package.json."""
    import json
    try:
        with open(path) as f:
            data = json.load(f)
        name = data.get("name", "")
        scripts = ", ".join(list(data.get("scripts", {}).keys())[:5])
        parts = [f"Node.js project: {name}" if name else "Node.js project"]
        if scripts:
            parts.append(f"scripts: {scripts}")
        return " (package.json, " + ", ".join(parts[1:]) + ")" if len(parts) > 1 else parts[0] + " (package.json)"
    except Exception:
        return "Node.js project (package.json)"


def _parse_cargo(path):
    """Extract project name from Cargo.toml."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("name") and "=" in line:
                    name = line.split("=", 1)[1].strip().strip('"').strip("'")
                    return f"Rust project: {name} (Cargo.toml)"
    except Exception:
        pass
    return "Rust project (Cargo.toml)"


def _parse_go_mod(path):
    """Extract module name from go.mod."""
    try:
        with open(path) as f:
            first_line = f.readline().strip()
            if first_line.startswith("module "):
                module = first_line[7:].strip()
                return f"Go project: {module} (go.mod)"
    except Exception:
        pass
    return "Go project (go.mod)"


def _detect_git(cwd):
    """Detect git context. Returns a formatted string or None."""
    branch = _run_git(["branch", "--show-current"], cwd=cwd)
    if not branch:
        # Not a git repo or git not available
        return None

    parts = [f"Git branch: {branch}"]

    status = _run_git(["status", "--short"], cwd=cwd)
    if status:
        dirty_count = len(status.splitlines())
        parts.append(f"{dirty_count} uncommitted change{'s' if dirty_count != 1 else ''}")

    log = _run_git(["log", "--oneline", "-5"], cwd=cwd)
    if log:
        parts.append(f"Recent commits:\n{log}")

    return "\n".join(parts)


def _load_convention_file(cwd):
    """Load AGENTS.md convention file if it exists. Returns contents or None."""
    path = os.path.join(cwd, "AGENTS.md")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except Exception:
            pass
    return None


def detect_project_context(cwd=None):
    """Detect project type, git context, and convention file.

    Returns a formatted string to append to the system prompt, or empty string.
    """
    if cwd is None:
        cwd = os.getcwd()

    sections = []

    project = _detect_project(cwd)
    if project:
        sections.append(project)

    git = _detect_git(cwd)
    if git:
        sections.append(git)

    convention = _load_convention_file(cwd)
    if convention:
        sections.append(f"Project instructions (AGENTS.md):\n{convention}")

    if not sections:
        return ""

    return "Project context:\n" + "\n".join(sections)
