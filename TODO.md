# Future Improvements

## Usability

- **Conversation reset** — A `/clear` command to reset history without restarting the program.
- **Colour output** — ANSI colours to visually distinguish model text, command proposals, and command output.

## Architecture

- **Persistent shell session** — Use `pexpect` or a long-lived `subprocess.Popen` so `cd`, `export`, and other stateful commands persist between invocations.
- **System prompt from file** — Move `SYSTEM_PROMPT` to an external file so it can be iterated on without editing Python.

## Safety

- **Sandbox option** — Run commands inside a Docker container so the agent can't damage the host filesystem.
