"""Resolve, build, scan, test and (on trusted master workflows) publish rebuilt binaries.

`check` qualifies candidates locally or on pull requests and never publishes.
`auto` compares each component with its active release and rebuilds, qualifies
and publishes it only when required.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import build, scan
from .build_inputs import (COMPONENTS, REPOSITORY, ROOT, needs_rebuild, next_version,
                           release_tag, resolve)
from .modules import overrides_from_report

MAX_ROUNDS = 3


def run(*args, capture=False, **kwargs):
    return subprocess.run(list(args), check=True, text=True,
                          stdout=subprocess.PIPE if capture else None, **kwargs)


def summary(message):
    print(message, flush=True)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as stream:
            stream.write(message + "\n")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def builder_image(name):
    return f"containerd-runc/{name}-builder:candidate"


def run_logged(command, log):
    """Run a command, keep its full output in log, and fail with the log's tail."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        tail = "".join(log.read_text(errors="replace").splitlines(keepends=True)[-30:])
        raise ValueError(f"{' '.join(command[:4])}... failed with exit status {result.returncode}; see {log}:\n{tail}")


def upstream_tests(name, log):
    """Run upstream's unit tests in the candidate's build environment."""
    image = builder_image(name)
    if name == "containerd":
        # `make test` excludes integration and root-only tests; run it as upstream intends, without root.
        command = ["docker", "run", "--rm", "--user", "1000:1000", "-e", "HOME=/tmp",
                   "-e", "GOCACHE=/tmp/gocache", "-e", "GOPATH=/tmp/gopath",
                   "-e", "GOFLAGS=-buildvcs=false", image, "make", "test"]
    else:
        # Upstream's `make unittest`: `make localunittest` as root in a privileged container.
        # The test helpers link libpathrs dynamically and their re-executed children lose
        # LD_LIBRARY_PATH, so the library directory is registered with the loader, as
        # upstream CI does by installing libpathrs into /usr. -vet=off: see AGENTS.md.
        command = ["docker", "run", "--rm", "--privileged", "-v", "/lib/modules:/lib/modules:ro", image,
                   "bash", "-c", "echo /opt/runc-dylibs/amd64/lib > /etc/ld.so.conf.d/runc-dylibs.conf"
                   " && ldconfig && exec script/prepare-cgroup-v2.sh make localunittest TESTFLAGS=-vet=off"]
    run_logged(command, log)


def scan_candidate(name, trivy, directory, label):
    reports = [scan.scan_binaries(trivy, directory / "out/bin", COMPONENTS[name].binaries,
                                  directory / f"trivy-{label}.json")]
    if name == "runc":
        reports.append(scan.scan_cargo_lock(trivy, directory / "out/libpathrs", directory / f"trivy-{label}-cargo.json"))
    return reports


def qualify(name, inputs, suffix, trivy, revision):
    """Build with iteratively derived module overrides until a scan requires no more; then test."""
    directory = ROOT / "build" / name
    official = build.fetch_official(inputs, directory / "official")
    report = scan.scan_binaries(trivy, official, COMPONENTS[name].binaries, directory / "trivy-official.json")
    inputs["module_overrides"] = overrides_from_report(report, inputs["go"]["version"])
    for round_number in range(1, MAX_ROUNDS + 1):
        summary(f"{name}: build round {round_number}, module overrides {inputs['module_overrides'] or 'none'}")
        build.build(inputs, suffix, directory / "out", builder=builder_image(name))
        reports = scan_candidate(name, trivy, directory, "candidate")
        required = overrides_from_report(reports[0], inputs["go"]["version"], inputs["module_overrides"])
        if required == inputs["module_overrides"]:
            break
        inputs["module_overrides"] = required
    else:
        raise ValueError(f"{name}: module overrides still changing after {MAX_ROUNDS} build rounds")
    unfixed = scan.gate(*reports)
    write_json(directory / "inputs.json", inputs)
    upstream_tests(name, directory / "upstream-tests.log")
    version = f"{inputs['upstream']['version']}-{suffix}"
    record = {
        "component": name,
        "version": version,
        "tag": release_tag(name, version),
        "repository": REPOSITORY,
        "repository_revision": revision,
        "go_version": (directory / "out/go-version.txt").read_text().strip(),
        "trivy": trivy,
        "binaries_sha256": build.binary_digests(directory / "out/bin", COMPONENTS[name].binaries),
        "unfixed_findings": scan.describe(unfixed),
    }
    write_json(directory / "build.json", record)
    summary(f"{name} {version}: no fixable HIGH/CRITICAL findings, {len(unfixed)} without a released fix; "
            "upstream unit tests passed.")
    return record


