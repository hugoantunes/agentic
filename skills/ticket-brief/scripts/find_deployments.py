"""Find Kubernetes deployments by name across one or more kube contexts. Read-only.

    python3 -I find_deployments.py <name-part> [<name-part> ...] [--context <ctx> ...]

Runs `kubectl get deploy -A -o json` once per context and prints only the deployments
whose name contains one of the name parts (case-insensitive): context, namespace, name,
ready/desired replicas and image. For each name part with no match, it says so and how
many deployments it checked. The full JSON, which can hold env values, never leaves this process.

With no `--context`, it uses the current kube context. Pass `--context` once per cluster to
check several in one call.
"""

import argparse
import json
import subprocess

KUBECTL_TIMEOUT_SECONDS = 60


def parse_args():
    parser = argparse.ArgumentParser(description="Find deployments by name across clusters.")
    parser.add_argument("name_parts", nargs="+", help="substrings of deployment names")
    parser.add_argument("--context", action="append", help="kube context, repeatable (default: the current context)")
    return parser.parse_args()


def current_context():
    result = subprocess.run(
        ["kubectl", "config", "current-context"],
        capture_output=True,
        text=True,
        timeout=KUBECTL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "no current kube context")
    return result.stdout.strip()


def list_deployments(context):
    result = subprocess.run(
        ["kubectl", "get", "deploy", "-A", "--context", context, "-o", "json"],
        capture_output=True,
        text=True,
        timeout=KUBECTL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)["items"]


def describe(deployment):
    metadata = deployment["metadata"]
    ready = deployment.get("status", {}).get("readyReplicas", 0)
    desired = deployment["spec"].get("replicas", 0)
    images = ",".join(container["image"].rsplit("/", 1)[-1] for container in deployment["spec"]["template"]["spec"]["containers"])
    return f"{metadata['namespace']}\t{metadata['name']}\t{ready}/{desired}\t{images}"


def report(context, deployments, name_part):
    found = [deployment for deployment in deployments if name_part.lower() in deployment["metadata"]["name"].lower()]
    for deployment in found:
        print(f"{context}\t{describe(deployment)}")
    if not found:
        print(f"{context}\t-\tno name contains '{name_part}' ({len(deployments)} deployments checked)")


def main():
    args = parse_args()
    contexts = args.context or [current_context()]
    print("context\tnamespace\tname\tready\timages")
    for context in contexts:
        try:
            deployments = list_deployments(context)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"{context}\tERROR\t{error}")
            continue
        for name_part in args.name_parts:
            report(context, deployments, name_part)


if __name__ == "__main__":
    main()
