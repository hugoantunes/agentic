---
name: pr-review
description: Review the current diff, a PR, a branch or a path, write the findings to a markdown file in a fixed Must fix / To confirm / Optional format, and post every finding as a labelled inline comment in one GitHub review with a short summary. Checks consumers in other repos when the change crosses a service boundary. Never approves.
argument-hint: "[target] [low|medium|high|xhigh|max] [--handoff] [--inline] [--no-comment] [--approve]"
disable-model-invocation: true
allowed-tools: Skill, Read, Write, Grep, Glob, Bash(git diff *), Bash(git log *), Bash(git show *), Bash(git blame *), Bash(git status *), Bash(git rev-parse *), Bash(git grep *), Bash(gh pr diff *), Bash(gh pr view *), Bash(gh api *), Bash(jq *)
---

# PR review

Review code and report every finding in the format defined under **Output format**.

## How to run the review

Invoke the `code-review` skill with `$ARGUMENTS` and follow its methodology for finding and
verifying issues: target resolution, effort level and the verification pass all behave as
documented there.

Override exactly one thing: **the reporting step.** Do not call `ReportFindings`, and do not
print findings in any other shape. Render each finding that survives verification as one block,
grouped by the headings below and most severe first within each group.

## Check other repos when the change crosses a boundary

After the review, check the consumers when the diff changes something another repo reads: an
HTTP route (path, method, params, request or response fields, status codes, errors), an event or
message (name, payload fields), a shared table, a shared package's public API, a config key or
env var other services read, or the meaning of a permission or auth result. Skip it for changes
that stay inside the repo: refactors, tests, private helpers, docs.

1. List what changed at the boundary: route paths, field and model names, event names, enum
   values, config keys.
2. Find the consumer repos. Look in the project's instruction files and memory first, then for
   sibling checkouts next to this repo. If none are known or checked out, say so in
   `## Checked and clean` and name the consumers you would expect.
3. Update every repo before looking at it: `git -C <repo> fetch origin`, then
   `git -C <repo> remote set-head origin --auto` so `origin/HEAD` is the current default branch.
   A repo whose fetch fails is not searched; say so in the terminal and in `## Checked and clean`
   instead of falling back to its stale copy.
4. Search the updated default branch, never the working tree, which may be stale or on a feature
   branch: `git -C <repo> grep -n '<identifier>' origin/HEAD`. Delegate this sweep to an
   `Explore` agent started with `model: "haiku"`, giving it the identifier list and these
   commands, and bring back only the call sites.
5. Read each call site from the same ref, `git -C <repo> show origin/HEAD:<path>`, and decide
   whether the change breaks it or changes what it gets: a removed or renamed field, a new
   required input, a changed default, a new enum value the consumer switches on, a response that
   now includes or omits rows.
6. Report a consumer problem like any other finding, anchored at the PR line that causes it, and
   name the consumer's `repo:path:line` in the comment.
7. Name the repos searched, and for what, in `## Checked and clean`.

## Where the output goes

Write the full report to a markdown file in the session scratchpad directory (or the system temp
directory when there is none), named `review-<target>.md`: the PR number when there is one
(`review-2220.md`), otherwise the branch name (`review-add-tags-tests.md`). Create it with
`Write`, in the format below.

Then print to the terminal only:

- one line per finding, `<n>. <the claim>`, grouped under its heading;
- one line naming where each finding was posted (`inline`, `off-diff`, or
  `skipped (already posted)`) and the review URL, when posting ran;
- the absolute path to the file.

Do not also print the full blocks; the file is the deliverable and the terminal is its index.
When `$ARGUMENTS` contains `--inline`, skip the file and print the full report instead.

## Posting findings to the PR

When the review target is a GitHub PR, post every finding (**Must fix**, **To confirm** and
**Optional**) in **one** GitHub review: each finding as a labelled inline comment, and the review
body as a short summary. One review means one notification and one place to read it. Skip
posting entirely when `$ARGUMENTS` contains `--no-comment`.

Resolve the PR first:

- `$ARGUMENTS` names a PR (number or URL): that is the PR.
- Otherwise: `gh pr view --json number,headRefOid,url`. No PR for the current branch: skip
  posting, say so in one line, and still write the file.

