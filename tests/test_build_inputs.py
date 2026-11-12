"""Offline regression tests for input resolution and release decisions."""

import json
import subprocess
import unittest
from unittest.mock import patch

from tools import build_inputs as inputs
from tools import github

REVISION = "a" * 40
SUM = "b" * 64


def release(tag, **extra):
    return {"tag_name": tag, "draft": False, "prerelease": False, **extra}


class GitHubAuthenticationTests(unittest.TestCase):
    def setUp(self):
        github.cli_github_token.cache_clear()
        self.addCleanup(github.cli_github_token.cache_clear)

    def test_explicit_tokens_take_precedence_over_cli_credentials(self):
        with patch.dict(github.os.environ, {"GH_TOKEN": "first", "GITHUB_TOKEN": "second"}, clear=True), \
                patch.object(github, "cli_github_token") as cli:
            self.assertEqual(github.github_authentication(), ("first", "GH_TOKEN"))
            cli.assert_not_called()

    def test_existing_cli_login_is_reused_once_per_process(self):
        result = subprocess.CompletedProcess([], 0, "cli-credential\n", "")
        with patch.dict(github.os.environ, {}, clear=True), \
                patch.object(github.subprocess, "run", return_value=result) as command:
            self.assertEqual(github.github_authentication(), ("cli-credential", "GitHub CLI"))
            self.assertEqual(github.github_authentication(), ("cli-credential", "GitHub CLI"))
            self.assertEqual(command.call_count, 1)

    def test_pagination_rejects_foreign_next_links(self):
        pages = iter([([1], '<https://example.com/next>; rel="next"')])
        with self.assertRaises(ValueError):
            list(github.github_pages("https://api.github.com/x", lambda url: next(pages)))

    def test_api_request_never_sends_credentials_elsewhere(self):
        with self.assertRaises(ValueError):
            github.api_request("https://example.com/api")


class ReleaseSelectionTests(unittest.TestCase):
    def test_selects_newest_stable_release_in_line_at_or_above_floor(self):
        releases = [release("v2.3.6"), release("v2.3.7"), release("v2.3.9"), release("v2.4.2"),
                    release("v2.3.10", prerelease=True), release("v2.3.11", draft=True),
                    release("v2.3.12-rc.1"), release("api/v1.10.0")]
        chosen = inputs.select_release(releases, (2, 3), (2, 3, 7))
        self.assertEqual(chosen["tag_name"], "v2.3.9")

    def test_versions_compare_numerically(self):
        chosen = inputs.select_release([release("v1.5.9"), release("v1.5.10")], (1, 5), (1, 5, 2))
        self.assertEqual(chosen["tag_name"], "v1.5.10")

    def test_no_release_in_line_fails(self):
        with self.assertRaises(ValueError):
            inputs.select_release([release("v1.5.1"), release("v1.6.0")], (1, 5), (1, 5, 2))

    def test_asset_requires_one_published_digest(self):
        url = "https://github.com/opencontainers/runc/releases/download/v1.5.2/runc.amd64"
        good = {"assets": [{"name": "runc.amd64", "digest": "sha256:" + SUM, "browser_download_url": url}]}
        self.assertEqual(inputs.asset(good, "runc.amd64")["sha256"], SUM)
        for broken in [{"assets": []},
                       {"assets": [{"name": "runc.amd64", "digest": None, "browser_download_url": url}]},
                       {"assets": [{"name": "runc.amd64", "digest": "sha256:" + SUM,
                                    "browser_download_url": "https://example.com/runc"}]}]:
            with self.assertRaises(ValueError):
                inputs.asset(broken, "runc.amd64")

    def test_raw_files_are_read_at_commits_only(self):
        with self.assertRaises(ValueError):
            inputs.raw_file("opencontainers/runc", "v1.5.2", "go.mod", fetch=lambda url: b"")


class GoSelectionTests(unittest.TestCase):
    GO = [
        {"version": "go1.27.4", "stable": True, "files": []},
        {"version": "go1.26.11", "stable": True,
         "files": [{"os": "linux", "arch": "amd64", "kind": "archive", "sha256": SUM},
                   {"os": "linux", "arch": "arm64", "kind": "archive", "sha256": "c" * 64}]},
        {"version": "go1.26.12rc1", "stable": False, "files": []},
        {"version": "go1.26.9", "stable": True,
         "files": [{"os": "linux", "arch": "amd64", "kind": "archive", "sha256": "d" * 64}]},
    ]

    def test_declared_go_reads_the_single_release_argument(self):
        self.assertEqual(inputs.declared_go("ARG GO_VERSION=1.26.9\nFROM x\n"), "1.26.9")
        self.assertEqual(inputs.declared_go("ARG GO_VERSION=1.26\n"), "1.26")
        with self.assertRaises(ValueError):
            inputs.declared_go("ARG GO_VERSION=1.26\nARG GO_VERSION=1.27\n")

    def test_newest_patch_of_declared_minor_is_selected(self):
        self.assertEqual(inputs.select_go(self.GO, "1.26"), {"version": "1.26.11", "sha256": SUM})
        self.assertEqual(inputs.select_go(self.GO, "1.26.9")["version"], "1.26.11")

    def test_never_older_than_declared_patch_or_other_minor(self):
        with self.assertRaises(ValueError):
            inputs.select_go(self.GO, "1.26.12")
        with self.assertRaises(ValueError):
            inputs.select_go(self.GO, "1.25")

    def test_go_directive(self):
        self.assertEqual(inputs.go_directive("module x\n\ngo 1.25.0\n\ntoolchain go1.26.1\n"), "1.25.0")


