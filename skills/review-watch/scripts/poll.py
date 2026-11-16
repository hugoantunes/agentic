"""Watch for PRs that need your review and review each one in its own headless Claude Code session.

Every 10 minutes until midnight, collect the open GitHub PRs requesting your review and, with
--slack-channel, the PRs linked in that Slack channel, skipping the ones already handled. Each
new one gets `pr-review <effort> <url> --approve` in a headless session (`claude -p`), one at a
time, so the session that started this script never wakes for it and its context stays small.
The effort grows with the PR's added lines, tests weighted down and generated files ignored
(see EFFORT_BY_SIZE and review_size), so small PRs skip the
large-PR review recipe; it sets both the model's effort and the review's depth.

notify.py reports each review as it starts and its verdict when it ends: always in this
script's output, and as a Slack DM when REVIEW_WATCH_SLACK_BOT_TOKEN and
REVIEW_WATCH_SLACK_USER_ID are set. With REVIEW_WATCH_SLACK_USER_TOKEN, the Slack message that
asked for a review carries your 👀 until the review ends.

Exits at midnight, when another poller is already running, on SIGTERM or SIGHUP, or after three
failed polls in a row. State lives in $XDG_STATE_HOME/review-watch (default
~/.local/state/review-watch): `last_ts` is the newest Slack message already handled, `gh_seen`
the GitHub requests already handled.

Usage: poll.py [--slack-channel ID] [--workdir DIR] [--review-skill /SKILL] [--budget USD] [--dry-run]
"""

import argparse
import contextlib
import functools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
PLUGIN_MANIFEST = SCRIPT_DIR.parents[2] / ".claude-plugin" / "plugin.json"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "review-watch"
STATE_PATH = STATE_DIR / "state.json"
PID_PATH = STATE_DIR / "poll.pid"
SLACK_VARIABLE_PREFIX = "REVIEW_WATCH_SLACK_"
BOT_TOKEN = "REVIEW_WATCH_SLACK_BOT_TOKEN"
USER_TOKEN = "REVIEW_WATCH_SLACK_USER_TOKEN"
CHANNEL = "REVIEW_WATCH_SLACK_CHANNEL"
POLL_SECONDS = 600
MAX_FAILURES = 3
REVIEW_TIMEOUT_SECONDS = 3600
REVIEW_BUDGET_USD = 15.0
EFFORT_BY_SIZE = ((200, "medium"), (600, "high"))
LARGE_PR_EFFORT = "xhigh"
TEST_LINE_WEIGHT = 0.25
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[^/]+$")
GENERATED_PATH = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|uv\.lock)$|\.snap$|_baseline\.json$")
GH_FILE_LIMIT = 100
REVIEWING_EMOJI = "eyes"
REACTION_ALREADY_SET = frozenset({"already_reacted", "no_reaction"})
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
VERDICT = re.compile(r"(?:Ready to approve|Not ready): [^\n`]+")
NEXT_STEP = "Next: follow 'When the poller exits' in the review-watch skill."


def default_review_skill() -> str:
    """`/<plugin>:pr-review` when this skill came with a plugin, `/pr-review` when it was copied into a skills directory."""
    if PLUGIN_MANIFEST.exists():
        return f"/{json.loads(PLUGIN_MANIFEST.read_text())['name']}:pr-review"
    return "/pr-review"


def slack(method: str, params: dict, token_variable: str = BOT_TOKEN, allowed_errors: frozenset = frozenset()) -> dict:
    """Call a Slack Web API method with the token in `token_variable`; raise when Slack answers with an error not in allowed_errors."""
    token = os.environ.get(token_variable)
    if not token:
        raise RuntimeError(f"{token_variable} is not set")
    request = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if not body.get("ok") and body.get("error") not in allowed_errors:
        raise RuntimeError(f"Slack {method} failed: {body.get('error')}")
    return body


@functools.cache
def workspace_url() -> str:
    return slack("auth.test", {})["url"].rstrip("/")


def notify(text: str) -> None:
    """Report through notify.py, the one place that knows where notices go, and keep a copy in this script's output."""
    result = subprocess.run([sys.executable, "-I", str(NOTIFY_SCRIPT), text], capture_output=True, text=True, check=True)
    print(result.stdout, end="", flush=True)


def gh_json(*args: str):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def github_login() -> str:
    return gh_json("api", "user")["login"]


def latest_ts(channel: str) -> str:
    latest = slack("conversations.history", {"channel": channel, "limit": 1})["messages"]
    return latest[0]["ts"] if latest else "0"


def load_state(channel: Optional[str]) -> dict:
    """Read the saved state; for a channel it has never watched, start from the channel's latest message so only new posts count."""
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    last_ts = state.get("last_ts", "0")
    if channel and state.get("channel") != channel:
        last_ts = latest_ts(channel)
    return {"channel": channel, "last_ts": last_ts, "gh_seen": state.get("gh_seen", [])}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(STATE_PATH)