The PR you comment on must be the one whose diff produced the findings. If the target was a path
or a local branch with no PR, there is nothing to post to.

### Skip what is already posted

A finding from an earlier run can live in an inline comment, a review body, or a top-level PR
comment, so list all three:

```bash
gh api repos/{owner}/{repo}/pulls/<pr>/comments --paginate
gh api repos/{owner}/{repo}/pulls/<pr>/reviews --paginate
gh api repos/{owner}/{repo}/issues/<pr>/comments --paginate
```

Skip any finding whose claim text (ignoring its label and `<n>.` number, which shift between
runs) already appears in any of those bodies. A re-review of the same PR adds only what is new,
and never restates a finding already posted. If nothing new survives, post no review.

### Split inline from off-diff

GitHub rejects the **whole** review if one inline comment points at a line outside the diff, so
sort the findings before posting:

- **inline**: the line is an added or context line in a hunk of `gh pr diff` (the hunk used for
  its `<file context>`).
- **off-diff**: anything else. It goes in the review body instead (see below).

### Build the payload

Write every body to its own file in the scratchpad and build the JSON with `jq`, because the
bodies carry backticks, quotes and newlines and must never be inlined in the shell.

Each inline comment body is the finding's block **without** the `<file context>` section, since
GitHub already renders the surrounding diff. Prefix the claim line with the finding's group,
because the PR comment loses the heading:

````
**[Must fix] 1. <the claim>**
`<repo-relative path>:<line>` · <category>

<comment>
…
</comment>
````

The label is exactly `[Must fix]`, `[To confirm]` or `[Optional]`. Turn each inline finding into
a comment object:

```bash
jq -n --rawfile body <comment body file> \
      --arg path '<repo-relative path>' \
      --argjson line <line> \
      '{path: $path, line: $line, side: "RIGHT", body: $body}' \
  > <scratchpad>/comment-<n>.json
```

The review body is a very short summary of the fixes for the findings posted in this run: a count
line, then one bullet per finding with its label, its fix in a few words, and its `path:line`.
Nothing else: no rationale, no file context, no restated failure scenarios. When there are
off-diff findings, add an `## Off the diff` section after the bullets with their labelled blocks
in full (still without `<file context>`), since they have no line to sit on.

````
3 findings — 1 must fix, 1 to confirm, 1 optional

- [Must fix] Guard `None` before `.strip()` — `app/forms.py:42`
- [To confirm] Is the 30s timeout intended? — `app/client.py:18`
- [Optional] Reuse `slugify` from `utils` — `app/models.py:77`
````

### Post the review

```bash
jq -s --rawfile body <summary file> \
      --arg sha '<headRefOid>' \
      '{body: $body, commit_id: $sha, event: "COMMENT", comments: .}' \
      <scratchpad>/comment-1.json <scratchpad>/comment-3.json \
  | gh api repos/{owner}/{repo}/pulls/<pr>/reviews --input -
```

Name each comment file written in this run explicitly, never a glob, which would pick up files
left by an earlier run in the same session. With no inline findings, use `jq -n` with
`comments: []` instead. `{owner}` and `{repo}` are literal: `gh` resolves them from the checkout.
Use `--input`, not `-f body=...`: `-f` sends every value as a raw string (so `@file` would post
the literal path), and `-F` coerces types it recognises. The JSON payload sidesteps both.

The response's `html_url` is the review URL for the terminal index.

- **422 anyway**: the line check missed one. Move every finding into `## Off the diff`, post once
  more with `comments: []`, and say in the terminal that the inline comments fell back to the
  review body. Retry at most once.
- **The file is written either way.** If posting fails, keep the file, report the error verbatim,
  and do not retry silently.

### Approval stays with the user (`--approve`)

Never post `event: "APPROVE"`: every review is `"COMMENT"`, and the user approves by hand. With
`--approve`, decide whether the PR is **ready for the user to approve** and print the verdict as
the first terminal line. It is ready when this run has no **Must fix** finding and the PR is not
held (below). With nothing surviving verification, post no GitHub review; the verdict goes only
to the terminal. `--no-comment` still skips the GitHub review.

**Held when someone else asked for a fix.** Before deciding, list the review threads and the
latest review per reviewer:

