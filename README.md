# agentic

Claude Code skills for everyday engineering work. They work with any stack: each skill reads the
project's commands, conventions and environments from the repo and your `CLAUDE.md`.

## Install

```
/plugin marketplace add hugoantunes/agentic
/plugin install agentic@agentic
```

Run them inside Claude Code. Skills are then available as `/agentic:<skill>`.

Without the plugin system, run `cp -R skills output-styles ~/.claude/` from a clone, then use
`/<skill>`.

## Skills

| Skill | What it does | Side effects |
|---|---|---|
| `ticket-brief` | Gathers verified context on a ticket or PR before you plan it | None. Read-only |
| `pr-review` | Reviews a PR or diff and posts the findings as one GitHub review | One `COMMENT` review. Never approves |
| `local-review` | Reviews unpushed changes, applies the fixes, runs lint and tests | Working tree only. Never commits |
| `tdd-ticket` | Implements a ticket test-first, after you approve the test plan | Code and tests. Commits only if asked |
| `release-notes` | Writes release notes from what actually runs in production | DM to yourself on Slack, or printed |
| `review-watch` | Runs `pr-review` on each PR that requests your review, until midnight | One `COMMENT` review per PR, $15 cap each. Never approves |

```
/agentic:ticket-brief ABC-123
/agentic:pr-review 42 [--no-comment] [--approve]
/agentic:local-review [--no-tests]
/agentic:tdd-ticket ABC-123
/agentic:release-notes [2026-W42]
/agentic:review-watch [--slack-channel C0123456789] [--dry-run]
```

The plugin also adds the **Review Responder** output style (pick it with `/output-style`): it
checks each PR review comment before acting on it and drafts paste-ready replies.

## Requirements

- `git` and `python3`
- `gh` and `jq` for `pr-review`; `gh` and the `claude` CLI for `review-watch`
- Optional: a tracker (MCP server or CLI), an observability tool, `kubectl`, the Slack connector,
  and [Slack env vars](skills/review-watch/SKILL.md#settings) for `review-watch`. Skills skip
  what isn't available and say so.

## Project setup

Skills ask once for what they can't find. To skip the questions, add these to your project's
`CLAUDE.md`:

- tracker and project key
- related repos and where they're checked out
- environments, kube contexts, observability service names
- release model and feature-flag provider
- commit message convention

## My settings

My global setup, in [`settings/`](settings). Optional; the plugin doesn't load it.

- `CLAUDE.md`: response style, accuracy, code review, code style and shell rules. Copy to
  `~/.claude/CLAUDE.md`.
- `settings.json`: auto permission mode, a read-only allowlist, auto-mode rules, effort and UI
  preferences. Merge into `~/.claude/settings.json`.

## License

[MIT](LICENSE)
