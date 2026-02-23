---
name: skill-creator
description: Guide for creating effective skills for llm-agent. This skill should be used when users want to create a new skill (or update an existing skill) that extends the agent's capabilities with specialized knowledge, workflows, or tool integrations.
---

# Skill Creator

This skill provides guidance for creating effective skills for llm-agent.

## About Skills

Skills are reusable prompt templates invoked as `/slash` commands in interactive mode. They transform the agent from a general-purpose assistant into a specialized one equipped with procedural knowledge for specific domains or tasks.

### How Skills Work in llm-agent

Each skill lives in a subdirectory under `.skills/` (project-level) or `~/.skills/` (user-level), containing a `SKILL.md` file:

```
.skills/
    my-skill/
        SKILL.md
```

Project-level skills (`.skills/`) take priority over user-level ones (`~/.skills/`).

### SKILL.md Format

Every SKILL.md has YAML frontmatter with `---` delimiters, followed by a markdown body:

```yaml
---
name: my-skill
description: What this skill does and when to use it
argument-hint: <filepath>
---
Your prompt template body goes here.

File: $0
Branch: !`git branch --show-current`
```

**Frontmatter fields:**
- `name` (required) - The slash command name (e.g., `name: review` -> `/review`)
- `description` - Explains what the skill does; helps users find it via `/skills`
- `argument-hint` - Shows usage hint (e.g., `<filepath>`)

**Variable substitution in the body:**
- `$ARGUMENTS` - Expands to the full args string
- `$0`, `$1`, etc. - Positional args (space-split)

**Dynamic injection:**
- Lines matching `` !`command` `` are replaced with the command's stdout at invocation time (5-second timeout)

### Available Tools

When a skill is invoked, the agent has these tools available:
- `read_file` - Read file contents
- `write_file` - Create or overwrite files
- `edit_file` - Targeted find-and-replace in files
- `run_command` - Execute shell commands
- `search_files` - Regex search over file contents
- `glob_files` - Find files matching glob patterns
- `list_directory` - List directory entries
- `read_url` - Fetch web pages
- `web_search` - Search the web
- `delegate` - Spawn subagents for subtasks

## Core Principles

### Concise is Key

The context window is shared with everything else. Only add context the LLM doesn't already have. Challenge each piece of information: "Does this paragraph justify its token cost?"

Prefer concise examples over verbose explanations.

### Set Appropriate Degrees of Freedom

**High freedom (text-based instructions)**: Use when multiple approaches are valid and decisions depend on context.

**Medium freedom (pseudocode or step-by-step)**: Use when a preferred pattern exists but some variation is acceptable.

**Low freedom (specific commands/scripts)**: Use when operations are fragile, consistency is critical, or a specific sequence must be followed.

## Skill Creation Process

### Step 1: Understand the Skill

Clearly understand how the skill will be used. Ask:
- What tasks should this skill handle?
- What would a user type to invoke it?
- What examples demonstrate the expected behavior?

### Step 2: Plan the Contents

Analyze each example to identify:
- What scripts, references, or assets would help?
- What procedural knowledge does the LLM need?
- What's the right level of freedom for each part?

### Step 3: Create the Skill

Create the directory and SKILL.md:

```bash
mkdir -p .skills/my-skill
```

Then write the SKILL.md file with:
1. YAML frontmatter (`name`, `description`, optional `argument-hint`)
2. Markdown body with instructions

### Step 4: Write the SKILL.md

**Frontmatter:**
- `name`: The slash command name
- `description`: This is the primary triggering mechanism. Include both what the skill does and specific triggers/contexts for when to use it.

**Body:**
- Write instructions for the agent to follow when the skill is invoked
- Include code examples, step-by-step workflows, and decision trees
- Reference tools by their llm-agent names (`read_file`, `write_file`, `edit_file`, `run_command`, etc.)
- Use variable substitution (`$ARGUMENTS`, `$0`, `$1`, etc.) for user inputs
- Use dynamic injection (`` !`command` ``) for runtime context

### Step 5: Test the Skill

Run `llm-agent` and test:
```bash
llm-agent --no-tui
# Then type:
/skills           # Verify the skill appears in the list
/my-skill args    # Test invocation
```

### Step 6: Iterate

After testing with real tasks:
1. Notice struggles or inefficiencies
2. Identify how SKILL.md should be updated
3. Implement changes and test again

## Example Skill

Here's a complete example of a code review skill:

```yaml
---
name: review
description: Code review a file for bugs, style issues, and improvements
argument-hint: <filepath>
---
Review the following file for bugs, style issues, and potential improvements.

File to review: $0
Current branch: !`git branch --show-current`
Recent changes: !`git diff --stat HEAD~3`

Focus on:
1. Logic errors and edge cases
2. Security vulnerabilities
3. Performance issues
4. Code style and readability
5. Missing error handling

Provide specific, actionable feedback with line references.
```

## What NOT to Include

- README.md, CHANGELOG.md, or other documentation files
- Installation guides or setup procedures
- Information the LLM already knows (common programming patterns, standard library usage)
- Deeply nested reference file hierarchies