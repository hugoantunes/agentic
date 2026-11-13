---
name: ticket-brief
description: Gather verified context on a ticket (Jira, Linear, GitHub or GitLab issue) or a pull request so the work can be planned and implemented. Covers the ticket, its comments, epic and linked issues, the PR diff and review threads, the code they touch and, when it matters, live production state. Every claim is re-checked against its source before reporting, so the user doesn't have to ask "are you sure?". Use whenever the user pastes a ticket key or link, or a PR link or number, and asks to gather info, look into it, get context, understand it, or get ready to plan or implement it, even if they don't say "brief".
argument-hint: "<ticket key or URL | PR URL or number> [what to focus on]"
effort: high
allowed-tools: Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/check_refs.py *), Bash(python3 -I ${CLAUDE_SKILL_DIR}/scripts/find_deployments.py *)
---

# Ticket brief

Input: `$ARGUMENTS`

The brief gives the user enough verified facts to write a plan and implement it in one pass. It
is the input to a plan, not the plan. The usual failure is the user having to ask "are you sure?"
and then watching claims get corrected. The fix is to do that check yourself before reporting,
against sources rather than memory.

## Learn the project first

Before Step 1, collect what this skill needs to know about the project. Take it from the
project's and the user's `CLAUDE.md`, memory you hold about the project, and the repo itself
(README, deploy config, CI). Don't ask for anything you can find; ask once, in one message, for
what's still missing and blocks the brief.

- **Tracker:** which one (Jira, Linear, GitHub Issues, GitLab, …), its site or project, and how
  to reach it: an MCP server (load its tools via ToolSearch) or a CLI (`gh`, `glab`, `jira`,
  `linear`).
- **Code host:** the org or group, and where its repos are checked out locally (often siblings
  of the current repo).
- **Related repos:** the services, frontends, shared libraries and deploy-config repos that sit
  on the other side of this repo's boundaries.
- **Docs:** where plans, ADRs, PRDs and design notes live (the repo's `docs/`, a separate docs
  repo, a wiki).
- **Environments:** names of prod, staging and other environments; the kube contexts or
  platform accounts that map to them; the observability tool (Datadog, Grafana, New Relic,
  Sentry, CloudWatch, …) and the service and env names it uses.

## Ground rules

- **Read-only.** Don't edit code, commit, push, comment on or transition tickets, or comment on
  PRs. Don't write memory notes unless the user asks. The one exception is a proven observability
  query, which you may save as described in `references/observability.md`.
- **Never run risky code against production.** This applies to staging too, which is usually
  shared. A wrong read costs a re-run, but a wrong write to prod can't be undone. Never run, in
  any form:
  - SQL that writes: `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` or DDL;
  - application code that can have side effects: background jobs, queue tasks, model methods,
    management or CLI commands, migrations or scripts that write;
  - commands that change infrastructure state: for Kubernetes, `apply`, `patch`, `edit`,
    `delete`, `scale`, `rollout`, `cp` into a pod, or a restart; the equivalents on any other
    platform;
  - HTTP calls to prod APIs other than `GET`;
  - unbounded queries on large tables, which can load the prod database.

  If you can't tell whether a command is safe, don't run it. Give it to the user with the reason.
- **Keep it cheap.** Every turn re-reads the whole context, so the number of turns and the size
  of each read set the cost. One run read whole files and grew from 70k to 170k tokens; it
  auto-compacted before reporting, re-fetched evidence it had already read, and left its live
  numbers unverified.
  - **Batch lookups that don't depend on each other.** Send every lookup you already know you
    need in the same turn. Batching only removes waiting turns. Never skip a lookup or assume its
    result to save a turn: an unchecked assumption costs a follow-up round with the user, which
    costs more than the lookup.
  - **Read slices, not files.** At a git ref:
    - around a symbol: `git grep -n -A<k> -e <symbol> <ref> -- <path>`, or `-C<k>`;
    - a line range: `git blame -s -L <a>,<b> <ref> -- <path>`;
    - a whole file with `git show <ref>:<path>`, only when you know it's short, such as a small
      test.
  - **For docs and local files,** grep the key or the headings first (`grep -n -m5 -A3`), then
    read only the section you need with `sed -n 'a,bp'`.
  - **Request only the fields you need** from trackers and APIs.
  - Don't spawn subagents. The exception is a code survey that spans more than one repo or more
    than ~15 files: give that to one `Explore` subagent with `effort: "lower"`.
  - A subagent's findings are leads, not facts. Re-check every one in Step 4 before it goes in
    the brief. Relaying them unchecked is the most common source of wrong claims.
