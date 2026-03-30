# Workspace Instructions

If the user explicitly says to stop responding, leave them alone, shut up, or send no further messages, the assistant must return an empty response and make no additional comments.

## Agent Instructions

This project uses a **guide chat** + **agent delegation** model:
- The guide chat holds architectural context and makes design decisions.
- Agents are spawned for specific implementation tasks with controlled scope.
- Agents should not make architecture decisions or redesign patterns — they execute.

## Codex Usage

When delegating to Codex:
1. Provide an instruction FILE that Codex reads and follows exactly.
2. Codex should STRICTLY follow instructions — no suggestions or commentary unless explicitly asked.
3. Codex is for routine/mechanical tasks with explicit specs.
4. Codex output goes to a file (e.g., `outputs/codex_audit_YYYY-MM-DD.md`).
5. If a task requires judgment calls, Claude handles it directly.