def messages_after(channel: str, oldest: str) -> list:
    """Return the channel's messages posted after `oldest`, oldest first."""
    messages, params = [], {"channel": channel, "oldest": oldest, "limit": 200}
    while True:
        page = slack("conversations.history", params)
        messages += page["messages"]
        cursor = page.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    newer = [m for m in messages if Decimal(m["ts"]) > Decimal(oldest)]
    return sorted(newer, key=lambda m: Decimal(m["ts"]))


def first_links(messages: list) -> dict:
    """Map each PR URL linked in the messages to the ts of the earliest message linking it."""
    links = {}
    for message in messages:
        for url in PR_URL.findall(message.get("text", "")):
            links.setdefault(url, message["ts"])
    return links


def my_reviews(pr: dict, me: str) -> list:
    return [r for r in pr["reviews"] if (r.get("author") or {}).get("login") == me]


def linked_pr(url: str):
    """Return a PR linked in Slack, or None when it or its repository does not resolve for you.

    Skipping those keeps one bad link from failing every poll, since its message would
    otherwise never be marked handled.
    """
    try:
        return gh_json("pr", "view", url, "--json", "state,author,reviews")
    except subprocess.CalledProcessError as error:
        if "Could not resolve to a" in error.stderr:
            return None
        raise


def needs_my_review(url: str, me: str) -> bool:
    """A PR linked in Slack needs you when it is open, not yours, and you have never reviewed it."""
    pr = linked_pr(url)
    if pr is None:
        return False
    author = (pr.get("author") or {}).get("login")
    return pr["state"] == "OPEN" and author != me and not my_reviews(pr, me)


def head_reviewed(url: str, me: str) -> bool:
    pr = gh_json("pr", "view", url, "--json", "headRefOid,reviews")
    return any((r.get("commit") or {}).get("oid") == pr["headRefOid"] for r in my_reviews(pr, me))


def poll(state: dict, me: str) -> tuple:
    """Return each new request as PR URL -> the ts of the Slack message asking for it (None when it came from GitHub), and the state that marks them handled.

    gh_seen becomes the current non-draft requests, so a request that goes away and comes back
    (a re-request after you review) is picked up again, and a draft is picked up once ready.
    """
    channel = state["channel"]
    messages = messages_after(channel, state["last_ts"]) if channel else []
    from_slack = {url: ts for url, ts in first_links(messages).items() if needs_my_review(url, me)}
    pending = gh_json("search", "prs", "user-review-requested:@me", "--state=open", "--limit=100", "--json", "url,isDraft")
    ready = [pr["url"] for pr in pending if not pr["isDraft"]]
    seen = set(state["gh_seen"])
    from_github = [url for url in ready if url not in seen and url not in from_slack and not head_reviewed(url, me)]
    last_ts = messages[-1]["ts"] if messages else state["last_ts"]
    return from_slack | dict.fromkeys(from_github), {"channel": channel, "last_ts": last_ts, "gh_seen": ready}


def effort_for_size(lines: float) -> str:
    """Pick the review effort from a PR's review size; review cost barely follows size, so small PRs would otherwise pay the large-PR price."""
    for limit, effort in EFFORT_BY_SIZE:
        if lines < limit:
            return effort
    return LARGE_PR_EFFORT


def line_weight(path: str) -> float:
    """How much a file's added lines count toward review size: generated files not at all, tests a quarter, everything else in full."""
    if GENERATED_PATH.search(path):
        return 0.0
    if TEST_PATH.search(path):
        return TEST_LINE_WEIGHT
    return 1.0


def review_size(files: list) -> float:
    """Weighted added lines; deletions are left out because removed or rewritten-away code needs far less review than new code."""
    return sum(file["additions"] * line_weight(file["path"]) for file in files)


def review_effort(url: str) -> str:
    """Size-based effort for a PR; when its size cannot be read, or gh's file list may be truncated, review at the large-PR effort rather than skimp."""
    try:
        files = gh_json("pr", "view", url, "--json", "files")["files"]
    except subprocess.CalledProcessError:
        return LARGE_PR_EFFORT
    if len(files) >= GH_FILE_LIMIT:
        return LARGE_PR_EFFORT
    return effort_for_size(review_size(files))


def review_command(review_skill: str, url: str, effort: str) -> str:
    """Build the review command; the effort goes first because code-review reads its level from before the target."""
    return f"{review_skill} {effort} {url} --approve"


def message_link(channel: str, ts: str) -> str:
    return f"{workspace_url()}/archives/{channel}/p{ts.replace('.', '')}"


def set_reviewing_reaction(action: str, channel: str, ts: str) -> None:
    """Add or remove your 👀 on a Slack message, when REVIEW_WATCH_SLACK_USER_TOKEN is set.

    A failure is reported instead of raised: the review matters more than the mark, and raising
    would stop the watcher after the state already marked the request handled.
    """
    if not os.environ.get(USER_TOKEN):
        return
    params = {"channel": channel, "timestamp": ts, "name": REVIEWING_EMOJI}
    try:
        slack(f"reactions.{action}", params, token_variable=USER_TOKEN, allowed_errors=REACTION_ALREADY_SET)
    except (RuntimeError, OSError) as error:
        notify(f"Review watcher could not {action} :{REVIEWING_EMOJI}: on {message_link(channel, ts)} — {describe(error)}")