- **Write evidence down when you read it.** Every claim needs a source captured on the spot:
  - `file:line` and the line itself;
  - a ticket comment's author and date;
  - a PR hunk;
  - a command and its output.

  A claim with no captured evidence is an inference. Label it as one.
- **Each source settles a different question.**
  - Code settles what the system does.
  - The ticket settles what's wanted.
  - Live sources (the tracker, the code host) settle status.

  When the ticket and the code disagree, report the disagreement. It is often the most useful
  finding.
- **Read code at a known commit.** Local checkouts are often stale or on another branch.
  - Run `git fetch`, then read `origin/<default branch>`, or the PR head for a PR, with the slice
    reads above.
  - Record the short sha in the brief.
  - To explain how prod behaves today, read the code at the version prod is running, not the
    default branch. Run `git fetch --tags`, then read at `<tag>` the same way. Get the version
    from what's deployed (Step 3b), or else from the latest release tag. The default branch can
    differ from what prod runs.
  - If the ticket cites `file:line` or a sha, check those references against the current default
    branch. Report any that have moved.
- **Config in a repo is not live state.** Helm charts, Kustomize overlays, Terraform, env files
  and deploy manifests show what was asked for, not what runs. Replica counts, pool sizes,
  deployed versions and whether prod is affected need a live check: the platform's CLI,
  observability, or the database.
  - Step 3b says when to run that check and how.
  - If the check can't run, for example because the permission prompt was denied, label the claim
    inferred. Give the user the exact command to run with `!`.
  - Never write "prod is unaffected" or "nothing uses it" on the strength of having found no
    evidence.
- **Test the ticket's premises.** Tickets often get the cause, the scope or a count wrong. Check
  what the ticket asserts against code or data, the same way you check your own claims.
- **Hold your position when you have the evidence.** If the user later disputes a verified claim,
  re-check it. If it still holds, show the evidence rather than give way.

## Step 1: Resolve the input

**Do this step in two turns.**
- **Turn 1, in parallel:**
  - the ToolSearch for the tracker's tools (get issue, list comments, search), if it's an MCP;
  - `git fetch origin --tags`;
  - the prior-work `grep -rl`;
  - the related-PR search.
- **Turn 2, in parallel:**
  - the issue;
  - its comments;
  - the headings of any matching docs.

Then fetch the parent epic and all linked keys together.

**Ticket:**
- Get the summary, description, status, assignee, reporter, type, parent, labels, issue links,
  subtasks and **all comments**, in order. Later comments often change what the description asks
  for.
- Ask the tracker for a compact view or only the fields you need. Fetch custom fields only when
  the acceptance criteria aren't in the description, because they can sit in a custom field.
- **Parent epic or project:** get its summary and description. Epics often carry constraints that
  the ticket doesn't repeat.
- **Epic children:** if the input is an epic, or the work depends on sibling tickets, list every
  child with a query (e.g. JQL `parent = <EPIC>`). Don't sample them or go from memory, and give
  their count.
- **Linked issues:** these include every key mentioned in the description or comments, not only
  formal links.
  - Get each one's summary and status from the tracker in a single search call (e.g. JQL
    `key in (A, B, C)`), even when the ticket, a doc or a memory note already states it.
  - Go deeper only on "blocks" or "is blocked by" links, or when the description depends on the
    linked issue.
- **Related PRs:** look for PRs that cite the key, e.g.
  `gh search prs <KEY> --owner <org> --json repository,number,title,state,url`.
  - The search is fuzzy. Keep only hits whose title, body or branch contains the key.
  - When none do, report "N hits, none cite the key", not a bare "no PR".

