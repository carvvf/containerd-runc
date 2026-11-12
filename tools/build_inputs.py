"""Resolve verified, immutable build inputs for each component, shared by local builds and CI."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess

from .github import download, github_pages, request_json

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
PRODUCER_VERSION = "0.1.0"
PLATFORM = "linux/amd64"
GO_RELEASES_URL = "https://go.dev/dl/?mode=json&include=all"
REPOSITORY = "carvvf/containerd-runc"


@dataclass(frozen=True)
class Component:
    name: str
    repository: str
    series: tuple
    floor: tuple
    go_file: str
    base_image: str
    official_asset: str
    binaries: tuple


COMPONENTS = {
    "containerd": Component(
        name="containerd",
        repository="containerd/containerd",
        series=(2, 3),
        floor=(2, 3, 7),
        go_file=".github/workflows/release/Dockerfile",
        base_image="ubuntu:22.04",
        official_asset="containerd-{version}-linux-amd64.tar.gz",
        binaries=("containerd", "containerd-shim-runc-v2", "containerd-stress", "ctr"),
    ),
    "runc": Component(
        name="runc",
        repository="opencontainers/runc",
        series=(1, 5),
        floor=(1, 5, 2),
        go_file="Dockerfile",
        base_image="debian:trixie",
        official_asset="runc.amd64",
        binaries=("runc",),
    ),
}


def parse_version(text):
    if not re.fullmatch(r"[0-9]+(\.[0-9]+){1,2}", text):
        raise ValueError(f"Invalid version: {text}")
    return tuple(map(int, text.split(".")))


def release_version(release, series, floor):
    """Return the version tuple of a stable release in the reviewed line, else None."""
    if release.get("draft") or release.get("prerelease"):
        return None
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)", release.get("tag_name", ""))
    if not match:
        return None
    parts = parse_version(match[1])
    return parts if parts[:2] == series and parts >= floor else None


def select_release(releases, series, floor):
    candidates = [(v, item) for item in releases if (v := release_version(item, series, floor))]
    if not candidates:
        raise ValueError(f"No stable release at or above {floor} in series {series}")
    return max(candidates, key=lambda pair: pair[0])[1]


def asset(release, name):
    """Require exactly one asset with a GitHub-published SHA-256 digest."""
    matches = [a for a in release.get("assets", []) if a.get("name") == name]
    if len(matches) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", matches[0].get("digest") or ""):
        raise ValueError(f"A published SHA-256 digest is required for {name}")
    url = matches[0]["browser_download_url"]
    if not url.startswith("https://github.com/"):
        raise ValueError("Unexpected release asset URL")
    return {"name": name, "url": url, "sha256": matches[0]["digest"].removeprefix("sha256:")}


def raw_file(repository, revision, path, fetch=download):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Raw files are read at an immutable commit only")
    return fetch(f"https://raw.githubusercontent.com/{repository}/{revision}/{path}").decode()


def declared_go(text):
    """The Go version upstream's release build pins (containerd: patch, runc: minor)."""
    matches = re.findall(r"^ARG GO_VERSION=([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*$", text, re.M)
    if len(matches) != 1:
        raise ValueError("Expected exactly one ARG GO_VERSION in upstream's release build")
    return matches[0]


def go_directive(go_mod):
    matches = re.findall(r"^go ([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*$", go_mod, re.M)
    if len(matches) != 1:
        raise ValueError("Expected exactly one go directive in go.mod")
    return matches[0]


def select_go(releases, declared):
    """Newest stable patch of the declared minor, never older than a declared patch."""
    wanted = parse_version(declared)
    candidates = []
    for release in releases:
        match = re.fullmatch(r"go([0-9]+\.[0-9]+\.[0-9]+)", release.get("version", ""))
        if not release.get("stable") or not match:
            continue
        version = parse_version(match[1])
        if version[:2] != wanted[:2] or version < wanted:
            continue
        files = [f for f in release.get("files", [])
                 if f.get("os") == "linux" and f.get("arch") == "amd64" and f.get("kind") == "archive"]
        if len(files) != 1 or not re.fullmatch(r"[0-9a-f]{64}", files[0].get("sha256", "")):
            raise ValueError(f"Expected one checksummed linux/amd64 archive for go{match[1]}")
        candidates.append((version, {"version": match[1], "sha256": files[0]["sha256"]}))
    if not candidates:
        raise ValueError(f"No stable Go {wanted[0]}.{wanted[1]} release at or above {declared}")
    return max(candidates, key=lambda pair: pair[0])[1]