def smoke(containerd_bin, runc_binary, containerd_version, runc_version, log):
    run_logged(["bash", str(ROOT / "tests/smoke-cri.sh"), str(containerd_bin), str(runc_binary),
                containerd_version, runc_version, str(log)], log.with_suffix(".run.log"))
    summary(f"CRI smoke test passed: containerd {containerd_version} with runc {runc_version}.")


def partner(name, releases):
    """Binaries of the other component for the smoke test: its active release, else the official one."""
    from . import release
    other = "runc" if name == "containerd" else "containerd"
    directory = ROOT / "build" / other / "partner"
    if directory.exists():
        shutil.rmtree(directory)
    active = release.active(other, releases)
    if active:
        version = release.fetch_binaries(active, directory)
    else:
        inputs = resolve(other)
        build.fetch_official(inputs, directory)
        version = inputs["upstream"]["version"]
    return directory, version


def trusted_publication():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != "refs/heads/master"
            or os.environ.get("GITHUB_EVENT_NAME") not in {"push", "schedule", "workflow_dispatch"}):
        raise ValueError(f"Publication is restricted to trusted master workflows in {REPOSITORY}")


def check(names, trivy, revision):
    records = {}
    for name in names:
        records[name] = qualify(name, resolve(name), "check", trivy, revision)
    if set(names) == set(COMPONENTS):
        smoke(ROOT / "build/containerd/out/bin", ROOT / "build/runc/out/bin/runc",
              records["containerd"]["version"], records["runc"]["version"], ROOT / "build/smoke.log")
    else:
        from . import release
        name = names[0]
        other_dir, other_version = partner(name, release.listing())
        candidate = ROOT / "build" / name / "out/bin"
        if name == "containerd":
            smoke(candidate, other_dir / "runc", records[name]["version"], other_version, ROOT / "build/smoke.log")
        else:
            smoke(other_dir, candidate / "runc", other_version, records[name]["version"], ROOT / "build/smoke.log")
    summary("Candidates passed scan, upstream tests and the CRI smoke test; publication is disabled for this run.")


def auto(name, trivy, revision, force=False):
    from . import release
    trusted_publication()
    directory = ROOT / "build" / name
    inputs = resolve(name)
    releases = release.listing()
    active = release.active(name, releases)
    active_inputs = active_revision = None
    findings = False
    if active:
        active_inputs = release.asset_json(active, "inputs.json")
        active_revision = release.asset_json(active, "build.json").get("repository_revision")
        binaries = directory / "active"
        if binaries.exists():
            shutil.rmtree(binaries)
        release.fetch_binaries(active, binaries)
        fixable, _ = scan.split_findings(scan.scan_binaries(
            trivy, binaries, COMPONENTS[name].binaries, directory / "trivy-active.json"))
        findings = bool(fixable)
        summary(f"{name}: active release {active['tag_name']}, fixable findings: {len(fixable)}")
    if not needs_rebuild(inputs, active_inputs, active_revision, revision, findings=findings, force=force):
        summary(f"{name}: no rebuild required; active inputs, revision and vulnerability scan are current.")
        return
    version = next_version(name, inputs["upstream"]["version"], release.tag_names(releases))
    record = qualify(name, inputs, version.removeprefix(inputs["upstream"]["version"] + "-"), trivy, revision)
    other_dir, other_version = partner(name, releases)
    candidate = directory / "out/bin"
    if name == "containerd":
        smoke(candidate, other_dir / "runc", version, other_version, directory / "smoke.log")
    else:
        smoke(other_dir, candidate / "runc", other_version, version, directory / "smoke.log")
    release.stage(name, record, directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "auto"])
    parser.add_argument("--component", choices=sorted(COMPONENTS) + ["all"], default="all")
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    names = sorted(COMPONENTS) if args.component == "all" else [args.component]
    if args.mode == "auto" and len(names) != 1:
        parser.error("auto publishes one component per run; pass --component")
    revision = run("git", "rev-parse", "HEAD", capture=True).stdout.strip()
    trivy = scan.trivy_image()
    summary(f"Trivy: {trivy}; repository revision: {revision}")
    subprocess.run(["docker", "pull", "-q", trivy], check=True, stdout=subprocess.DEVNULL)
    if args.mode == "check":
        check(names, trivy, revision)
    else:
        auto(names[0], trivy, revision, args.force_rebuild)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