**Pull request (GitHub shown; use the equivalent `glab` calls on GitLab):**
- `gh pr view <n> --repo <owner>/<repo> --json title,body,state,author,baseRefName,headRefName,headRefOid,mergedAt,files,reviews,comments,url`.
- Inline review threads:
  `gh api repos/<owner>/<repo>/pulls/<n>/comments --jq '.[] | {path, line, user: .user.login, body}'`.
- Take the ticket key from the title, branch or body, confirm it exists, then fetch it as above.
  PR titles cite wrong keys more often than you'd expect.

**Prior work:** run `grep -rl <KEY>` over the project's docs for the ticket and epic keys, plans,
PRDs and ADRs in particular.
- In a long doc, grep the headings and the key with line numbers first. Then read only the
  sections the ticket depends on. Reading a whole old plan to get one phase's task lines is a
  common, expensive mistake.
- Plans, records and PRDs often answer questions the ticket leaves open.
- Trackers' boards, docs and memory notes go stale. Treat them as leads, and confirm any status
  they show with the tracker.
- When a doc contradicts the ticket or the code, say so in the brief, under *What the ticket gets
  wrong* or *Decisions needed*. Don't drop it.
- When the ticket links a PRD, a wiki page or a chat thread, read it only if the requirements
  depend on it.

## Step 2: Extract the requirements

From the description and the comments, pull out:
- the problem, in one or two sentences;
- the acceptance criteria, verbatim;
- decisions made in comments. Note who made each one and when, and if two conflict, the later one
  wins;
- open questions nobody has answered;
- concrete values such as ids, counts, environments and flag keys.

## Step 3: Trace the code

For each requirement:
1. Find the entry point: a route or handler, a queue consumer or background job, a scheduled
   task, a CLI script, or a UI component.
2. Trace from there to the code that has to change.
3. Note each hop as `file:line`.

Also collect what implementation will need:
- **Tests:** the tests covering the area, and the fixture or setup to copy.
- **A pattern to copy:** an existing implementation of something similar.
- **Rules:** any `CLAUDE.md` or `AGENTS.md` in the touched directories.
- **Migrations:** the current head, if the schema changes.
- **Gates:** any feature flag that guards the path.
- **Other callers:** callers or consumers outside the obvious path, such as scripts and workers.
- **Other services:** whether the change crosses a service boundary, such as an API contract, an
  event, a shared table, auth, or deploy config. If it does, check the related repos on the other
  side. Skipping a related repo is a repeated gap.
  - Fetch each repo first, then search `origin/<default branch>`: `git -C <repo> fetch`, then
    `git -C <repo> grep -n <pattern> origin/<default>`.
  - A local working tree can be stale, so a hit or a miss in one doesn't count.

For a PR, read each changed hunk together with its surrounding code at the PR head. A hunk on its
own doesn't tell you what it changes in behavior.

**Running code to confirm behavior.** If a local script can't import the project, don't fix the
environment: copy the minimal code into the script, and say in the brief that you ran a copy.
Working around a missing system library can easily cost four calls.

**Trace every option you'll present.** Do this when the brief will recommend an option, or list
options under *Decisions needed* (finish or retire, fix it here or there). Trace each option the
way you trace a requirement, and find:
- **What it changes or deletes,** and who else calls that code: scripts, workers, other repos.
- **What crosses a process boundary** and can break during a rolling deploy or with a backed-up
  queue: job or task names and arguments, queued messages and events, API payloads, DB columns,
  cache keys. Old instances keep sending the old shape to new ones.
- **What has to happen in what order:** a migration, a flag change, a deploy in another repo.

After a recommendation, the user's next question is "is it safe?". Answer it in the brief, in
one clause (see Step 5). Keep the evidence, so a request to dive deeper needs no new lookups.
Skipping this tends to cost a follow-up round, and that round is where hazards like a renamed
job argument surface.

Stop reading once, for each requirement and each option, you can say:
- what changes;
- where it changes;
- how it will be tested;
- what can break.

Anything beyond that costs tokens and adds nothing to the brief.

## Step 3b: Check live state (logs, errors, deployments)

