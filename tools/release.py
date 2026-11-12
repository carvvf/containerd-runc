"""Read and publish the component releases of carvvf/containerd-runc.

Publication has two steps so the workflow can attest the exact files in
between: `stage` creates a draft release with every asset and verifies the
uploaded sizes and digests; `finalize` publishes that draft and verifies the
result. Published releases are never modified or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.parse

from .build_inputs import COMPONENTS, REPOSITORY, ROOT, active_tag, custom_versions
from .github import api_request, download, github_pages

RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
UPLOADS_URL = f"https://uploads.github.com/repos/{REPOSITORY}/releases"


def listing():
    """Every release, drafts included; drafts are listed only with write access."""
    return list(github_pages(RELEASES_URL + "?per_page=100", lambda url: api_request(url)))


def tag_names(releases):
    """All tags, drafts included, so a failed run's draft never gets its number reused."""
    return [item["tag_name"] for item in releases]


def published(releases):
    return [item for item in releases if not item.get("draft") and not item.get("prerelease")]


def active(name, releases):
    """The newest published custom release of a component, or None."""
    candidates = published(releases)
    tag = active_tag(name, [item["tag_name"] for item in candidates])
    return next((item for item in candidates if item["tag_name"] == tag), None)


def release_asset(release, name):
    matches = [a for a in release.get("assets", []) if a.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"{release.get('tag_name')}: expected one asset named {name}")
    return matches[0]


def verified_asset(release, name):
    """Download a release asset and check it against the digest GitHub reports for it."""
    item = release_asset(release, name)
    data = download(item["browser_download_url"])
    if "sha256:" + hashlib.sha256(data).hexdigest() != item.get("digest"):
        raise ValueError(f"{release['tag_name']}/{name} does not match its published digest")
    return data


def asset_json(release, name):
    return json.loads(verified_asset(release, name))


def release_version(release):
    for name in COMPONENTS:
        if custom_versions(name, [release["tag_name"]]):
            return name, release["tag_name"].removeprefix(name + "-")
    raise ValueError(f"Not a component release: {release['tag_name']}")


def fetch_binaries(release, directory):
    """Place a release's executables in directory under their installed names; return its version."""
    name, version = release_version(release)
    sums = parse_sums(verified_asset(release, "SHA256SUMS").decode())
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    asset_name = f"containerd-{version}-linux-amd64.tar.gz" if name == "containerd" else "runc.amd64"
    data = verified_asset(release, asset_name)
    if hashlib.sha256(data).hexdigest() != sums.get(asset_name):
        raise ValueError(f"{asset_name} does not match SHA256SUMS")
    if name == "runc":
        (directory / "runc").write_bytes(data)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for binary in COMPONENTS[name].binaries:
                member = archive.getmember(f"bin/{binary}")
                if not member.isfile():
                    raise ValueError(f"Unexpected archive member type: bin/{binary}")
                (directory / binary).write_bytes(archive.extractfile(member).read())
    for path in directory.iterdir():
        path.chmod(0o755)
    return version


def parse_sums(text):
    result = {}
    for line in text.splitlines():
        digest, _, name = line.partition("  ")
        if len(digest) != 64 or not name:
            raise ValueError("Malformed SHA256SUMS")
        result[name] = digest
    return result


def asset_files(name, record, directory):
    """The exact qualified files a release publishes, under their asset names."""
    out = directory / "out"
    files = {}
    if name == "containerd":
        tarball = f"containerd-{record['version']}-linux-amd64.tar.gz"
        files[tarball] = out / "release" / tarball
    else:
        for path in sorted((out / "release").iterdir()):
            files[path.name] = path
    files.update({
        "inputs.json": directory / "inputs.json",
        "build.json": directory / "build.json",
        "go.mod.diff": out / "go.mod.diff",
        "trivy.json": directory / "trivy-candidate.json",
    })
    if name == "runc":
        files["trivy-cargo.json"] = directory / "trivy-candidate-cargo.json"
    for asset_name, path in files.items():
        if not path.is_file():
            raise ValueError(f"Missing release file {asset_name}: {path}")
    return files


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def release_notes(name, record, inputs):
    upstream = inputs["upstream"]
    overrides = inputs["module_overrides"] or {"none": ""}
    lines = [
        f"Rebuild of {name} [{upstream['tag']}](https://github.com/{upstream['repository']}/releases/tag/{upstream['tag']}) "
        f"(`{upstream['revision']}`) for linux/amd64. Upstream code is unchanged.",
        "",
        f"1. Go toolchain: {inputs['go']['version']} (upstream's release build declares {inputs['go']['declared']}).",
        "2. Module upgrades: " + ", ".join(f"`{m}@{v}`" if v else m for m, v in overrides.items()) + ".",
        f"3. Trivy: {record['trivy']}; no fixable HIGH/CRITICAL findings; "
        f"{len(record['unfixed_findings'])} without a released fix.",
        "4. Upstream unit tests and the CRI smoke test passed.",
        f"5. Built from {REPOSITORY}@`{record['repository_revision']}`.",
        "",
        "Verify an asset with `sha256sum -c SHA256SUMS` and "
        f"`gh attestation verify <file> --repo {REPOSITORY}`.",
    ]
    return "\n".join(lines) + "\n"


