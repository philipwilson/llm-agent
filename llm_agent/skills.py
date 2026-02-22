"""Skill system: reusable prompt templates invoked as /slash commands."""

import os
import re
import subprocess

from llm_agent.tools.base import shell


def parse_skill(path):
    """Parse a SKILL.md file with YAML frontmatter.

    Returns a dict with 'name', 'description', 'argument-hint', 'body',
    and 'path', or None if parsing fails.
    """
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return None

    # Expect --- delimited frontmatter
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None

    frontmatter = text[3:end].strip()
    body = text[end + 4:].strip()  # skip past "\n---"

    skill = {"body": body, "path": path}
    for line in frontmatter.splitlines():
        line = line.strip()
        if not line:
            continue
        colon = line.find(":")
        if colon < 0:
            continue
        key = line[:colon].strip()
        value = line[colon + 1:].strip()
        skill[key] = value

    if "name" not in skill:
        return None

    return skill


def load_all_skills():
    """Scan ~/.skills/ and .skills/ for subdirectories containing SKILL.md.

    Project-level (.skills/) takes priority over user-level (~/.skills/).
    Returns {name: skill_dict}.
    """
    skills = {}
    dirs = [
        os.path.expanduser("~/.skills"),
        os.path.join(os.getcwd(), ".skills"),
    ]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            skill_dir = os.path.join(d, entry)
            if not os.path.isdir(skill_dir):
                continue
            skill_file = os.path.join(skill_dir, "SKILL.md")
            if not os.path.isfile(skill_file):
                continue
            skill = parse_skill(skill_file)
            if skill:
                skills[skill["name"]] = skill
    return skills


def render_skill(skill, args_string):
    """Render a skill body with variable substitution and dynamic injection.

    Variable substitution:
      $ARGUMENTS  -> full args string
      $0, $1, ... -> positional args (replaced in descending index order)

    Dynamic injection:
      Lines matching  !`command`  are replaced with the command's stdout.
    """
    body = skill["body"]

    # Variable substitution
    args = args_string.split() if args_string else []
    body = body.replace("$ARGUMENTS", args_string)
    # Replace positional args in descending order to avoid $1 matching inside $10
    for i in sorted(range(len(args)), reverse=True):
        body = body.replace(f"${i}", args[i])

    # Dynamic injection: lines matching  !`command`
    lines = body.splitlines()
    result = []
    for line in lines:
        m = re.match(r"^\s*!`(.+)`\s*$", line)
        if m:
            cmd = m.group(1)
            try:
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True,
                    timeout=5, cwd=shell.cwd,
                )
                output = proc.stdout.strip()
                if proc.returncode != 0 and proc.stderr.strip():
                    output = proc.stderr.strip()
                result.append(output)
            except subprocess.TimeoutExpired:
                result.append(f"(command timed out: {cmd})")
            except OSError as e:
                result.append(f"(command failed: {e})")
        else:
            result.append(line)

    return "\n".join(result)


def format_skill_list(skills):
    """Format skills for display."""
    lines = []
    for name in sorted(skills):
        skill = skills[name]
        desc = skill.get("description", "")
        hint = skill.get("argument-hint", "")
        usage = f"/{name} {hint}".rstrip()
        lines.append(f"  {name} — {desc}  ({usage})")
    return "\n".join(lines)
