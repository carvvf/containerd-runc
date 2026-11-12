"""Derive targeted Go module upgrades from Trivy findings.

Only modules with fixable findings are raised, each to the smallest fixed
version within its current major. The standard library is fixed by the
toolchain selection, never by module upgrades.
"""

from __future__ import annotations

import re

SEMVER = re.compile(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)")


def semver(text):
    match = SEMVER.fullmatch(text.strip())
    if not match:
        raise ValueError(f"Unsupported module version: {text}")
    return tuple(map(int, match.groups()))


def installed_semver(text):
    """Installed versions may be pseudo-versions; their release triple orders them."""
    match = re.match(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:[-+].*)?$", text.strip())
    if not match:
        raise ValueError(f"Unsupported installed module version: {text}")
    return tuple(map(int, match.groups()))


def module_version(parts):
    return "v{}.{}.{}".format(*parts)


def findings(report):
    """Flatten a Trivy JSON report into finding rows."""
    rows = []
    for result in report.get("Results") or []:
        for item in result.get("Vulnerabilities") or []:
            rows.append({
                "target": result.get("Target", ""),
                "type": result.get("Type", ""),
                "id": item.get("VulnerabilityID", ""),
                "package": item.get("PkgName", ""),
                "installed": item.get("InstalledVersion", ""),
                "fixed": item.get("FixedVersion") or "",
                "severity": item.get("Severity", ""),
            })
    return rows


def smallest_fix(installed, fixed):
    """Smallest listed fixed version above the installed one, in the same major."""
    current = installed_semver(installed)
    candidates = []
    for text in re.split(r"[,\s|]+", fixed):
        if not text:
            continue
        try:
            version = semver(text)
        except ValueError:
            continue
        if version[0] == current[0] and version > current:
            candidates.append(version)
    if not candidates:
        raise ValueError(f"No fixed version above {installed} within its major in {fixed!r}")
    return min(candidates)


def overrides_from_report(report, go_version, current=None):
    """Merge the module upgrades a report requires into the current overrides.

    Standard-library findings must already be fixed by the selected toolchain
    go_version; a fix that exists only in another Go minor is reported.
    """
    result = {module: semver(version) for module, version in (current or {}).items()}
    toolchain = semver(go_version)
    stdlib = []
    for row in findings(report):
        if not row["fixed"] or row["type"] != "gobinary":
            continue
        if row["package"] == "stdlib":
            if smallest_fix(row["installed"], row["fixed"]) > toolchain:
                stdlib.append(row)
            continue
        fix = smallest_fix(row["installed"], row["fixed"])
        result[row["package"]] = max(result.get(row["package"], fix), fix)
    if stdlib:
        details = ", ".join(sorted({f"{r['id']} (installed {r['installed']}, fixed {r['fixed']})" for r in stdlib}))
        raise ValueError(
            f"Go {go_version}, the newest patch of the reviewed minor, does not fix these standard-library "
            f"findings; moving to another Go minor is a reviewed change: {details}"
        )
    return {module: module_version(version) for module, version in sorted(result.items())}


def go_get_arguments(overrides):
    for module, version in overrides.items():
        if not re.fullmatch(r"[A-Za-z0-9.\-_~/]+", module) or not SEMVER.fullmatch(version):
            raise ValueError(f"Invalid module override: {module}@{version}")
    return [f"{module}@{version}" for module, version in sorted(overrides.items())]
