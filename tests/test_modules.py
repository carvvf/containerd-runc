"""Offline regression tests for targeted module upgrades."""

import unittest

from tools import modules


def report(*rows):
    results = {}
    for target, kind, package, installed, fixed in rows:
        results.setdefault((target, kind), []).append(
            {"VulnerabilityID": f"CVE-{package}-{installed}", "PkgName": package,
             "InstalledVersion": installed, "FixedVersion": fixed, "Severity": "HIGH"})
    return {"SchemaVersion": 2, "Results": [{"Target": t, "Type": k, "Vulnerabilities": v}
                                            for (t, k), v in results.items()]}


class SmallestFixTests(unittest.TestCase):
    def test_smallest_fix_above_installed_in_same_major(self):
        self.assertEqual(modules.smallest_fix("v0.58.0", "0.60.0, 0.62.0"), (0, 60, 0))
        self.assertEqual(modules.smallest_fix("v1.18.5", "1.18.7"), (1, 18, 7))
        self.assertEqual(modules.smallest_fix("v4.1.4", "4.1.6, 5.0.1"), (4, 1, 6))

    def test_pseudo_versions_are_ordered_by_their_release_triple(self):
        self.assertEqual(modules.smallest_fix("v0.0.0-20251202230838-ff82c1b0f217", "0.1.0"), (0, 1, 0))

    def test_no_fix_within_major_fails(self):
        with self.assertRaises(ValueError):
            modules.smallest_fix("v1.9.0", "2.0.0")


class OverrideTests(unittest.TestCase):
    def test_only_fixable_module_findings_create_overrides(self):
        result = modules.overrides_from_report(report(
            ("containerd", "gobinary", "golang.org/x/net", "v0.58.0", "0.60.0, 0.62.0"),
            ("ctr", "gobinary", "golang.org/x/net", "v0.58.0", "0.62.0"),
            ("ctr", "gobinary", "github.com/klauspost/compress", "v1.18.5", "1.18.7"),
            ("ctr", "gobinary", "github.com/example/unfixed", "v1.0.0", ""),
            ("ctr", "gobinary", "stdlib", "v1.26.9", "1.26.10, 1.27.3"),
            ("Cargo.lock", "cargo", "some-crate", "0.1.0", "0.1.1"),
        ), "1.26.11")
        self.assertEqual(result, {"github.com/klauspost/compress": "v1.18.7", "golang.org/x/net": "v0.62.0"})

    def test_existing_overrides_are_kept_and_raised(self):
        result = modules.overrides_from_report(
            report(("runc", "gobinary", "golang.org/x/net", "v0.60.0", "0.62.0")), "1.26.11",
            {"golang.org/x/net": "v0.60.0", "golang.org/x/sys": "v0.49.0"})
        self.assertEqual(result, {"golang.org/x/net": "v0.62.0", "golang.org/x/sys": "v0.49.0"})

    def test_stdlib_fix_outside_the_selected_toolchain_fails(self):
        with self.assertRaises(ValueError) as context:
            modules.overrides_from_report(report(("runc", "gobinary", "stdlib", "v1.26.11", "1.27.5")), "1.26.11")
        self.assertIn("another Go minor", str(context.exception))

    def test_go_get_arguments_are_validated(self):
        self.assertEqual(modules.go_get_arguments({"golang.org/x/net": "v0.62.0"}), ["golang.org/x/net@v0.62.0"])
        for bad in [{"golang.org/x/net; rm -rf /": "v0.62.0"}, {"golang.org/x/net": "latest"}]:
            with self.assertRaises(ValueError):
                modules.go_get_arguments(bad)


if __name__ == "__main__":
    unittest.main()