class RuncDependencyTests(unittest.TestCase):
    RELEASE = ': "${LIBSECCOMP_VERSION:=2.6.1}"\n: "${LIBPATHRS_VERSION:=0.2.6}"\n'
    SECCOMP = 'declare -A SECCOMP_SHA256=(\n\t["2.6.0"]=' + "1" * 64 + '\n\t["2.6.1"]=' + "2" * 64 + "\n)\n"
    PATHRS = 'declare -A LIBPATHRS_SHA256=(\n\t["0.2.6"]=' + "3" * 64 + "\n)\n"

    def test_versions_and_upstream_checksums(self):
        result = inputs.runc_dependencies(self.RELEASE, self.SECCOMP, self.PATHRS)
        self.assertEqual(result["libseccomp"], {"version": "2.6.1", "sha256": "2" * 64})
        self.assertEqual(result["libpathrs"], {"version": "0.2.6", "sha256": "3" * 64})

    def test_missing_checksum_fails(self):
        with self.assertRaises(ValueError):
            inputs.runc_dependencies(self.RELEASE.replace("0.2.6", "0.2.7"), self.SECCOMP, self.PATHRS)


class ResolveTests(unittest.TestCase):
    def test_resolve_runc_records_every_input(self):
        url = "https://github.com/opencontainers/runc/releases/download/v1.5.2/runc.amd64"
        api = {
            "https://api.github.com/repos/opencontainers/runc/releases?per_page=100": (
                [release("v1.5.2", assets=[{"name": "runc.amd64", "digest": "sha256:" + SUM,
                                            "browser_download_url": url}]), release("v1.4.9")], ""),
            "https://api.github.com/repos/opencontainers/runc/commits/v1.5.2": ({"sha": REVISION}, ""),
        }
        raw = f"https://raw.githubusercontent.com/opencontainers/runc/{REVISION}/"
        files = {
            raw + "Dockerfile": b"ARG GO_VERSION=1.26\n",
            raw + "go.mod": b"module github.com/opencontainers/runc\n\ngo 1.25.0\n",
            raw + "script/release_build.sh": RuncDependencyTests.RELEASE.encode(),
            raw + "script/build-seccomp.sh": RuncDependencyTests.SECCOMP.encode(),
            raw + "script/build-libpathrs.sh": RuncDependencyTests.PATHRS.encode(),
            inputs.GO_RELEASES_URL: json.dumps(GoSelectionTests.GO).encode(),
        }
        result = inputs.resolve("runc", fetch_json=api.__getitem__, fetch=files.__getitem__,
                                digest=lambda ref: "sha256:" + "e" * 64)
        self.assertEqual(result["upstream"], {"repository": "opencontainers/runc", "tag": "v1.5.2",
                                              "version": "1.5.2", "revision": REVISION})
        self.assertEqual(result["go"], {"version": "1.26.11", "sha256": SUM, "declared": "1.26"})
        self.assertEqual(result["go_directive"], "1.25.0")
        self.assertEqual(result["base"]["image"], "debian:trixie")
        self.assertEqual(result["module_overrides"], {})
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["dependencies"]["libpathrs"]["version"], "0.2.6")


class ReleaseDecisionTests(unittest.TestCase):
    TAGS = ["containerd-2.3.7-custom.1", "containerd-2.3.7-custom.2", "containerd-2.3.8-custom.1",
            "runc-1.5.2-custom.9", "runc-1.5.2-custom.10", "containerd-2.3.7-check", "v2.3.7"]

    def test_next_version_counts_per_component_and_upstream_version(self):
        self.assertEqual(inputs.next_version("containerd", "2.3.7", self.TAGS), "2.3.7-custom.3")
        self.assertEqual(inputs.next_version("containerd", "2.3.9", self.TAGS), "2.3.9-custom.1")
        self.assertEqual(inputs.next_version("runc", "1.5.2", self.TAGS), "1.5.2-custom.11")

    def test_active_tag_is_the_newest_custom_release(self):
        self.assertEqual(inputs.active_tag("containerd", self.TAGS), "containerd-2.3.8-custom.1")
        self.assertEqual(inputs.active_tag("runc", self.TAGS), "runc-1.5.2-custom.10")
        self.assertIsNone(inputs.active_tag("runc", ["containerd-2.3.7-custom.1"]))

    def test_rebuild_conditions(self):
        current = {"schema_version": 1, "component": "runc", "go": {"version": "1.26.11"}}
        self.assertFalse(inputs.needs_rebuild(current, dict(current), "r1", "r1"))
        self.assertTrue(inputs.needs_rebuild(current, None, None, "r1"))
        self.assertTrue(inputs.needs_rebuild(current, dict(current, go={"version": "1.26.12"}), "r1", "r1"))
        self.assertTrue(inputs.needs_rebuild(current, dict(current), "r0", "r1"))
        self.assertTrue(inputs.needs_rebuild(current, dict(current), "r1", "r1", findings=True))
        self.assertTrue(inputs.needs_rebuild(current, dict(current), "r1", "r1", force=True))

    def test_module_overrides_alone_do_not_trigger_a_rebuild(self):
        current = {"schema_version": 1, "component": "runc", "module_overrides": {}}
        active = dict(current, module_overrides={"golang.org/x/net": "v0.62.0"})
        self.assertFalse(inputs.needs_rebuild(current, active, "r1", "r1"))

    def test_unsupported_schema_fails(self):
        with self.assertRaises(ValueError):
            inputs.check_schema({"schema_version": 2, "component": "runc"})


if __name__ == "__main__":
    unittest.main()
