"""Snapshot a git working tree and inventory a project's standards, gates and CI for the local-review skill.

Usage:
    local_review.py prepare [--branch] [--repo DIR]
    local_review.py snapshot [--repo DIR]

`prepare` records the working tree as an unreferenced commit, resolves the review scope and prints an
inventory of instruction files, task-runner recipes, hooks, CI commands and tool configs.
`snapshot` only records the working tree and prints the commit sha.

Neither command touches the index, refs, stash or files: the snapshot is built in a temporary index and
written with `git commit-tree`, so it leaves only unreferenced objects that `git gc` prunes later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_UNTRACKED_BYTES = 5 * 1024 * 1024
MAX_COMMAND_CHARS = 300
MAX_FILES_LISTED = 200
MAX_HEADINGS = 15
MAX_COMMANDS_PER_FILE = 40
MAX_RECIPES = 60
LINK_DEPTH = 2

SNAPSHOT_IDENTITY = {
    "GIT_AUTHOR_NAME": "local-review",
    "GIT_AUTHOR_EMAIL": "local-review@localhost",
    "GIT_COMMITTER_NAME": "local-review",
    "GIT_COMMITTER_EMAIL": "local-review@localhost",
}

DIRECTORY_INSTRUCTION_NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
ROOT_INSTRUCTION_PATHS = (
    "CLAUDE.md",
    "CLAUDE.local.md",
    ".claude/CLAUDE.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".github/CONTRIBUTING.md",
    "docs/CONTRIBUTING.md",
    ".github/copilot-instructions.md",
    ".cursorrules",
)
CONFIG_PATTERNS = (
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "noxfile.py",
    "pytest.ini",
    "conftest.py",
    "*ruff*.toml",
    "*mypy*.ini",
    "mypy.ini",
    ".flake8",
    ".pylintrc",
    "pyrightconfig.json",
    "package.json",
    "tsconfig*.json",
    ".eslintrc*",
    "eslint.config.*",
    ".prettierrc*",
    "prettier.config.*",
    "biome.json*",
    "vitest.config.*",
    "jest.config.*",
    "playwright.config.*",
    "go.mod",
    ".golangci.y*ml",
    "Cargo.toml",
    "rustfmt.toml",
    "clippy.toml",
    "Gemfile",
    ".rubocop.yml",
    "composer.json",
    "build.gradle*",
    "pom.xml",
    ".editorconfig",
    "docker-compose*.y*ml",
    "compose*.y*ml",
    ".env.test",
    ".env.ci",
    ".env.example",
)
CI_FILES = (
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "bitbucket-pipelines.yml",
    "azure-pipelines.yml",
    ".buildkite/pipeline.yml",
    "Jenkinsfile",
)
JUSTFILE_NAMES = ("justfile", "Justfile", ".justfile")
MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")

MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
H2_HEADING = re.compile(r"^##\s+(.+?)\s*#*\s*$")
JUST_RECIPE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_-]*)(?:\s+[^:]*)?:(?!=)")
JUST_NON_RECIPE = re.compile(r"^(set|export|alias|import|mod)\b")
MAKE_TARGET = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_./-]*)\s*:(?![=:])(?:.*?##\s*(.*))?")
COMMAND_KEY = re.compile(
    r"^(?P<indent>\s*)(?:-\s+)?(?P<key>run|command|script|before_script|after_script|sh)\s*:\s*(?P<value>.*)$"
)
BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*\s*(#.*)?$")
YAML_KEY = re.compile(r"^(\s*)([\w.-]+)\s*:\s*(#.*)?$")
GHA_ON = re.compile(r"""^(\s*)["']?(on|true)["']?\s*:\s*(.*)$""")
PRE_COMMIT_ID = re.compile(r"^\s*-?\s*id:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
TOOL_SECTION = re.compile(r"^\[tool\.([A-Za-z0-9_-]+)")
TASK_SECTION = re.compile(r"^\[tool\.(poe\.tasks|pdm\.scripts|hatch\.envs\.[\w-]+\.scripts|taskipy\.tasks)\]")
TOML_KEY = re.compile(r"^([A-Za-z0-9_-]+)\s*=")
TOX_ENV = re.compile(r"^\[testenv:([^\]]+)\]")
NOX_SESSION = re.compile(r"@nox\.session[^\n]*\n(?:@[^\n]*\n)*def\s+(\w+)")


class GitError(RuntimeError):
    """A git command exited non-zero."""


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None, stdin: str | None = None) -> str:
    """Run git in `repo` and return its raw stdout, raising GitError on failure."""
    result = subprocess.run(["git", *args], cwd=repo, env=env, input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def try_git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return its stripped stdout, or "" when it fails."""
    try:
        return run_git(repo, *args).strip()
    except GitError:
        return ""


def repo_root(start: Path) -> Path:
    """Return the top-level directory of the work tree containing `start`."""
    return Path(run_git(start, "rev-parse", "--show-toplevel").strip())


def untracked_files(repo: Path) -> list[str]:
    """List untracked, non-ignored files relative to the repo root."""
    output = run_git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", ":/")
    return [path for path in output.split("\0") if path]


def oversized(repo: Path, paths: list[str]) -> list[str]:
    """Return the paths whose files exceed MAX_UNTRACKED_BYTES."""
    return [path for path in paths if (repo / path).is_file() and (repo / path).stat().st_size > MAX_UNTRACKED_BYTES]


def take_snapshot(repo: Path) -> tuple[str, list[str], list[str]]:
    """Commit the working tree, untracked files included, without touching the index, refs or files.

    Returns the commit sha, the untracked files it contains, and the untracked files left out for size.
    """
    real_index = repo / run_git(repo, "rev-parse", "--git-path", "index").strip()
    untracked = untracked_files(repo)
    skipped = oversized(repo, untracked)
    kept = [path for path in untracked if path not in set(skipped)]
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp, "index")
        if real_index.exists():
            shutil.copyfile(real_index, index)
        env = {**os.environ, **SNAPSHOT_IDENTITY, "GIT_INDEX_FILE": str(index)}
        run_git(repo, "add", "--update", env=env)
        if kept:
            run_git(
                repo,
                "--literal-pathspecs",
                "add",
                "--pathspec-from-file=-",
                "--pathspec-file-nul",
                env=env,
                stdin="\0".join(kept),
            )
        tree = run_git(repo, "write-tree", env=env).strip()
    head = try_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    parents = ["-p", head] if head else []
    sha = run_git(
        repo,
        "commit-tree",
        "--no-gpg-sign",
        *parents,
        "-m",
        "local-review snapshot",
        tree,
        env={**os.environ, **SNAPSHOT_IDENTITY},
    ).strip()
    return sha, kept, skipped


def default_branch(repo: Path) -> str:
    """Return the remote default branch (e.g. origin/main), falling back to common local names."""
    symbolic = try_git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if symbolic:
        return symbolic
    for candidate in ("origin/main", "origin/master", "main", "master"):
        if try_git(repo, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return ""


def count_commits(repo: Path, revision_range: str) -> int:
    """Count the commits in a revision range, or 0 when the range can't be resolved."""
    output = try_git(repo, "rev-list", "--count", revision_range)
    return int(output) if output.isdigit() else 0


def resolve_scope(repo: Path, branch_mode: bool) -> dict[str, str | int]:
    """Pick the base the review diffs against, and describe why."""
    dirty = bool(run_git(repo, "status", "--porcelain", "--untracked-files=all").strip())
    head = try_git(repo, "rev-parse", "--verify", "--quiet", "HEAD")
    upstream = try_git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    unpushed = count_commits(repo, f"{upstream}..HEAD") if upstream else 0
    default = default_branch(repo)
    merge_base = try_git(repo, "merge-base", default, "HEAD") if default and head else ""
    scope: dict[str, str | int] = {
        "branch": try_git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "(no commits)",
        "upstream": upstream,
        "unpushed": unpushed,
        "default": default,
        "dirty": int(dirty),
    }
    if branch_mode and merge_base:
        scope.update(base=merge_base, mode=f"the branch since it left {default}, plus uncommitted changes")
    elif dirty:
        base = head or run_git(repo, "hash-object", "-t", "tree", "--stdin", stdin="").strip()
        scope.update(base=base, mode="uncommitted changes (staged, unstaged, untracked)")
    elif upstream and unpushed:
        scope.update(base=try_git(repo, "rev-parse", upstream), mode=f"{unpushed} unpushed commits on {upstream}")
    elif merge_base and merge_base != head:
        scope.update(base=merge_base, mode=f"the branch since it left {default} (tree is clean)")
    else:
        scope.update(base="", mode="empty")
    return scope


def changed_files(repo: Path, base: str, snapshot: str, untracked: set[str]) -> list[str]:
    """Return `status path` lines for the diff between base and snapshot, marking untracked files."""
    output = run_git(repo, "diff", "--name-status", "-z", "-M", base, snapshot)
    tokens = [token for token in output.split("\0") if token]
    lines: list[str] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        if status[0] in "RC":
            path = f"{tokens[index + 1]} -> {tokens[index + 2]}"
            index += 3
        else:
            path = tokens[index + 1]
            index += 2
        marker = "   (untracked)" if path in untracked else ""
        lines.append(f"{status[0]}  {path}{marker}")
    return lines


def read_text(path: Path) -> str:
    """Read a text file, returning "" when it can't be decoded or read."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def h2_headings(text: str) -> list[str]:
    """Return the level-2 markdown headings outside fenced code blocks."""
    headings: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = None if fenced else H2_HEADING.match(line)
        if match:
            headings.append(match.group(1))
    return headings


def describe_doc(repo: Path, path: Path, origin: str = "") -> str:
    """Summarize a markdown file as `path (N lines, origin): ## heading · ...`."""
    text = read_text(path)
    details = [f"{len(text.splitlines())} lines"]
    if origin:
        details.append(origin)
    headings = h2_headings(text)
    shown = " · ".join(headings[:MAX_HEADINGS])
    more = f" · +{len(headings) - MAX_HEADINGS} more" if len(headings) > MAX_HEADINGS else ""
    summary = f": {shown}{more}" if headings else ""
    return f"{path.relative_to(repo)} ({', '.join(details)}){summary}"


def linked_docs(repo: Path, path: Path) -> list[Path]:
    """Return markdown files inside the repo that `path` links to."""
    found: list[Path] = []
    for target in MARKDOWN_LINK.findall(read_text(path)):
        if "://" in target:
            continue
        resolved = (path.parent / target).resolve()
        if resolved.is_file() and resolved.is_relative_to(repo.resolve()):
            found.append(resolved)
    return found


def instruction_files(repo: Path, changed_paths: list[str]) -> list[str]:
    """List the root and directory instruction files and the docs they link to, up to LINK_DEPTH hops."""
    resolved_repo = repo.resolve()
    roots = [(resolved_repo / name) for name in ROOT_INSTRUCTION_PATHS if (resolved_repo / name).is_file()]
    directories: list[Path] = []
    for changed in changed_paths:
        parent = (resolved_repo / changed).parent
        while parent != resolved_repo and parent.is_relative_to(resolved_repo):
            for name in DIRECTORY_INSTRUCTION_NAMES:
                candidate = parent / name
                if candidate.is_file() and candidate not in directories:
                    directories.append(candidate)
            parent = parent.parent
    seen = set(roots) | set(directories)
    lines = [describe_doc(resolved_repo, path) for path in roots]
    lines += [describe_doc(resolved_repo, path, "for the changed files below it") for path in directories]
    frontier = roots
    for _ in range(LINK_DEPTH):
        next_frontier: list[Path] = []
        for source in frontier:
            for target in linked_docs(resolved_repo, source):
                if target in seen:
                    continue
                seen.add(target)
                next_frontier.append(target)
                lines.append(describe_doc(resolved_repo, target, f"linked from {source.relative_to(resolved_repo)}"))
        frontier = next_frontier
    return lines


def runner_dirs(repo: Path, changed_paths: list[str]) -> list[Path]:
    """Return the repo root plus every ancestor directory of a changed file, nearest last."""
    dirs = [repo]
    for changed in changed_paths:
        parent = (repo / changed).parent
        chain: list[Path] = []
        while parent != repo and parent.is_relative_to(repo):
            chain.append(parent)
            parent = parent.parent
        for directory in reversed(chain):
            if directory not in dirs:
                dirs.append(directory)
    return dirs


def justfile_recipes(path: Path) -> list[str]:
    """Return `name — doc comment` for each public recipe in a justfile."""
    recipes: list[str] = []
    comment = ""
    for line in read_text(path).splitlines():
        stripped = line.strip()
        if line.startswith("#") and not line.startswith("#!"):
            comment = stripped.lstrip("#").strip()
            continue
        if line.startswith("[") or not stripped:
            continue
        match = None if line[0].isspace() or JUST_NON_RECIPE.match(line) else JUST_RECIPE.match(line)
        if match and not match.group(1).startswith("_"):
            recipes.append(f"{match.group(1)} — {comment}" if comment else match.group(1))
        if not line[0].isspace():
            comment = ""
    return recipes


def makefile_targets(path: Path) -> list[str]:
    """Return `target — ## comment` for each explicit target in a Makefile."""
    targets: list[str] = []
    for line in read_text(path).splitlines():
        match = MAKE_TARGET.match(line)
        if match and match.group(1) not in targets:
            description = (match.group(2) or "").strip()
            targets.append(f"{match.group(1)} — {description}" if description else match.group(1))
    return targets


def json_section(path: Path, key: str) -> list[str]:
    """Return `name = command` entries from a JSON object field such as package.json `scripts`."""
    try:
        section = json.loads(read_text(path) or "{}").get(key) or {}
    except (json.JSONDecodeError, AttributeError):
        return []
    return [f"{name} = {shorten(str(command))}" for name, command in section.items()]


def pyproject_summary(path: Path) -> list[str]:
    """Return the configured tools and any task-runner tasks declared in pyproject.toml."""
    tools: list[str] = []
    tasks: list[str] = []
    in_tasks = False
    for line in read_text(path).splitlines():
        if line.startswith("["):
            in_tasks = bool(TASK_SECTION.match(line))
            tool = TOOL_SECTION.match(line)
            if tool and tool.group(1) not in tools:
                tools.append(tool.group(1))
            continue
        key = TOML_KEY.match(line) if in_tasks else None
        if key:
            tasks.append(key.group(1))
    lines = [f"tools configured: {', '.join(tools)}"] if tools else []
    if tasks:
        lines.append(f"tasks: {', '.join(tasks)}")
    return lines


def runner_summary(repo: Path, directory: Path) -> list[str]:
    """Describe the task runners found in one directory."""
    label = "" if directory == repo else f" ({directory.relative_to(repo)}/)"
    entries = set(os.listdir(directory))
    lines: list[str] = []
    for name in JUSTFILE_NAMES:
        if name in entries:
            recipes = justfile_recipes(directory / name)[:MAX_RECIPES]
            lines.append(f"{name}{label}:")
            lines += [f"  {recipe}" for recipe in recipes]
    for name in MAKEFILE_NAMES:
        if name in entries:
            targets = makefile_targets(directory / name)[:MAX_RECIPES]
            lines.append(f"{name}{label}:")
            lines += [f"  {target}" for target in targets]
    for name, key in (("package.json", "scripts"), ("composer.json", "scripts"), ("deno.json", "tasks")):
        entries = json_section(directory / name, key) if (directory / name).is_file() else []
        if entries:
            lines.append(f"{name}{label} {key}:")
            lines += [f"  {entry}" for entry in entries[:MAX_RECIPES]]
    if (directory / "pyproject.toml").is_file():
        summary = pyproject_summary(directory / "pyproject.toml")
        if summary:
            lines.append(f"pyproject.toml{label}:")
            lines += [f"  {entry}" for entry in summary]
    if (directory / "tox.ini").is_file():
        envs = TOX_ENV.findall(read_text(directory / "tox.ini"))
        lines.append(f"tox.ini{label}: envs {', '.join(envs) or '(default only)'}")
    if (directory / "noxfile.py").is_file():
        sessions = NOX_SESSION.findall(read_text(directory / "noxfile.py"))
        lines.append(f"noxfile.py{label}: sessions {', '.join(sessions)}")
    for name in ("Taskfile.yml", "Taskfile.yaml"):
        if (directory / name).is_file():
            lines.append(f"{name}{label}: present")
    for name, hint in (("Cargo.toml", "cargo"), ("go.mod", "go"), ("Gemfile", "bundle"), ("pom.xml", "maven")):
        if (directory / name).is_file():
            lines.append(f"{name}{label}: {hint} project")
    return lines


def git_hooks(repo: Path) -> list[str]:
    """List the active, non-sample git hooks and the hook managers configured in the repo."""
    lines: list[str] = []
    hooks_path = try_git(repo, "config", "--get", "core.hooksPath")
    hooks_dir = repo / (hooks_path or run_git(repo, "rev-parse", "--git-path", "hooks").strip())
    managed_by_husky = hooks_dir.resolve().parent == (repo / ".husky").resolve()
    if hooks_dir.is_dir() and not managed_by_husky:
        active = sorted(
            entry.name
            for entry in hooks_dir.iterdir()
            if entry.is_file() and not entry.name.endswith(".sample") and os.access(entry, os.X_OK)
        )
        if active:
            lines.append(f"git hooks ({hooks_dir}): {', '.join(active)}")
    pre_commit = repo / ".pre-commit-config.yaml"
    if pre_commit.is_file():
        ids = PRE_COMMIT_ID.findall(read_text(pre_commit))
        lines.append(f"pre-commit (.pre-commit-config.yaml): {', '.join(ids) or 'no hook ids found'}")
    husky = repo / ".husky"
    if husky.is_dir():
        names = sorted(entry.name for entry in husky.iterdir() if entry.is_file())
        lines.append(f"husky (.husky/): {', '.join(names)}")
    for name in ("lefthook.yml", ".lefthook.yml", "lefthook.yaml"):
        if (repo / name).is_file():
            lines.append(f"lefthook ({name}): present")
    return lines


def claude_hooks(repo: Path) -> list[str]:
    """List the Claude Code hooks the project configures, which fire on their own during edits."""
    lines: list[str] = []
    for name in (".claude/settings.json", ".claude/settings.local.json"):
        path = repo / name
        if not path.is_file():
            continue
        try:
            hooks = json.loads(read_text(path) or "{}").get("hooks") or {}
        except json.JSONDecodeError:
            continue
        for event, groups in hooks.items():
            for group in groups:
                matcher = group.get("matcher", "")
                commands = [hook.get("command", "") for hook in group.get("hooks", [])]
                suffix = f"[{matcher}]" if matcher else ""
                lines.append(f"Claude hook ({name}) {event}{suffix}: {'; '.join(commands)}")
    return lines


def shorten(command: str) -> str:
    """Collapse whitespace and line continuations, then cap the length, keeping both ends.

    The tail is kept because that is where test and lint flags usually sit.
    """
    flat = re.sub(r"\s*\\\n\s*", " ", command)
    flat = re.sub(r"\s+", " ", flat).strip()
    if len(flat) <= MAX_COMMAND_CHARS:
        return flat
    half = (MAX_COMMAND_CHARS - 3) // 2
    return f"{flat[:half]} … {flat[-half:]}"


def indent_of(line: str) -> int:
    """Return the number of leading spaces."""
    return len(line) - len(line.lstrip(" "))


def block_after(lines: list[str], start: int, key_column: int) -> tuple[list[str], int]:
    """Collect the lines indented deeper than `key_column` that follow `start`."""
    collected: list[str] = []
    index = start + 1
    while index < len(lines) and (not lines[index].strip() or indent_of(lines[index]) > key_column):
        collected.append(lines[index])
        index += 1
    return collected, index


def yaml_commands(lines: list[str], start: int) -> tuple[list[str], int]:
    """Parse the command(s) of the run/script/command key at `start`; return them and the next line index."""
    match = COMMAND_KEY.match(lines[start])
    assert match
    key_column = lines[start].index(match.group("key"))
    value = match.group("value").strip()
    if value and not BLOCK_SCALAR.match(value):
        quoted = len(value) > 1 and value[0] == value[-1] and value[0] in "'\""
        return [value[1:-1] if quoted else value], start + 1
    block, next_index = block_after(lines, start, key_column)
    if value:
        return [shorten("\n".join(line.strip() for line in block))], next_index
    items = [line.strip()[2:] for line in block if line.strip().startswith("- ")]
    if items:
        return items, next_index
    return [], start + 1


def job_names(lines: list[str]) -> dict[int, str]:
    """Map each line index under a top-level `jobs:` key to the job it belongs to."""
    owners: dict[int, str] = {}
    jobs_line = next((i for i, line in enumerate(lines) if re.match(r"^\s*jobs:\s*(#.*)?$", line)), None)
    if jobs_line is None:
        return owners
    jobs_indent = indent_of(lines[jobs_line])
    job_indent = None
    current = ""
    for index in range(jobs_line + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            owners[index] = current
            continue
        if indent_of(line) <= jobs_indent:
            break
        job_indent = job_indent if job_indent is not None else indent_of(line)
        key = YAML_KEY.match(line)
        if key and indent_of(line) == job_indent:
            current = key.group(2)
        owners[index] = current
    return owners


def top_level_owners(lines: list[str]) -> dict[int, str]:
    """Map each line index to the nearest preceding top-level YAML key (GitLab-style job names)."""
    owners: dict[int, str] = {}
    base = min((indent_of(line) for line in lines if line.strip() and not line.lstrip().startswith("#")), default=0)
    current = ""
    for index, line in enumerate(lines):
        key = YAML_KEY.match(line)
        if key and indent_of(line) == base:
            current = key.group(2)
        owners[index] = current
    return owners


def workflow_triggers(lines: list[str]) -> str:
    """Return the GitHub Actions `on:` triggers as a comma-separated string."""
    base = min((indent_of(line) for line in lines if line.strip() and not line.lstrip().startswith("#")), default=0)
    for index, line in enumerate(lines):
        match = GHA_ON.match(line)
        if not match or indent_of(line) != base:
            continue
        if match.group(3).strip():
            return match.group(3).strip()
        block, _ = block_after(lines, index, indent_of(line))
        child_indent = min((indent_of(child) for child in block if child.strip()), default=0)
        keys = [YAML_KEY.match(child) or re.match(r"^(\s*)([\w-]+)\s*:", child) for child in block]
        names = [key.group(2) for key, child in zip(keys, block) if key and indent_of(child) == child_indent]
        return ", ".join(names)
    return ""


def ci_file_summary(repo: Path, path: Path) -> list[str]:
    """List the commands each job of a CI config runs, grouped by job."""
    lines = read_text(path).splitlines()
    owners = job_names(lines) or top_level_owners(lines)
    triggers = workflow_triggers(lines) if ".github/workflows" in str(path) else ""
    header = f"{path.relative_to(repo)}" + (f" (on: {triggers})" if triggers else "")
    grouped: dict[str, list[str]] = {}
    index = 0
    total = 0
    while index < len(lines) and total < MAX_COMMANDS_PER_FILE:
        if not COMMAND_KEY.match(lines[index]):
            index += 1
            continue
        commands, next_index = yaml_commands(lines, index)
        job = owners.get(index, "")
        for command in commands:
            grouped.setdefault(job, []).append(shorten(command))
            total += 1
        index = max(next_index, index + 1)
    output = [header]
    for job, commands in grouped.items():
        output.append(f"  {job or '(top level)'}:")
        output += [f"    $ {command}" for command in commands]
    return output


def ci_summary(repo: Path) -> list[str]:
    """Summarize every CI config in the repo."""
    paths = sorted((repo / ".github" / "workflows").glob("*.y*ml")) if (repo / ".github" / "workflows").is_dir() else []
    paths += [repo / name for name in CI_FILES if (repo / name).is_file()]
    lines: list[str] = []
    for path in paths:
        if path.name == "Jenkinsfile":
            lines.append(f"{path.relative_to(repo)}: present (read it directly)")
            continue
        lines += ci_file_summary(repo, path)
    return lines


def tool_configs(repo: Path) -> list[str]:
    """List the lint, type, test and container configs at the repo root."""
    found: list[str] = []
    for pattern in CONFIG_PATTERNS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file() and path.name not in found:
                found.append(path.name)
    return found


def print_section(title: str, lines: list[str]) -> None:
    """Print a titled section, or nothing when it has no lines."""
    if lines:
        print(f"\n## {title}")
        print("\n".join(lines))


def prepare(repo: Path, branch_mode: bool) -> int:
    """Snapshot the tree, resolve the scope and print the project inventory."""
    snapshot, untracked, skipped = take_snapshot(repo)
    scope = resolve_scope(repo, branch_mode)
    print(f"repo: {repo}")
    upstream = f"{scope['upstream']} ({scope['unpushed']} unpushed)" if scope["upstream"] else "none"
    print(f"branch: {scope['branch']}   upstream: {upstream}   default: {scope['default'] or 'unknown'}")
    print(f"before: {snapshot}")
    print(f"scope: {scope['mode']}")
    if not scope["base"]:
        print("nothing to review: the working tree is clean and HEAD has nothing the default branch lacks")
        return 0
    print(f"base: {scope['base']}")
    print(f"diff: git diff {scope['base']} {snapshot}")
    if scope["unpushed"] and scope["base"] != try_git(repo, "rev-parse", str(scope["upstream"])) and not branch_mode:
        print(f"note: {scope['unpushed']} unpushed commits are outside this scope; --branch includes them")
    if skipped:
        print(f"skipped untracked files over {MAX_UNTRACKED_BYTES // 1024 // 1024} MB (not reviewed): {', '.join(skipped)}")
    files = changed_files(repo, str(scope["base"]), snapshot, set(untracked))
    if not files:
        print("nothing to review: the diff is empty")
        return 0
    paths = [line.split("  ", 1)[1].split(" -> ")[-1].replace("   (untracked)", "") for line in files]
    shown = files[:MAX_FILES_LISTED]
    more = [f"… +{len(files) - MAX_FILES_LISTED} more"] if len(files) > MAX_FILES_LISTED else []
    print_section(f"Changed files ({len(files)})", shown + more)
    print_section("Instruction files", instruction_files(repo, paths))
    runners: list[str] = []
    for directory in runner_dirs(repo, paths):
        runners += runner_summary(repo, directory)
    print_section("Task runners", runners)
    print_section("Hooks", git_hooks(repo) + claude_hooks(repo))
    print_section("CI", ci_summary(repo))
    configs = tool_configs(repo)
    print_section("Tool configs at the root", [", ".join(configs)] if configs else [])
    return 0


def main() -> int:
    """Parse arguments and dispatch to `prepare` or `snapshot`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("prepare", "snapshot"))
    parser.add_argument("--branch", action="store_true", help="review the whole branch against the default branch")
    parser.add_argument("--repo", default=".", help="any directory inside the work tree")
    args = parser.parse_args()
    try:
        repo = repo_root(Path(args.repo).resolve())
        if args.command == "snapshot":
            sha, _, skipped = take_snapshot(repo)
            print(sha)
            if skipped:
                print(f"skipped untracked files over {MAX_UNTRACKED_BYTES // 1024 // 1024} MB: {', '.join(skipped)}")
            return 0
        return prepare(repo, args.branch)
    except GitError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
