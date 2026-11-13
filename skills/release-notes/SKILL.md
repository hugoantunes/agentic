---
name: release-notes
description: Build stakeholder release notes for a period (default the current week) for product, sales and support. Covers what went live for customers, what's coming, and how production did, with an owner per item. Every status is proven from what actually runs in production (deployed versions, feature-flag values, live traffic), never from tracker status or merged PRs. Sends the post to the runner's own Slack DM for review when Slack is connected, otherwise prints it. Use when asked for release notes, a weekly release report, or "what shipped this week".
argument-hint: "[period, e.g. 2026-W42 or 2026-10-01..2026-10-14; defaults to the current week] [repo ...]"
effort: high
---

# Release notes

This skill produces one post for non-engineers. It has three sections: what went live for
customers, what's coming, and how the platform did. It is not an engineering changelog.

Input: `$ARGUMENTS`. The period defaults to the current week, Monday 00:00 in the user's timezone
→ now. Repos named in the input override the defaults below.

## Learn the project first

Collect these from the project's and the user's `CLAUDE.md`, memory you hold about the project,
and the repos themselves (README, CI workflows, deploy config). Ask once, in one message, only for
what's missing and blocks the post.

- **Repos:** the user-facing services and apps the notes cover, and libraries that reach users
  only through them. Their code-host org and local checkouts.
- **Release model:** how a version reaches each environment. Learn it from tags and CI: e.g. a
  plain semver tag means prod and `-rc.N` means staging; or merge to `main` deploys; or a release
  branch.
