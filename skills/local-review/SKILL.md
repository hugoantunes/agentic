---
name: local-review
description: Review local, not-yet-pushed changes the way pr-review reviews a PR, apply the verified findings to the working tree, then run the project's own quality gates and full test suite, so the user only reads the final diff, commits and pushes. Project-agnostic, it learns each repo's standards, gates and test command at run time. Posts nothing and never commits.
argument-hint: "[low|medium|high|xhigh|max] [path] [--branch] [--no-tests]"
disable-model-invocation: true
effort: high
allowed-tools: Skill, Read, Edit, Write, Grep, Glob, Bash(git diff *), Bash(git status *), Bash(git log *), Bash(git show *), Bash(git blame *), Bash(git rev-parse *), Bash(git grep *), Bash(git ls-files *), Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/local_review.py *)
---

# Local review

Review the changes the user hasn't pushed, fix what the review finds, and prove the result with
the project's own gates and tests. The working tree is the only output: the user reads
`git diff <before> <after>`, then commits and pushes.

## Ground rules

- **Working tree only.** Never commit, stage, stash, reset, checkout, rebase or push, and post
  nothing to a code host, chat or issue tracker. The script's snapshots are unreferenced commits:
  they don't move HEAD, the index or any ref.
- **The project's standards win.** Its instruction files, CI config and task runner decide how
  code is written and checked; the user's own `CLAUDE.md` fills the gaps. When the two conflict,
  follow the project and list the conflict in the report.
- **Change only what a finding or a failing check needs.** No drive-by refactors, renames or
  reformatting of lines nothing asked you to touch.
- **Change code with Edit or Write only**, never a shell script, heredoc or `sed -i`: Claude Code
  hooks fire only on Edit and Write, so a script edit skips the project's checks. The project's
  own formatters and generators are the exception.
- **Settled decisions stand.** When this conversation, or a plan doc the user points to, already
  decided something, a finding that would reverse it goes to **To confirm** naming the decision,
  and one the decision put out of scope is dropped with that reason.
- **Never weaken a gate to get green:** no new lint-suppression or type-ignore comments,
  skipped or expected-failure tests, baseline or exemption entries, or loosened config. The
  exception is an escape hatch the project's docs sanction for that exact case; name the doc in
  the report.
- **Stay local.** Run what the project documents for local use, including starting its own
  services the documented way (its compose file, its recipe). No system installs, container
  runtime or OS setting changes, remote environments or deploys. A gate that needs one is
  "not run" with the reason.
- **Report only what ran.** A gate or test you did not run is `not run — <reason>`, never a pass.
- **Keep it cheap.** Batch independent calls in one turn, read large files by section, and use
  the runner's quiet output flags (e.g. `-q --tb=short` for pytest, `--silent` for npm) so
  results stay small.

## 1. Scope and snapshot

Parse `$ARGUMENTS`: an effort level for `code-review` (none: let it reuse the last one), an
optional path that narrows the review (gates and tests still cover the whole change),
`--branch`, `--no-tests`.

Run, from the repo you're working in (add `--repo <dir>` when the session's working directory is
outside it, `--branch` when given):

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/local_review.py prepare
```

It snapshots the working tree, untracked files included, as the `before` commit and picks the
scope:

- uncommitted changes exist: those, against `HEAD`
- clean tree, unpushed commits: those, against the upstream
- `--branch`, or clean with nothing unpushed: the branch since it left the default branch
- nothing: print its line and stop

The diff under review is `git diff <base> <before>` (append `-- <path>` when a path was given).
Unlike plain `git diff`, it includes untracked files. Use it everywhere a diff is needed,
including the hunks in `<file context>`. When the script notes unpushed commits outside the
scope, say so in one line.

## 2. Learn the standards

The script's inventory lists instruction files (with their `##` headings), task-runner recipes,
hooks, the commands each CI job runs, and tool configs. From it, read:

- every listed instruction file and linked doc, by section, using the headings, and only the
  sections relevant to this change's language, layers and kind (tests, migrations, API, …);
- the directory instruction files listed for the changed paths, in full;
- the CI workflows that run on pull requests or pushes, enough to confirm what each gate runs.

Skip any file already in this conversation's context. Claude Code loads a directory's
`CLAUDE.md` on its own once you touch files under it. Memory you hold about this project counts
as standards (e.g. "tests run only in Docker").

Then keep a short working list in mind (not a file):

- **Conventions** the fixes must follow: naming, layering, error handling, test style, comment
  and docstring rules, import rules.
- **Gates**: CI check mapped to a local command, fastest first. Prefer the project's runner
  recipe (just, make, npm/pnpm/yarn script, tox, nox, cargo alias, …) over retyping the CI
  command, since recipes carry the project's config files and flags.
- **Full test suite**: command, prerequisites (services, env files, container memory), and the
  CI's parallelism flag.
- **Generated artifacts** CI checks for freshness, and the command that regenerates each.

Claude hooks listed under **Hooks** fire on their own when you edit; treat their complaints as a
failing gate.

## 3. Review

Invoke the `code-review` skill with the level (and path) from `$ARGUMENTS`, never `--comment` or
`--fix`. Follow its methodology for finding and verifying issues, with two overrides:

- **Target:** the diff is `git diff <base> <before>`, not what `code-review` would resolve on
  its own. Pass that exact command to every reviewer subagent it starts.
- **Reporting:** don't call `ReportFindings`; keep the verified findings for step 4.

Also:

- Flag changed lines that break a rule from step 2 as category `standards`, citing the rule's
  `file:line`. Only rules the project states, not taste.
