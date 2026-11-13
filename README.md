# agentic

Claude Code skills for everyday engineering work. They work with any stack: each skill reads the
project's commands, conventions and environments from the repo and your `CLAUDE.md`.

## Install

```
/plugin marketplace add hugoantunes/agentic
/plugin install agentic@agentic
```

Run them inside Claude Code. Skills are then available as `/agentic:<skill>`.

Without the plugin system: `cp -R skills/* ~/.claude/skills/` from a clone, then use `/<skill>`.

## Skills

| Skill | What it does | Side effects |
|---|---|---|
| `ticket-brief` | Gathers verified context on a ticket or PR before you plan it | None. Read-only |
| `pr-review` | Reviews a PR or diff and posts the findings as one GitHub review | One `COMMENT` review. Never approves |
| `local-review` | Reviews unpushed changes, applies the fixes, runs lint and tests | Working tree only. Never commits |
| `tdd-ticket` | Implements a ticket test-first, after you approve the test plan | Code and tests. Commits only if asked |
| `release-notes` | Writes release notes from what actually runs in production | DM to yourself on Slack, or printed |

```
/agentic:ticket-brief ABC-123
/agentic:pr-review 42 [--no-comment] [--approve]
/agentic:local-review [--no-tests]
/agentic:tdd-ticket ABC-123
/agentic:release-notes [2026-W42]
```

## Requirements

- `git` and `python3`.
- `gh` and `jq` for `pr-review`.
- Optional: a tracker (MCP server or CLI), an observability tool, `kubectl`, and the Slack
  connector. Skills skip what isn't available and say so.

## Project setup

Skills ask once for what they can't find. To skip the questions, add these to your project's
`CLAUDE.md`:

- tracker and project key
- related repos and where they're checked out
- environments, kube contexts, observability service names
- release model and feature-flag provider
- commit message convention

## License

[MIT](LICENSE)
