---
name: tdd-ticket
description: Work one ticket end to end, test-first. Loads the ticket from the project's tracker or planning file, turns its acceptance criteria into a test list, stops for review, then runs red-green-refactor one test at a time with the project's own test command, and closes by walking the criteria and drafting decision and progress notes. Use when the user asks to start, implement or continue a ticket test-first, e.g. "/tdd-ticket ABC-42" or "let's do T-07".
argument-hint: "<ticket key or id>"
---

# TDD ticket

Work ticket `$ARGUMENTS` end to end, test-first.

## Learn the project first

Collect these from the project's and the user's `CLAUDE.md`, memory you hold about the project,
and the repo (README, `CONTRIBUTING`, task runner, CI). Ask once, in one message, only for what's
missing.

- **Where tickets live:** a tracker (Jira, Linear, GitHub or GitLab issues, via MCP tools or a
  CLI), or a planning file the project names (e.g. `TICKETS.md`, `ROADMAP.md`).
- **Decisions log:** where settled decisions are recorded (e.g. `DECISIONS.md`, ADRs under
  `docs/adr/`, a table in the planning file), and its row format.
- **Progress log:** a session log or status field the project keeps, if any.
- **Test commands:** how to run a single test, the fast suite, and the full suite including
  integration tests. Prefer the project's runner recipe (just, make, npm scripts, tox, nox, …)
  over a bare runner call.
- **Test conventions:** where tests live, naming, how integration tests are marked or separated,
  fixture style, and whether fakes or mocks are preferred.
- **Language rules:** if the planning docs and the code use different languages or naming,
  the mapping the project documents.

## Phase 1: Load

1. Read the ticket: description, acceptance criteria and comments, or its section in the
   planning file. If the source can't be read, stop and say why.
2. Read every decision-log entry that names the ticket. Those decisions are made; don't reopen
   them.
3. Check the dependencies the ticket or its tracker links name: confirm their deliverables exist
   in the repo. Report a missing one and stop, unless the user says to proceed anyway.
4. Read the code and data the ticket touches, and the existing tests for that area. Don't read
   unrelated modules or the progress log unless you're resuming a session.

## Phase 2: Plan, then STOP

Turn each acceptance criterion into test cases, one behavior per test, and present:

```
## $ARGUMENTS: <title>

### Tests, in the order they will be written
1. <test file>::<test name>   <- criterion N   [unit | integration]
...

### Files
- <path> (new | modify): <what>

### Decisions this ticket requires
- <choice>: proposed <value>, because <why>   (-> decisions log)

### Not covered by a test
- <criterion>: <how it will be verified instead>
```

Unit tests use synthetic data and no external services. Anything that needs a real service,
network or model is an integration test, marked or placed the way the project does it. Prefer a
fake over a mock unless the project's conventions say otherwise.

**STOP HERE.** Wait for the user to approve or change the plan. Write no code before that.

## Phase 3: Red, green, refactor, one test at a time

For each test in the approved order:

1. **Red.** Write that single test. Run only it with the project's single-test command. Confirm
   it fails for the right reason (a missing name or a wrong value), not a broken fixture or
   import.
2. **Green.** Write the least production code that passes it. Run it again.
3. **Refactor.** Remove duplication, fix names, keep the tests green. Run the fast suite.

Never write production code without a failing test. Never write several tests ahead. If a test
turns out to be wrong, fix the test first, then continue.

## Phase 4: Close

1. The project's checks (lint, types, fast suite) are green. If the ticket has integration
   tests, run them when their dependencies are reachable; otherwise say they were not run and
   why.
2. Walk the acceptance criteria and report each one: met (naming the test or artifact that
   proves it), not met, or a manual step for the user.
3. Draft, in the project's formats and with today's date:
   - one decisions-log entry per decision made;
   - the progress-log line, if the project keeps one;
   - the new ticket status.

   Propose them; apply them only when the user approves. Never transition a tracker ticket or
   edit control documents outside the repo without that approval.
4. Propose the commit message in the convention the user's or the project's instructions define
   (their `CLAUDE.md`, `CONTRIBUTING`, a commitlint config, or recent `git log` subjects), with
   the ticket key where the convention expects it. Commit only if the user asked for it.
