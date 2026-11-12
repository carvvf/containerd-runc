"""Offline regression tests for release discovery and publication safety."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import release


def item(tag, draft=False, prerelease=False, assets=()):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease, "assets": list(assets)}


class DiscoveryTests(unittest.TestCase):
    RELEASES = [item("containerd-2.3.7-custom.1"), item("containerd-2.3.7-custom.2", draft=True),
                item("containerd-2.3.8-custom.1", prerelease=True), item("runc-1.5.2-custom.1")]

    def test_drafts_count_for_allocation_but_not_as_active(self):
        self.assertIn("containerd-2.3.7-custom.2", release.tag_names(self.RELEASES))
        self.assertEqual(release.active("containerd", self.RELEASES)["tag_name"], "containerd-2.3.7-custom.1")
        self.assertEqual(release.active("runc", self.RELEASES)["tag_name"], "runc-1.5.2-custom.1")
        self.assertIsNone(release.active("runc", self.RELEASES[:3]))

    def test_release_version_maps_tags_to_components(self):
        self.assertEqual(release.release_version(item("runc-1.5.2-custom.3")), ("runc", "1.5.2-custom.3"))
        with self.assertRaises(ValueError):
            release.release_version(item("v1.5.2"))

    def test_assets_must_match_their_published_digest(self):
        data = b"binary"
        good = item("runc-1.5.2-custom.1", assets=[{"name": "runc.amd64", "browser_download_url": "https://x/r",
                                                    "digest": "sha256:" + hashlib.sha256(data).hexdigest()}])
        bad = item("runc-1.5.2-custom.1", assets=[{"name": "runc.amd64", "browser_download_url": "https://x/r",
                                                   "digest": "sha256:" + "0" * 64}])
        with patch.object(release, "download", return_value=data):
            self.assertEqual(release.verified_asset(good, "runc.amd64"), data)
            with self.assertRaises(ValueError):
                release.verified_asset(bad, "runc.amd64")

    def test_sha256sums_parsing(self):
        self.assertEqual(release.parse_sums("a" * 64 + "  runc.amd64\n"), {"runc.amd64": "a" * 64})
        with self.assertRaises(ValueError):
            release.parse_sums("short  runc.amd64\n")


class PublicationTests(unittest.TestCase):
    def make_build(self, root, name="runc", version="1.5.2-custom.1"):
        directory = Path(root) / name
        (directory / "out/release").mkdir(parents=True)
        files = ["runc.amd64", "libseccomp-2.6.1.tar.gz", "libseccomp-2.6.1.tar.gz.asc"] if name == "runc" \
            else [f"containerd-{version}-linux-amd64.tar.gz"]
        for file_name in files:
            (directory / "out/release" / file_name).write_bytes(file_name.encode())
        (directory / "out/go.mod.diff").write_text("")
        for file_name in ["inputs.json", "build.json", "trivy-candidate.json", "trivy-candidate-cargo.json"]:
            (directory / file_name).write_text("{}")
        return directory, {"version": version, "tag": f"{name}-{version}"}

    def test_asset_files_cover_binaries_sources_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            directory, record = self.make_build(root)
            names = set(release.asset_files("runc", record, directory))
            self.assertEqual(names, {"runc.amd64", "libseccomp-2.6.1.tar.gz", "libseccomp-2.6.1.tar.gz.asc",
                                     "inputs.json", "build.json", "go.mod.diff", "trivy.json", "trivy-cargo.json"})
            directory, record = self.make_build(root, "containerd", "2.3.7-custom.1")
            self.assertIn("containerd-2.3.7-custom.1-linux-amd64.tar.gz",
                          release.asset_files("containerd", record, directory))

    def test_missing_evidence_blocks_publication(self):
        with tempfile.TemporaryDirectory() as root:
            directory, record = self.make_build(root)
            (directory / "build.json").unlink()
            with self.assertRaises(ValueError):
                release.asset_files("runc", record, directory)

    def test_existing_tag_is_never_reused(self):
        with patch.object(release, "listing", return_value=[item("runc-1.5.2-custom.1", draft=True)]), \
                patch.object(release, "api_request") as api:
            with self.assertRaises(ValueError):
                release.stage("runc", {"tag": "runc-1.5.2-custom.1"}, Path("/nonexistent"))
            api.assert_not_called()

    def test_uploaded_assets_must_match_staged_files(self):
        with tempfile.TemporaryDirectory() as root:
            staging = Path(root)
            (staging / "runc.amd64").write_bytes(b"runc")
            digest = "sha256:" + hashlib.sha256(b"runc").hexdigest()
            good = {"assets": [{"name": "runc.amd64", "state": "uploaded", "size": 4, "digest": digest}]}
            release.verify_assets(good, staging)
            for broken in [{"assets": []},
                           {"assets": [{"name": "runc.amd64", "state": "starter", "size": 4, "digest": digest}]},
                           {"assets": [{"name": "runc.amd64", "state": "uploaded", "size": 5, "digest": digest}]},
                           {"assets": [{"name": "runc.amd64", "state": "uploaded", "size": 4,
                                        "digest": "sha256:" + "0" * 64}]}]:
                with self.assertRaises(ValueError):
                    release.verify_assets(broken, staging)

    def test_release_notes_name_inputs_and_verification(self):
        inputs = {"upstream": {"tag": "v1.5.2", "repository": "opencontainers/runc", "revision": "a" * 40},
                  "go": {"version": "1.26.11", "declared": "1.26"},
                  "module_overrides": {"golang.org/x/net": "v0.62.0"}}
        record = {"trivy": "aquasec/trivy:0.76.2", "unfixed_findings": [], "repository_revision": "b" * 40}
        notes = release.release_notes("runc", record, inputs)
        self.assertIn("`golang.org/x/net@v0.62.0`", notes)
        self.assertIn("gh attestation verify", notes)
        self.assertIn(json.dumps("1.26.11").strip('"'), notes)


if __name__ == "__main__":
    unittest.main()
