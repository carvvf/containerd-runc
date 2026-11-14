"""Scan built binaries and libpathrs's Cargo.lock with Trivy and apply the publication gate.

The gate blocks fixable HIGH/CRITICAL findings and reports findings without a
released fix separately. A report must name every expected binary: Trivy
silently skips Go binaries that are not executable.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from .build_inputs import COMPONENTS, ROOT
from .modules import findings

CACHE = ROOT / ".cache/trivy"


def trivy_image():
    version = subprocess.run(["bash", str(ROOT / "scripts/trivy/resolve-version.sh")],
                             check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    return f"aquasec/trivy:{version}"


def make_executable(directory):
    for path in Path(directory).iterdir():
        if path.is_file():
            path.chmod(0o755)


def run_trivy(trivy, mode, target, report):
    """Run one Trivy scan as the invoking user; the JSON report is written from stdout."""
    CACHE.mkdir(parents=True, exist_ok=True)
    command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
               "-e", "TRIVY_DISABLE_TELEMETRY=true", "-e", "TRIVY_SKIP_VERSION_CHECK=true",
               "-v", f"{CACHE}:/cache", "-v", f"{Path(target).resolve()}:/target:ro",
               trivy, mode, "--cache-dir", "/cache", "--quiet", "--format", "json",
               "--scanners", "vuln", "--severity", "HIGH,CRITICAL", "--timeout", "30m",
               "--db-repository", "ghcr.io/aquasecurity/trivy-db:2",
               "--db-repository", "mirror.gcr.io/aquasec/trivy-db:2", "/target"]
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w") as output:
        subprocess.run(command, stdout=output, check=True)
    result = json.loads(report.read_text())
    if result.get("SchemaVersion") != 2 or not isinstance(result.get("Results", []), list):
        raise ValueError(f"Expected a Trivy schema 2 report: {report}")
    return result


def scanned_binaries(report):
    return sorted(Path(row.get("Target", "")).name for row in report.get("Results") or []
                  if row.get("Type") == "gobinary")


def require_binaries(report, expected):
    actual = scanned_binaries(report)
    if actual != sorted(expected):
        raise ValueError(f"Trivy analyzed Go binaries {actual}, expected {sorted(expected)}")


def require_cargo(report):
    if not any(row.get("Type") == "cargo" for row in report.get("Results") or []):
        raise ValueError("Trivy did not analyze libpathrs's Cargo.lock")


def scan_binaries(trivy, directory, expected, report):
    make_executable(directory)
    result = run_trivy(trivy, "rootfs", directory, report)
    require_binaries(result, expected)
    return result


def scan_cargo_lock(trivy, directory, report):
    result = run_trivy(trivy, "fs", directory, report)
    require_cargo(result)
    return result


def split_findings(*reports):
    rows = [row for report in reports for row in findings(report)]
    return [r for r in rows if r["fixed"]], [r for r in rows if not r["fixed"]]


def describe(rows):
    return [f"{r['target']}: {r['id']} {r['severity']} {r['package']} {r['installed']}"
            + (f" -> {r['fixed']}" if r["fixed"] else " (no fix released)") for r in rows]


def gate(*reports):
    """Raise on fixable findings; return the unfixed ones for reporting."""
    fixable, unfixed = split_findings(*reports)
    if fixable:
        raise ValueError("Fixable HIGH/CRITICAL findings block publication:\n" + "\n".join(describe(fixable)))
    return unfixed


def report_unfixed(rows, context):
    """Keep allowed residual findings visible without failing a successful publication."""
    if not rows:
        return
    messages = [f"{context}: {line}" for line in describe(rows)]
    for message in messages:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::warning title=Vulnerabilities without a released fix::{escaped}", flush=True)
        else:
            for line in message.splitlines():
                print(f"WARNING: {line}", flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write(f"\n{context}: {len(rows)} HIGH/CRITICAL findings without a released fix.\n\n")
            for message in messages:
                stream.write("    " + message.replace("\r", " ").replace("\n", " ") + "\n")


def main():
    parser = argparse.ArgumentParser(description="Rescan an existing local build with the shared gate.")
    parser.add_argument("--component", choices=sorted(COMPONENTS) + ["all"], default="all")
    args = parser.parse_args()
    trivy = trivy_image()
    subprocess.run(["docker", "pull", "-q", trivy], check=True, stdout=subprocess.DEVNULL)
    names = sorted(COMPONENTS) if args.component == "all" else [args.component]
    for name in names:
        directory = ROOT / "build" / name
        binaries = directory / "out/bin"
        if not binaries.is_dir():
            parser.error(f"No local build in {binaries}; run the local build and qualification first")
        reports = [scan_binaries(trivy, binaries, COMPONENTS[name].binaries, directory / "trivy-local.json")]
        if name == "runc":
            reports.append(scan_cargo_lock(trivy, directory / "out/libpathrs", directory / "trivy-local-cargo.json"))
        for line in describe(gate(*reports)):
            print(line)
        print(f"{name}: {trivy} found no fixable HIGH/CRITICAL findings.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Scan failed: {error}") from error
