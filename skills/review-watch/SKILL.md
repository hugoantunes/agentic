---
name: review-watch
description: Watch for PRs that need your review for the rest of the day and review each new one with pr-review in its own headless Claude Code session. Polls your GitHub review requests, and optionally a Slack channel for PR links, every 10 minutes, and reports when each review starts and its verdict, by Slack DM or in the task output. Never approves.
argument-hint: "[--slack-channel <id>] [--workdir <dir>] [--review-skill </skill>] [--budget <usd>] [--dry-run]"
disable-model-invocation: true
allowed-tools: Read, Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/poll.py *), Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/notify.py *)
---

# Review watch

`poll.py` polls without the model and runs each review in its own headless `claude -p` session,
so this session never wakes for a PR and its context stays small. It wakes only when the script
exits: at midnight, on a stop signal, or on failure. The script dies with the session.

## Start

1. With `--dry-run` in `$ARGUMENTS`: run `python3 -I ${CLAUDE_SKILL_DIR}/scripts/poll.py $ARGUMENTS`
   in the foreground, print its output (the reviews it would run, or why it failed), and stop.
2. Otherwise run the same command with Bash `run_in_background: true`.
3. Print one line: the background task id, and that it polls every 10 minutes until midnight,
   reviews each new PR in a headless session at an effort set by its size (under 200 changed
   lines `medium`, under 600 `high`, else `xhigh`), reports when each starts and its verdict,
   and stops with the session.

## When the poller exits

Read its whole output (stdout and stderr) and act on the first case that matches:

- **`day over`:** print `Review watcher stopped for the day.`
- **`already running as pid <n>`:** print that another poller already watches, and do not
  start one.
- **`stopped by signal`:** print it; someone stopped the poller on purpose.
- **Anything else** (an `error:` line, a traceback, no output): run
  `python3 -I ${CLAUDE_SKILL_DIR}/scripts/notify.py 'Review watcher stopped: <the error, one line>'`,
  print the same line, and do not restart. When the error names a missing setting below, say
  which one to set.

## Settings

Needs `gh` (authenticated) and `claude` on `PATH`. Reviews run from the directory the session
started in, or `--workdir`; start it where your repos are checked out so `pr-review` can check
consumers in sibling repos.

Slack is optional. Read from the environment:

- `REVIEW_WATCH_SLACK_BOT_TOKEN`: bot token with `chat:write`, plus `channels:history` (or
  `groups:history` for a private channel) to watch a channel the bot was invited to.
- `REVIEW_WATCH_SLACK_USER_ID`: your Slack member ID; notices go to the bot's DM with you.
- `REVIEW_WATCH_SLACK_CHANNEL`: channel ID to watch for PR links, instead of `--slack-channel`.
- `REVIEW_WATCH_SLACK_USER_TOKEN`: user token with `reactions:write`, to put your 👀 on the
  message that asked for a review while it runs.

Without them, notices go only to the background task's output. Reviews never see these
variables.
