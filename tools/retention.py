"""Delete completed workflow runs older than the retention window. Releases are never touched."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import sys

from .build_inputs import PRODUCER_VERSION, REPOSITORY, ROOT
from .github import api_request, github_pages

RUNS_URL = f"https://api.github.com/repos/{REPOSITORY}/actions/runs"
DAYS = 60


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def activity(item):
    values = [timestamp(item.get(key)) for key in ["created_at", "updated_at"]]
    return max(values) if all(values) else None


def list_runs(request=api_request):
    def page(url):
        value, links = request(url)
        if not isinstance(value, dict) or not isinstance(value.get("workflow_runs"), list):
            raise ValueError("Invalid workflow-run listing")
        return value["workflow_runs"], links
    return list(github_pages(RUNS_URL + "?per_page=100", request=page))


def plan_runs(runs, now, current_run):
    """Completed runs, other than the current one, last active before the cutoff."""
    cutoff = now - timedelta(days=DAYS)
    result = []
    for item in runs:
        if type(item.get("id")) is not int or item["id"] <= 0:
            raise ValueError("Invalid workflow run identity")
        date = activity(item)
        if (item.get("status") == "completed" and str(item["id"]) != current_run
                and date is not None and date < cutoff):
            result.append(item)
    return result


def summary(message):
    print(message, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as stream:
            stream.write(message + "\n")


def cleanup(dry_run=False, request=api_request):
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != "refs/heads/master"
            or os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}):
        raise ValueError(f"Retention is restricted to trusted master workflows in {REPOSITORY}")
    now = datetime.now(timezone.utc)
    runs = plan_runs(list_runs(request), now, os.environ.get("GITHUB_RUN_ID", ""))
    directory = ROOT / "build"
    directory.mkdir(exist_ok=True)
    plan = {"schema_version": 1, "producer_version": PRODUCER_VERSION, "dry_run": dry_run,
            "generated_at": now.isoformat(), "retention_days": DAYS,
            "delete_runs": [{"id": r["id"], "name": r.get("name"), "updated_at": r.get("updated_at")} for r in runs]}
    (directory / "retention-plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    summary(f"Retention: {len(runs)} completed workflow runs older than {DAYS} days; releases are never deleted; "
            f"dry_run={dry_run}.")
    result = {"schema_version": 1, "producer_version": PRODUCER_VERSION, "dry_run": dry_run,
              "deleted_runs": [], "skipped_runs": []}
    try:
        for item in runs:
            summary(f"{'DRY-RUN' if dry_run else 'DELETE'} completed workflow run {item['id']}")
            if dry_run:
                continue
            current, _ = request(f"{RUNS_URL}/{item['id']}")
            if current.get("status") != "completed" or activity(current) != activity(item):
                result["skipped_runs"].append(item["id"])
                continue
            request(f"{RUNS_URL}/{item['id']}", method="DELETE")
            result["deleted_runs"].append(item["id"])
    finally:
        (directory / "retention-result.json").write_text(json.dumps(result, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cleanup(args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Retention failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
