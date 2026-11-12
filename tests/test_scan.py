"""Offline regression tests for scan report validation and the publication gate."""

import unittest

from tools import scan


def result(target, kind, vulnerabilities=()):
    return {"Target": target, "Type": kind, "Vulnerabilities": list(vulnerabilities)}


def finding(package, fixed):
    return {"VulnerabilityID": "CVE-1", "PkgName": package, "InstalledVersion": "v1.0.0",
            "FixedVersion": fixed, "Severity": "CRITICAL"}


class BinaryCoverageTests(unittest.TestCase):
    def test_every_expected_binary_must_be_analyzed(self):
        report = {"Results": [result("containerd", "gobinary"), result("ctr", "gobinary")]}
        scan.require_binaries(report, ["ctr", "containerd"])
        with self.assertRaises(ValueError):
            scan.require_binaries(report, ["containerd", "containerd-shim-runc-v2", "ctr"])

    def test_extra_binaries_also_fail(self):
        report = {"Results": [result("runc", "gobinary"), result("unexpected", "gobinary")]}
        with self.assertRaises(ValueError):
            scan.require_binaries(report, ["runc"])

    def test_cargo_lock_must_be_analyzed(self):
        scan.require_cargo({"Results": [result("Cargo.lock", "cargo")]})
        with self.assertRaises(ValueError):
            scan.require_cargo({"Results": []})


class GateTests(unittest.TestCase):
    def test_fixable_findings_block(self):
        report = {"Results": [result("runc", "gobinary", [finding("golang.org/x/net", "0.62.0")])]}
        with self.assertRaises(ValueError):
            scan.gate(report)

    def test_unfixed_findings_are_reported_without_blocking(self):
        report = {"Results": [result("runc", "gobinary", [finding("github.com/example/a", "")])]}
        cargo = {"Results": [result("Cargo.lock", "cargo")]}
        unfixed = scan.gate(report, cargo)
        self.assertEqual(len(unfixed), 1)
        self.assertIn("no fix released", scan.describe(unfixed)[0])


if __name__ == "__main__":
    unittest.main()