- When the change crosses a boundary (a route's status codes and errors count), run "Check other
  repos when the change crosses a boundary" from `${CLAUDE_SKILL_DIR}/../pr-review/SKILL.md`.
  Either way, `## Checked and clean` names the repos searched, or why the check didn't apply.
- Classify each finding as **Must fix**, **To confirm** or **Optional** by that file's
  "Output format" definitions.

## 4. Apply

- Fix every **Must fix** and **Optional** finding. Leave **To confirm** unapplied: it turns on
  intent only the user knows, so it goes in the report as a question.
- Before each fix, re-read the surrounding code and follow the conventions list, so the fix reads
  like the rest of the module.
- For a behavior-changing **Must fix** where the project expects tests, add a regression test in
  the module's existing style; when practical, watch it fail before applying the fix.
- Drop a finding, with a one-line reason, when re-reading shows it's wrong, when it sits in code
  the diff doesn't change (pre-existing: worth a ticket, not a fix here), or when a settled
  decision rules it out.
- A fix that changes a public interface, schema or migration, or reaches files far from the diff,
  is not applied: move it to **To confirm** with the proposed fix.
- Run formatters only on in-scope files and files this run touched, never the whole repo, so
  unrelated churn stays out of the user's diff.

## 5. Gates

Run every gate that can run locally (lint, format check, types, import or smoke checks,
architecture or dependency checks), independent ones in parallel in one turn. For hooks: run
pre-commit-framework hooks with `pre-commit run --files <in-scope files>`; for plain git hook
scripts (husky, lefthook, `.git/hooks`), run the commands they call on the in-scope files, since
the scripts themselves check the index, which you don't touch.

- Fails on in-scope code (the diff or this run's fixes): fix and rerun that gate, at most two
  rounds per gate; still failing: report it with the error.
- Fails only outside the scope, judged by the file and line it points at: pre-existing; report,
  don't fix.
- Freshness check on a generated file: regenerate it with the project's command and list it under
  files to commit.
- Needs CI-only resources (secrets, remote services): `not run — CI-only`.

## 6. Tests

Skip with `--no-tests` (`Tests: not run — --no-tests`). Start after the gates, so the run sees
the final code.

- Run the full suite the way the project runs it locally, with its documented services, and the
  CI's parallelism flag when it works locally.
- Long runs: Bash with `run_in_background: true`, then wait for the completion notification. Do
  not poll.
- Read the outcome with one targeted search for the runner's summary line (e.g. pytest's final
  `passed`/`failed` line, jest's `Tests:` line, `go test`'s `ok`/`FAIL` lines), not `tail`: debug
  logging often fills the end of the output.
- The full suite can't run (prerequisite missing, environment won't start): run the touched
  modules' tests the documented way, and say exactly what ran and what blocked the rest.
- In-scope failure: fix it, rerun the failing tests and the fast gates; rerun the full suite only
  when the fix touched code shared beyond those tests.
- Failure outside the scope: rerun that test alone once. Still failing: `pre-existing`; passing:
  `flaky`. Both are inferences; label them so.
- Record passed, failed and skipped counts, and the duration.

## 7. Report

Run the script's `snapshot` command (same `--repo` as before) for the `after` commit;
`git diff <before> <after>` shows only this run's changes:

```
python3 -I ${CLAUDE_SKILL_DIR}/scripts/local_review.py snapshot
```

Write `local-review-<branch>.md` (slashes in the branch name become `-`) to the session
scratchpad (or the system temp directory when there is none) with `Write`:

- Header: repo, branch, scope, base, before, after.
- Findings in the format of "Output format" in `${CLAUDE_SKILL_DIR}/../pr-review/SKILL.md`
  (groups, continuous numbering, finding blocks, `<file context>` from `git diff <base> <before>`,
  the closing `## Checked and clean` line). After each `</comment>`, add a status line:
  `Status: applied`, `Status: applied + test <path>`, or `Status: not applied — <question>`.
- `## Dropped`: one line per dropped finding, `<claim> · path:line · <reason>`, where the reason
  is `wrong: …`, `pre-existing`, or `ruled out by <decision>`. Dropped findings get no block in
  the groups above, so every **To confirm** block still ends in a question.
- `## Gates`: each gate, its command, and `pass`, `fail` with the error, `pre-existing`,
  `regenerated`, or `not run — <reason>`.
- `## Tests`: command, counts, duration, and each failure with its status.
- `## Files to commit`: `git diff --name-status <before> <after>`.
- `## Conflicts with the user's CLAUDE.md`: only when there are any.

Print to the terminal only:

```
<n> fixes applied — `git diff <before> <after>` shows only this run's changes
1. <claim> — applied (`path:line`)
2. <claim> — applied + test (`tests/path`)
To confirm:
3. <the question>
Dropped: <n> — <reasons, one line>
Gates: <name> pass, <name> pre-existing (<one line>), <name> not run (<why>)
Tests: <passed> passed, <failed> failed, <skipped> skipped in <duration> (<command>)
Report: <absolute path>

Commit message:
<subject line>

- <bullet>
```

The commit message covers everything the user is about to commit (their change plus this run's
fixes). Follow the commit convention the user's or the project's instructions define (their
`CLAUDE.md`, `CONTRIBUTING`, a commitlint config, or the style of recent `git log` subjects);
with none, use Conventional Commits. When the convention includes a ticket key and the branch
name carries one, take it from the branch, uppercased (`abc-123-fix-login` → `ABC-123`); when the
convention needs one and the branch has none, leave a `<TICKET>` placeholder and say so. With no
findings, the first line is `No findings — checked <what>`, and the gates and tests still run.