- **Deploy evidence:** where to see what actually runs: Kubernetes image tags (`kubectl` or the
  observability tool's container metrics), a GitOps controller's history, or the platform's
  release list (ECS, Cloud Run, Heroku, Vercel, Fly, …).
- **Environments and regions:** prod environments and clusters, and which of them the
  observability KPIs cover.
- **Feature flags:** the provider (OpenFeature, LaunchDarkly, Unleash, Flagsmith, ConfigCat, a
  homegrown table) and how to read a flag's prod value.
- **KPIs:** the service and env names in the observability tool; the request metric; the
  project's key operation for the usage line (e.g. checkouts, AI requests, documents processed);
  any documented error-rate method.
- **Audience and tone:** who reads it, and the product name to put in the title.

## Ground rules

- **Read-only.** Never edit, commit, push or tag in any repo, and never change a flag.
- **Every claim must survive a prod check.** The post goes out under the user's name, and an
  unverifiable claim makes them look like they invented it. If you can't reproduce a number or a
  claim, drop it.
- **Confirm reach, not only the deploy.** A feature counts as "live" only if prod can reach it.
  An endpoint with zero prod traffic because the frontend doesn't call it yet is not a customer
  change. A guard that fires 0 times is still live, but say "no customer has hit it yet".
- **A tag is not a deploy.** A release tag, a merged PR or a version-bump commit in a deploy repo
  says what was asked for. Take go-live from what runs (Step 2). In one case a version was tagged
  at 10:03 and ran in prod only from about 20:00, because the deploy was a manual sync.
- **Use the exact incident window, never a rounded-up one.** Take it from per-minute error
  counts.
- **Never guess people's names.** Take the code host's display name (e.g.
  `gh api users/<login> --jq .name`) or the tracker's assignee. Flag any name you inferred from a
  handle.
- **Verify every ticket key** with the tracker before using it. PR titles cite wrong or
  nonexistent keys.
- **Label the environment or region** when prod has more than one and the evidence covers only
  some of them. Mark a Live item with its region only when the version never reached the others,
  and label Platform health with the environments its KPIs cover.
- **Status comes from prod evidence, never from the tracker.** "Done" or "merged" proves nothing
  about customers. Every item gets exactly one status from the definition of done below, and that
  status decides its section.

## Definition of done (status → section)

| Status | Means | Evidence required | Goes in |
|---|---|---|---|
| Live | Customers can use it today | Every prod environment runs the version (Step 2) **and** the code is not behind a flag, or its flag is on for every prod evaluation (Step 2b) | Live for customers |
| Rolling out | On for some customers | In prod, the flag is on for some accounts and off for others, or the version is in some prod regions only | Live for customers, with "now on for <n> accounts" or the region |
| Ready, switched off | Deployed but nobody sees it yet | In prod, the flag is off for every evaluation | Coming soon, with "behind a feature flag" |
| In testing | Not in prod | In staging or a release candidate only | Coming soon |
| Flag unverified | Can't tell | Gated by a flag whose prod value can't be read | Coming soon, with "behind a feature flag". Never promote it to Live |

"Live" plus zero prod traffic still counts as Live, but say "no customer has used it yet".

## Step 1: Gather releases (read-only)

Run `git fetch --tags` in each repo first, because local checkouts can be stale. With two or more
repos, give each one to its own subagent in parallel (`Explore` is enough), with this list as the
brief. For each repo:

- List the prod releases in the period and the PRs in each range (squash-merge subjects often
  carry `(#NNNN)`). Also list the PRs in the current staging release that aren't in prod yet.
- Record who cut each release when the CI shows it (e.g. the triggering actor of the release
  workflow run).
- For a library, note which consuming app pins the new version in its lockfile, and in which of
  that app's releases. Users see a library change only once a prod release of a consumer ships
  it. Otherwise leave the library out of the post.

For each PR, return:
- number, title, author login and display name, and verified ticket key and assignee;
- one plain-language line on the user impact;
- a class: Product, Reliability/Performance, or Internal;
- the release it shipped in (prod version or staging candidate);
- the flag key(s) gating it, or "none", found as in Step 2b.

Subagent results are leads. Re-check every status, name and key you put in the post.

## Step 2: Deploys and prod KPIs

Load the observability tools (for an MCP server, via ToolSearch, following its instructions) and,
if a documented error-rate method exists in the project's docs, use it.

Windows: this period, and the same span shifted back one period for comparison. Use UTC in
queries and convert for the post.

| What | How |
|---|---|
| Deploys per environment | The running version per environment and the first time it ran. E.g. container metrics grouped by image tag (first 15-min bucket per version is the go-live time), the GitOps controller's sync history, or the platform's release list. Ignore sidecar image tags |
| Requests and errors | Sum of the request metric and of its 5xx (or error) subset over the window. Success % = 1 − errors/total |
| p95 latency | The request-duration metric or spans, at p95. Check the unit: some tools store span durations in nanoseconds |
| Key-operation volume | Count of the project's key operation (routes, jobs or events), this period vs last |
| Incidents | Error counts at a 1-minute interval around any spike, for the exact minutes. The cause from error spans or logs grouped by error message |
| Feature reach | Requests per new endpoint by status code, or span counts for in-code guards |
| Pod health | Restarts and OOM kills per deployment. Use these to explain incidents, not as lines in the post |

Query traps:
- Workers and schedulers are separate deployments from the web service. Find them before
  claiming anything about them.
- An empty error series usually means zero. Check it against the unfiltered total.
- Save project-specific traps you hit (tag names, quoting rules, metric names) as project memory
  so the next run starts with them.

## Step 2b: Feature flags (is it gated, and what is the prod value?)

Run this for every Product item, Live or Coming soon.

**1. Find the gate.** Trace from the user's entry point, not only the diff: a route, a job, a
screen, a CLI script. A PR can extend code that a flag already guards upstream without touching
the flag. Look for the project's flag constants and evaluation calls (e.g. `getBooleanValue(`,
`variation(`, `isEnabled(`, a `useFeatureFlag(` hook). A backend feature with no flag can still be
hidden by a frontend flag on the screen that calls it. Check every repo on the path before marking
it Live.