def stage(name, record, directory):
    """Create a draft release with every asset; return the staging record."""
    releases = listing()
    if record["tag"] in tag_names(releases):
        raise ValueError(f"Immutable release tag already exists: {record['tag']}")
    files = asset_files(name, record, directory)
    staging = directory / "release-assets"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    for asset_name, path in files.items():
        shutil.copyfile(path, staging / asset_name)
    sums = "".join(f"{sha256_file(staging / n)}  {n}\n" for n in sorted(files))
    (staging / "SHA256SUMS").write_text(sums)
    inputs = json.loads((directory / "inputs.json").read_text())
    draft, _ = api_request(RELEASES_URL, method="POST", data={
        "tag_name": record["tag"], "target_commitish": record["repository_revision"],
        "name": record["tag"], "body": release_notes(name, record, inputs),
        "draft": True, "prerelease": False, "make_latest": "false",
    })
    for path in sorted(staging.iterdir()):
        url = f"{UPLOADS_URL}/{draft['id']}/assets?name={urllib.parse.quote(path.name)}"
        api_request(url, method="POST", data=path.read_bytes(), content_type="application/octet-stream")
    verify_assets(fetch_release(draft["id"]), staging)
    result = {"component": name, "tag": record["tag"], "release_id": draft["id"], "assets": str(staging)}
    (directory / "staged-release.json").write_text(json.dumps(result, indent=2) + "\n")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as stream:
            stream.write(f"staged=true\nassets={staging}\n")
    print(f"Staged draft release {record['tag']} with {len(list(staging.iterdir()))} assets.", flush=True)
    return result


def fetch_release(release_id):
    value, _ = api_request(f"{RELEASES_URL}/{int(release_id)}")
    return value


def verify_assets(release, staging):
    expected = {path.name: path for path in staging.iterdir()}
    actual = {item["name"]: item for item in release.get("assets", [])}
    if set(actual) != set(expected):
        raise ValueError(f"Release assets {sorted(actual)} differ from staged files {sorted(expected)}")
    for asset_name, path in expected.items():
        item = actual[asset_name]
        if item.get("state") != "uploaded" or item.get("size") != path.stat().st_size \
                or item.get("digest") != "sha256:" + sha256_file(path):
            raise ValueError(f"Uploaded asset {asset_name} differs from the staged file")


def finalize(name):
    """Publish the staged draft after attestation and verify the published release."""
    directory = ROOT / "build" / name
    staged = json.loads((directory / "staged-release.json").read_text())
    release = fetch_release(staged["release_id"])
    if not release.get("draft") or release.get("tag_name") != staged["tag"]:
        raise ValueError("The staged release is no longer the expected draft")
    staging = Path(staged["assets"])
    verify_assets(release, staging)
    api_request(f"{RELEASES_URL}/{int(staged['release_id'])}", method="PATCH",
                data={"draft": False, "make_latest": "false"})
    final = fetch_release(staged["release_id"])
    if final.get("draft") or final.get("tag_name") != staged["tag"]:
        raise ValueError("The release was not published as expected")
    verify_assets(final, staging)
    message = f"Published {staged['tag']}: {final['html_url']}"
    print(message, flush=True)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as stream:
            stream.write(message + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["finalize"])
    parser.add_argument("--component", choices=sorted(COMPONENTS), required=True)
    args = parser.parse_args()
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != "refs/heads/master"):
        raise ValueError(f"Publication is restricted to trusted master workflows in {REPOSITORY}")
    finalize(args.component)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
