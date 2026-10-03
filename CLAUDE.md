@AGENTS.md

## Claude Code notes

- Project skills live in `.claude/skills/`: `new-domain`, `new-migration`,
  `open-pr`, `deploy-debug`, `sync-contracts`, `new-permission`. Use them for those tasks.
- Before claiming work is done, run the four checks from "Commands" and show
  their result.
- Commits and PRs carry no AI attribution: no `Co-Authored-By: Claude` /
  Anthropic trailers, no "Generated with Claude Code" lines.
- Never print secrets (tokens, passwords, `.env` values) into the terminal,
  commits, PR text or logs; refer to them by name.
- The deploy host (`dellpromaxgb10`) is shared: only touch resources named
  `tuttitrip-*` / labelled `tuttitrip.managed=true`.