**2. Read the prod value.** Use the first source that works:
- **The provider's API or CLI** (read-only): the flag's prod targeting rules and default.
- **Evaluation telemetry:** OpenFeature hooks and most providers can record each evaluation on a
  span or as an event (key, value, reason, targeting key). Count evaluations per env, the share
  that returned `true`, and the number of distinct accounts. Confirm with one or two sample spans
  that the value belongs to the right key: a wildcard like `*<key>*true*` can match a span where a
  different flag evaluated `true`. Sampled spans give ratios and account counts, never "N
  evaluations".
- **The flag store** if it's a table: a read-only `SELECT` (see the `ticket-brief` skill's
  read-only query template).

Read the result:
- on for every prod evaluation, in every prod environment: Live;
- off everywhere: Ready, switched off;
- in between: Rolling out, with the account count;
- no evaluations in the window or no readable source: Flag unverified. That happens when the
  gated path got no traffic, ran outside a traced request, or evaluates in the browser without
  reporting.

When flags in one repo can't be read at all, say what would fix it in the hand-off (e.g. a
read-only API token for the provider, or an evaluation hook that reports to the observability
tool).

## Step 3: Select and write

- **Live for customers:** Product and Reliability/Performance items whose status is Live or
  Rolling out.
  - Write each as a benefit, with numbers where they help ("~850 checks since Tuesday").
  - For a performance fix, quote the current worst case from prod ("today some take up to 17 s").
- **Coming soon:** Product items whose status is In testing, Ready switched off, or Flag
  unverified. Put removals as "**Heads-up:**".
  - When a flag is already on in prod, say so: "goes live as soon as the next release ships".
- **Leave out:**
  - Internal items (dependencies, CI, refactors, library upgrades);
  - libraries no prod consumer ships yet;
  - security fixes worded in a way that suggests data leaked.
- **Platform health:**
  - reliability, with the exact incident window;
  - key-operation volume vs last period;
  - p95. Write "faster" or "slower" from the numbers, not "stable" by default.
- **Owner:** the feature owner (PR author or ticket assignee), one per item.

Write the post in standard markdown. Keep it under 5,000 characters, which fits one Slack message
through the connector. If the Slack connector is used, `**bold**`, `_italic_` and `- ` lists
render correctly, while a single `*x*` renders as italic.

Template:

```
**<Product> release notes — <Week NN | period> (<Mon d>–<Fri d>)**
_What's new for customers, what's coming, and how the platform did._

:white_check_mark: **Live for customers**
- **<Benefit headline>** — <one or two plain sentences, with prod evidence where useful>. _Owner: <Name>_

:soon: **Coming soon** (in testing, no date yet)
- **<Benefit headline>** — <what changes for users>. _Owner: <Name>_
- **Heads-up:** <removal or behavior change>. _Owner: <Name>_

:bar_chart: **Platform health** (<envs covered>, <start> → <end>, vs the same window last <period>)
- **Reliability:** <x>% of <n>M requests succeeded (<y>% last <period>). <Exact disruption window, or "No disruptions.">
- **Usage:** <n>k <key operations>, <±x>% vs last <period> (<n>k).
- **Speed:** <faster/slower>; 95% of requests answered within <n> ms (<n> ms last <period>).
```

## Step 4: Deliver to the runner only

The runner reviews the post first and forwards it themselves.

- **Slack connector available:** load `slack_send_message` via ToolSearch. Take the runner's own
  user id from that tool's description (it states the current logged-in user's id). Never
  hardcode an id or look one up by name. Send the post with `channel_id` set to that id, without
  asking first.
- **Never send to anyone else:** no channels, group DMs or other users' ids, even if asked in the
  same run. Posting to the team is the runner's manual step.
- **No Slack connector, or it's unauthorized:** print the post in a code block and say so.

Then reply with the message link (if sent) and list anything left unverified:
- inferred names;
- Flag unverified items, with the flag key;
- mismatches between a status page or incident record and the measured error window.
