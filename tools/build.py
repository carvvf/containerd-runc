"""Build one component from resolved inputs, and fetch the official binaries it replaces."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

from .build_inputs import COMPONENTS, ROOT, check_schema
from .github import download
from .modules import go_get_arguments


def build_arguments(inputs, suffix):
    check_schema(inputs)
    upstream = inputs["upstream"]
    arguments = {
        "BASE_IMAGE": f"{inputs['base']['image']}@{inputs['base']['digest']}",
        "GO_VERSION": inputs["go"]["version"],
        "GO_SHA256": inputs["go"]["sha256"],
        "UPSTREAM_TAG": upstream["tag"],
        "UPSTREAM_REVISION": upstream["revision"],
        "UPSTREAM_VERSION": upstream["version"],
        "BUILD_SUFFIX": suffix,
        "MODULE_OVERRIDES": " ".join(go_get_arguments(inputs["module_overrides"])),
    }
    if inputs["component"] == "runc":
        arguments["LIBSECCOMP_VERSION"] = inputs["dependencies"]["libseccomp"]["version"]
        arguments["LIBPATHRS_VERSION"] = inputs["dependencies"]["libpathrs"]["version"]
    return [item for key, value in arguments.items() for item in ["--build-arg", f"{key}={value}"]]


def buildx(inputs, suffix, target, output):
    name = inputs["component"]
    # No shared remote build cache: pull-request and master builds never exchange layers.
    command = ["docker", "buildx", "build", "--platform", "linux/amd64", "--pull",
               "--file", str(ROOT / "docker" / name / "Dockerfile"), "--target", target]
    command += output + build_arguments(inputs, suffix) + [str(ROOT / "docker")]
    subprocess.run(command, check=True)


def build(inputs, suffix, output, builder=None):
    """Export the built artifacts to output; optionally load the build stage for upstream tests."""
    if output.exists():
        shutil.rmtree(output)
    buildx(inputs, suffix, "artifacts", ["--output", f"type=local,dest={output}"])
    if builder:
        buildx(inputs, suffix, "build", ["--load", "--tag", builder])
    return output


def verified_download(asset):
    data = download(asset["url"])
    if hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise ValueError(f"{asset['name']} does not match its published SHA-256")
    return data


def fetch_official(inputs, directory):
    """Place the official release's executables in directory under their installed names."""
    component = COMPONENTS[inputs["component"]]
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    data = verified_download(inputs["official_asset"])
    if component.name == "runc":
        (directory / "runc").write_bytes(data)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for name in component.binaries:
                member = archive.getmember(f"bin/{name}")
                if not member.isfile():
                    raise ValueError(f"Unexpected archive member type: bin/{name}")
                (directory / name).write_bytes(archive.extractfile(member).read())
    for path in directory.iterdir():
        path.chmod(0o755)
    return directory


def binary_digests(directory, names):
    return {name: hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest() for name in names}