```bash
gh api graphql -f query='{ repository(owner: "<owner>", name: "<repo>") { pullRequest(number: <pr>) { reviewThreads(first: 100) { nodes { isResolved isOutdated path line comments(first: 1) { nodes { author { login } body url } } } } latestReviews(first: 50) { nodes { author { login } state url } } } } }'
```

The PR is held when any of these is true for an author other than the user (humans and bots):

- an unresolved, non-outdated thread whose first comment demands a change: labelled `Required`,
  `Must fix`, `Blocking`, `P0`/`P1`, or plainly asking for a fix before merge. `Nit`,
  `Consider`, `Optional`, `FYI` and open questions do not hold it;
- their latest review is `CHANGES_REQUESTED`;
- a review body or top-level PR comment demands a change and no later commit or reply addresses
  it.

| Outcome                                   | Verdict line                                                                 |
|-------------------------------------------|------------------------------------------------------------------------------|
| Ready, no finding                         | `Ready to approve: <PR url> — no findings.`                                  |
| Ready, only **To confirm** / **Optional** | `Ready to approve: <PR url> — I left <n> comments: <review url>.`            |
| A **Must fix** found                      | `Not ready: <PR url> — <n> must fix: <review url>.`                          |
| Held for others' required changes         | `Not ready: <PR url> — open required changes from <login>: <comment links>.` |

When a run is both held and has a **Must fix**, use the held row and add the must-fix count.

## Output format

Group findings under the three headings below, in this order. **Omit a heading entirely when it
has no findings**; never print an empty section. Number findings continuously across groups, so
"the second one" is unambiguous.

- `## Must fix`: verified, and it produces wrong behavior, data loss, or a security hole. Only
  findings you traced or executed yourself belong here. A finding you would not stand behind in
  review belongs elsewhere.
- `## To confirm`: the reasoning holds but you could not verify it, or it turns on intent only
  the reader knows. Every finding here must end in a specific question.
- `## Optional`: real but discretionary: simplification, efficiency, coverage. The reader's call,
  and fine to decline.

Each finding:

````
**<n>. <the claim, under 60 characters — no rationale, no consequence clause>**
`<repo-relative path>:<line>` · <category>

<comment>
<The claim in one sentence. What is wrong, not what the code does.>

<The failure scenario: concrete inputs or state, then the wrong output, crash, or missed
regression that results. Say plainly whether you ran it or traced it.>

<The fix: one option stated plainly, or two if the choice is genuinely the reader's.>
</comment>

<file context>
```diff
<the hunk containing that line, from `git diff` / `gh pr diff`, with its @@ header>
```
</file context>
````

Field rules:

- **category**: kebab-case: `correctness`, `test-coverage`, `simplification`, `efficiency`,
  `security`, …
- **line**: the line the finding anchors to, in the file as it exists after the change.
- **file context**: the real hunk from the diff, never hand-typed or reconstructed. Keep it to
  the hunk that contains the line; do not paste the whole diff.
- The group heading carries the confidence, so do not also print `CONFIRMED`/`PLAUSIBLE` on the
  finding. A finding you verified goes in **Must fix** or **Optional**; one you did not goes in
  **To confirm**. If you catch yourself wanting to write "PLAUSIBLE" inside a **Must fix** block,
  it belongs in **To confirm** instead.

Close with a single `## Checked and clean` line listing what you examined and found sound,
comma-separated and compressed. It exists so a short review is not mistaken for a shallow one.
One line, not a paragraph per item. Omit it if you reviewed nothing beyond the findings.

Only report a finding that demands an action, flags a real risk, or poses a decision. No notes
that conclude the code is fine, restate what the diff did, or raise and resolve a concern in the
same breath. If a finding cannot end in a request, a question, or a concrete failure mode, drop
it. Where the user's or the project's instruction files add review rules, apply them too.

If nothing survives verification, say so in one line and state what you checked, in the terminal
and in the file both. Do not pad the output with a block per file reviewed. Post nothing to the
PR in that case.

## Handoff mode

When `$ARGUMENTS` contains `--handoff`, prefix each block with:

```
Check if this issue is valid — if so, understand the root cause and fix it.
```

That line is for pasting a block back to an agent that has not seen the finding. Omit it by
default: in a normal review the reader is reading your conclusion, and instructing them to
validate it turns a decision back into an open question. It is never added to a PR comment.