@contextlib.contextmanager
def reviewing(url: str, channel: Optional[str], slack_ts: Optional[str], effort: str):
    """Report that the review started, and keep your 👀 on the Slack message that asked for it until the review ends."""
    source = "requested on GitHub." if slack_ts is None else f"request: {message_link(channel, slack_ts)}"
    notify(f"Reviewing ({effort} effort): {url} — {source}")
    if slack_ts is not None:
        set_reviewing_reaction("add", channel, slack_ts)
    try:
        yield
    finally:
        if slack_ts is not None:
            set_reviewing_reaction("remove", channel, slack_ts)


def review_environment() -> dict:
    """This process's environment without the Slack settings, so the code under review can't reach the tokens."""
    return {name: value for name, value in os.environ.items() if not name.startswith(SLACK_VARIABLE_PREFIX)}


def final_message(stdout: str, stderr: str) -> str:
    """The headless session's last message, or its raw output when it did not end with one."""
    try:
        output = json.loads(stdout)
    except json.JSONDecodeError:
        return (stdout + stderr).strip()
    return output.get("result") or output.get("subtype", "")


def run_review(url: str, effort: str, options: argparse.Namespace) -> None:
    """Run one review in a headless Claude Code session and report its verdict, or why it ended without one."""
    command = review_command(options.review_skill, url, effort)
    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "json", "--permission-mode", "auto", "--permission-prompts", "none", "--max-budget-usd", str(options.budget), "--effort", effort, command],
            cwd=options.workdir,
            env=review_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=REVIEW_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        notify(f"Review failed: {url} — no verdict after {REVIEW_TIMEOUT_SECONDS // 60} minutes")
        return
    message = final_message(result.stdout, result.stderr)
    verdict = VERDICT.search(message)
    if verdict:
        notify(verdict.group(0).strip())
        return
    lines = message.splitlines() or [f"exit status {result.returncode}"]
    notify(f"Review failed: {url} — {lines[-1][:300]}")


def run_reviews(requests: dict, options: argparse.Namespace) -> None:
    """Review each PR in turn at an effort set by its size; when a signal stops the watcher mid-batch, report the PRs left unreviewed."""
    urls = list(requests)
    for position, url in enumerate(urls):
        try:
            effort = review_effort(url)
            with reviewing(url, options.slack_channel, requests[url], effort):
                run_review(url, effort, options)
        except SystemExit:
            notify(f"Review watcher stopped before reviewing: {', '.join(urls[position:])}")
            raise


def describe(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"{' '.join(error.cmd)} failed: {(error.stderr or '').strip()}"
    return f"{type(error).__name__}: {error}"


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def single_instance():
    """Hold poll.pid while running; exit when a live poller from any session already holds it."""
    holder = int(PID_PATH.read_text()) if PID_PATH.exists() else None
    if holder and is_alive(holder):
        raise SystemExit(f"already running as pid {holder}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))
    try:
        yield
    finally:
        PID_PATH.unlink(missing_ok=True)


def watch(options: argparse.Namespace) -> None:
    """Poll until the day ends, reviewing each new request as it appears."""
    me = github_login()
    today = date.today()
    failures = 0
    while date.today() == today:
        try:
            requests, new_state = poll(load_state(options.slack_channel), me)
        except Exception as error:
            failures += 1
            if failures == MAX_FAILURES:
                raise SystemExit(f"error: {describe(error)}")
        else:
            failures = 0
            save_state(new_state)
            run_reviews(requests, options)
        time.sleep(POLL_SECONDS)
    print("day over")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="poll.py", description="Review each PR that needs your review, for the rest of the day.")
    parser.add_argument("--slack-channel", default=os.environ.get(CHANNEL), help=f"also review PRs linked in this Slack channel ID (default: ${CHANNEL})")
    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="directory the reviews run in, ideally where your repos are checked out (default: the current one)")
    parser.add_argument("--review-skill", default=default_review_skill(), help="skill that reviews one PR (default: %(default)s)")
    parser.add_argument("--budget", type=float, default=REVIEW_BUDGET_USD, help="maximum USD per review (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="poll once, print the reviews it would run, and leave the state untouched")
    return parser.parse_args()


def main() -> None:
    options = parse_arguments()
    if shutil.which("claude") is None:
        raise SystemExit("error: the claude CLI is not on PATH")
    if options.dry_run:
        requests, new_state = poll(load_state(options.slack_channel), github_login())
        commands = [review_command(options.review_skill, url, review_effort(url)) for url in requests]
        print("\n".join(commands) or "No new review requests.")
        print(f"state after: {json.dumps(new_state)}")
        return
    for hangup in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(hangup, lambda *_: sys.exit("stopped by signal"))
    try:
        with single_instance():
            watch(options)
    finally:
        print(NEXT_STEP)


if __name__ == "__main__":
    main()