**When to run it.** Run this step when any of these is true:
- the ticket is a bug;
- it mentions errors, prod, specific ids or counts, or performance;
- it touches scaling, deploy config, workers or infra;
- a claim in the brief depends on what is actually running.

Otherwise skip it, and list it under *Not checked* in one line. Keep the checks narrow: the
services, resources and error messages the ticket names, over a window of 7–14 days unless the
ticket gives one. Run the observability and platform checks in the same turn.

**Observability: metrics, traces, logs, error tracking.**
- Read `references/observability.md` from this skill's directory in the same turn you load the
  observability tools. It says which source answers which question and has query shapes that
  already work. Check memory for queries already proven on this project.
- For the error or path in the ticket, get:
  - request and error counts per environment, from unsampled metrics;
  - first and last seen, and the tenants, accounts or users affected, from traces or logs;
  - one trace or log line that shows the failing code path.
- Confirm or contradict the ticket's numbers and ids.
- **Traces and logs are usually sampled.** A sample can miss known events.
  - Use traces and logs for the shape of the errors, timing and who is affected.
  - Take request and error counts from metrics that count every request. Take row counts from the
    database (below).
  - "No traffic" or "unused" needs an empty unsampled-metric result over a stated window, from a
    metric name you've confirmed exists.
- A cause counts as verified only once a trace or log line ties it to the code path from Step 3.
- If a query returns nothing, report "no matching events in <window>". That is not evidence that
  something is unaffected.

**Platform: deployments, instances, versions. Read-only.** Kubernetes shown; use the read-only
equivalents on any other platform (ECS, Cloud Run, Heroku, Fly, Nomad, VMs).
- **Always pass `--context`.** The current context is often not the one you think.
- **Find deployments with the bundled script.** It checks every context you pass in one call:
  `python3 -I ${CLAUDE_SKILL_DIR}/scripts/find_deployments.py <name-part> [<name-part> ...] --context <ctx> [--context <ctx> ...]`
  - It prints only the matches, with namespace, ready/desired replicas and image tag. Namespaces
    can differ per cluster.
  - For a name with no match, it says how many deployments it checked.
  - Never list every deployment into the context; three full listings can take 26k characters.
- **Then get what the brief needs:**
  - replicas, ready pods and restarts: `kubectl get deploy,pods -n <ns> --context <ctx> -o wide`
  - the running image: `-o jsonpath='{.spec.template.spec.containers[*].image}'`
  - specific env or config values: jsonpath on those fields only
- **Use `get`, `describe`, bounded `logs`, and `exec` for reads only.** `logs` needs `--since` or
  `--tail`, and never `-f`. With `exec`, only inspect:
  - specific env vars: `printenv <NAME>`, never a full `env` dump;
  - files and process state;
  - the database, through read-only SQL as described below.
- **Don't expose secrets.** Never run `get secret`, never dump a full deployment with `-o yaml`
  (env values can hold secrets), and never print credentials.

**Database: read-only SQL.**
- **Use the bundled template (PostgreSQL).**
  1. Copy `${CLAUDE_SKILL_DIR}/scripts/readonly_query.py` into the scratchpad.
  2. Put the SQL in `QUERY`, and set `DSN_ENV_VAR` if the app keeps its DSN under another name.
  3. Run it where the credentials already live, e.g.
     `kubectl exec -i deploy/<app> -c <container> -n <ns> --context <ctx> -- python - < <copy>`.

  The template opens a read-only session, so the database rejects any write, and sets a 120 s
  statement timeout. Don't write a new connection script. For another database, get the same two
  guarantees (read-only session or role, statement timeout) before running anything.
- **Expect a permission prompt.** Auto-mode safety checks treat a prod `exec` as a production
  read, and can deny one even after the user has given a general go-ahead.
  - Show the query and ask the user to confirm explicitly, for example "run it now".
  - If it's still denied, give them the command to run with `!`.
- **Write the queries to be safe:**
  - `SELECT` only;
  - filter by an indexed column, or use `LIMIT`;
  - use counts and samples rather than full dumps;
  - never select password, token or secret columns.
- **Show the SQL in the brief** next to its result.