def runc_dependencies(release_script, seccomp_script, pathrs_script):
    """Versions declared by the runc release and the checksums its own scripts verify."""
    result = {}
    for name, variable, script, table in [
        ("libseccomp", "LIBSECCOMP_VERSION", seccomp_script, "SECCOMP_SHA256"),
        ("libpathrs", "LIBPATHRS_VERSION", pathrs_script, "LIBPATHRS_SHA256"),
    ]:
        versions = re.findall(r'^: "\$\{' + variable + r':=([0-9.]+)\}"\s*$', release_script, re.M)
        if len(versions) != 1:
            raise ValueError(f"Expected one default {variable} in script/release_build.sh")
        block = re.search(r"declare -A " + table + r"=\((.*?)\)", script, re.S)
        sums = dict(re.findall(r'\["([0-9.]+)"\]=([0-9a-f]{64})', block[1])) if block else {}
        if versions[0] not in sums:
            raise ValueError(f"No upstream checksum for {name} {versions[0]}")
        result[name] = {"version": versions[0], "sha256": sums[versions[0]]}
    return result


def image_digest(reference):
    output = subprocess.check_output(["docker", "buildx", "imagetools", "inspect", reference], text=True)
    match = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})\s*$", output, re.M)
    if not match:
        raise ValueError(f"Cannot resolve image digest: {reference}")
    return match[1]


def resolve(name, fetch_json=request_json, fetch=download, digest=image_digest):
    """Resolve one component's inputs; module overrides are added later by qualification."""
    component = COMPONENTS[name]
    releases = github_pages(f"https://api.github.com/repos/{component.repository}/releases?per_page=100", fetch_json)
    release = select_release(releases, component.series, component.floor)
    tag = release["tag_name"]
    version = tag.removeprefix("v")
    commit, _ = fetch_json(f"https://api.github.com/repos/{component.repository}/commits/{tag}")
    revision = commit.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Invalid upstream source revision")
    official = asset(release, component.official_asset.format(version=version))
    declared = declared_go(raw_file(component.repository, revision, component.go_file, fetch))
    go = select_go(json.loads(fetch(GO_RELEASES_URL)), declared)
    result = {
        "schema_version": SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
        "component": name,
        "platform": PLATFORM,
        "upstream": {"repository": component.repository, "tag": tag, "version": version, "revision": revision},
        "official_asset": official,
        "go": dict(go, declared=declared),
        "go_directive": go_directive(raw_file(component.repository, revision, "go.mod", fetch)),
        "base": {"image": component.base_image, "digest": digest(component.base_image)},
        "module_overrides": {},
    }
    if name == "runc":
        result["dependencies"] = runc_dependencies(
            raw_file(component.repository, revision, "script/release_build.sh", fetch),
            raw_file(component.repository, revision, "script/build-seccomp.sh", fetch),
            raw_file(component.repository, revision, "script/build-libpathrs.sh", fetch),
        )
    return result


def check_schema(inputs):
    if inputs.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported input schema; supported schema versions: {SCHEMA_VERSION}")
    if inputs.get("component") not in COMPONENTS:
        raise ValueError("Unknown component in input manifest")
    return inputs


def fingerprint(inputs):
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def release_tag(name, version):
    return f"{name}-{version}"


def custom_versions(name, tags):
    """Map every published custom release of a component to (upstream version tuple, N)."""
    pattern = re.compile(re.escape(name) + r"-([0-9]+\.[0-9]+\.[0-9]+)-custom\.([1-9][0-9]*)")
    return {tag: (parse_version(m[1]), int(m[2])) for tag in tags if (m := pattern.fullmatch(tag))}


def next_version(name, version, tags):
    numbers = [n for upstream, n in custom_versions(name, tags).values() if upstream == parse_version(version)]
    return f"{version}-custom.{max(numbers, default=0) + 1}"


def active_tag(name, tags):
    """The newest custom release of a component: highest upstream version, then highest N."""
    versions = custom_versions(name, tags)
    return max(versions, key=versions.get) if versions else None


def base_fingerprint(inputs):
    """Fingerprint of the resolved inputs, excluding qualification-derived module overrides.

    Overrides are derived from scans during each build; new module findings in
    an active release are detected by rescanning it instead.
    """
    return fingerprint({key: value for key, value in inputs.items() if key != "module_overrides"})


def needs_rebuild(inputs, active_inputs, active_revision, revision, findings=False, force=False):
    return (force or findings or active_inputs is None
            or base_fingerprint(inputs) != base_fingerprint(active_inputs)
            or active_revision != revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=sorted(COMPONENTS), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    text = json.dumps(resolve(args.component), indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
