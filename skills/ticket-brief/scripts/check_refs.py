"""Re-check code claims at a git ref, all in one call.

Each claim is one of:

    path:line:text     line `line` contains `text`
    path:a-b:text      some line from a to b contains `text`
    path::text         the file contains `text` somewhere

Pass claims as arguments, or `@<file>` to read one claim per line from a file:

    python3 -I check_refs.py <ref> [--repo <dir>] <claim> [<claim> ...]

Prints one row per claim: OK, MOVED (with the lines where the text is now),
MISSING (text not in the file) or NO FILE. Exits 1 if any claim is not OK.
"""

import argparse
import subprocess
import sys
from functools import cache

MAX_LINES_SHOWN = 5


def parse_args():
    parser = argparse.ArgumentParser(description="Re-check path:line:text claims at a git ref.")
    parser.add_argument("ref", help="sha, branch or tag to read, e.g. origin/master")
    parser.add_argument("claims", nargs="+", help="path:line:text, path:a-b:text, path::text, or @file")
    parser.add_argument("--repo", default=".", help="repository directory (default: current directory)")
    return parser.parse_args()


def expand(claims):
    for claim in claims:
        if claim.startswith("@"):
            with open(claim[1:]) as handle:
                yield from (line.strip() for line in handle if line.strip())
        else:
            yield claim


@cache
def file_lines(repo, ref, path):
    result = subprocess.run(["git", "-C", repo, "show", f"{ref}:{path}"], capture_output=True, text=True)
    return result.stdout.splitlines() if result.returncode == 0 else None


def line_span(spec):
    if not spec:
        return None
    start, _, end = spec.partition("-")
    return int(start), int(end or start)


def check(repo, ref, claim):
    path, spec, text = claim.split(":", 2)
    lines = file_lines(repo, ref, path)
    if lines is None:
        return "NO FILE", ""
    hits = [number for number, content in enumerate(lines, start=1) if text in content]
    span = line_span(spec)
    if not hits:
        return "MISSING", ""
    if span is None or any(span[0] <= hit <= span[1] for hit in hits):
        return "OK", ""
    return "MOVED", "now at " + ", ".join(str(hit) for hit in hits[:MAX_LINES_SHOWN])


def main():
    args = parse_args()
    failures = 0
    for claim in expand(args.claims):
        status, detail = check(args.repo, args.ref, claim)
        failures += status != "OK"
        print(f"{status:8s} {claim}  {detail}".rstrip())
    print(f"{failures} of the claims above are not OK at {args.ref}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