Record the environment, the time window and the query or command next to every live fact.
Without them, the fact can't be re-checked in Step 4.

## Step 4: Verify before reporting

This is the "are you sure?" pass, done before the user has to ask.

1. **List every claim** the brief will make. Next to each, write the source you captured for it.
2. **Re-check each claim with a fresh lookup, all in one turn.** Re-run the search, re-read the
   exact lines at the recorded sha, re-read the comment. Don't re-reason from memory. Reasoning
   about an earlier conclusion tends to confirm it, while a lookup can contradict it. In that one
   turn, send:
   - **Every code claim in one call per repo:**
     `python3 -I ${CLAUDE_SKILL_DIR}/scripts/check_refs.py <sha> [--repo <dir>] "path:line:text" "path:a-b:text" "path::text" ...`
     It prints OK, MOVED (with the new lines), MISSING or NO FILE for each claim.
   - the repo-wide searches behind universal claims;
   - the tracker and code-host status lookups;
   - the live checks that a recommendation rests on.
3. **Watch these claim types closely.** They are where errors usually hide:
   - **Names.** Every path, function, class, field, endpoint, table, flag key and ticket key must
     exist exactly as written. Check each with `git grep -n` at the sha, or with the tracker.
   - **Behavior.** "X does Y" must come from reading X's code, not from its name, its docstring or
     the ticket.
   - **Universal claims.** "Only", "all", "never", "nothing else calls" and "unused" need a
     repo-wide search. State the search you ran. Check other repos too when they could plausibly
     consume the code.
   - **Status and dates.** Ticket status, PR state, merge state and the release something shipped
     in come from a live lookup, not from earlier in the session or a local doc.
   - **Live state.** Anything about what runs in an environment needs a live check, not a config
     file. See the ground rules.
   - **Root causes.** A cause matched from an error class or a fuzzy log match is inferred until a
     trace, a log line or the code path confirms it.
   - **Numbers.** Every number needs a source you can cite.
   - **The ticket's own claims.** Each one is either confirmed or contradicted, with evidence.
4. **Act on the result.** Fix wrong claims. Move claims you can't prove to *Inferred*. Drop claims
   that don't affect planning.

## Step 5: Report in chat

Keep it tight: facts that change the plan, not a narration of what you read. The first line has
to stand on its own.
- **Aim for 700 words or fewer.** The final message drives output tokens, which cost the most per
  token.
- **State each fact once,** in the section where it changes the plan. Don't list the same files
  under two headings.
- **Link `file:line`** rather than quoting code.
- **Present decisions as a TL;DR.** Give each one a single line: the recommendation in bold, why,
  the main risk, and the alternative, in a few words each. Write no paragraphs. The user asks to
  go deeper on the ones they care about, and you answer from the evidence you already captured.

Use this template:

```
**<KEY>: <one-line verdict, e.g. "under-scoped: the FK fix also needs a migration">**
<summary> · <status> · <assignee> · <repo> @ `<sha>` · <PR #n state, if any>

<2–3 sentences: what has to be done and why, in plain words.>
**Ready to plan:** yes / no, blocked by <decision or check>

**What the ticket gets wrong**
- <premise>: <what the code or data shows instead> — <evidence>

**Requirements**
- Acceptance criteria, verbatim
- Decisions from comments (who, date)

**Verified facts**
- <fact> — `<file:line>` / comment by <name> on <date> / PR #<n>

**Live state** (<envs>, <time window>)
- <fact> — <query or command>

**Where the change goes**
- <file:function>: <what changes>
- Tests: <file>, using <fixture/pattern> from <file:line>
- Pattern to copy: <file:line>

**Inferred, not verified**
- <claim>: <what would confirm it>

**Decisions needed**
1. <question>? **<recommended option>**: <why>. Risk: <main risk, or "none found">. Alternative: <option>, which <costs or implies>.

**Not checked:** <sources you skipped and why, such as prod DB, other repos or chat, with the command that would check each>
**Verification:** <n> claims re-checked; corrected: <what was wrong, or "none">.
```

Leave out any section that would be empty. Don't write the plan. The user asks for it next, and
the brief is what the plan builds on.
