"""Offline regression tests for workflow-run retention."""

from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import retention

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def run(identity, status="completed", date="2026-08-01T00:00:00Z"):
    return {"id": identity, "status": status, "created_at": date, "updated_at": date, "name": "ci"}


class PlanTests(unittest.TestCase):
    def test_runs_must_be_old_completed_and_not_current(self):
        runs = [run(1), run(2, status="in_progress"), run(3, date="2026-11-01T00:00:00Z"), run(4),
                {"id": 5, "status": "completed", "created_at": None, "updated_at": None}]
        self.assertEqual([r["id"] for r in retention.plan_runs(runs, NOW, "4")], [1])

    def test_invalid_run_identity_fails(self):
        with self.assertRaises(ValueError):
            retention.plan_runs([run("1")], NOW, "")

    def test_workflow_runs_are_fully_paginated(self):
        pages = {
            retention.RUNS_URL + "?per_page=100": ({"workflow_runs": [run(1)]},
                                                   f'<{retention.RUNS_URL}?per_page=100&page=2>; rel="next"'),
            retention.RUNS_URL + "?per_page=100&page=2": ({"workflow_runs": [run(2)]}, ""),
        }
        self.assertEqual([r["id"] for r in retention.list_runs(lambda url: pages[url])], [1, 2])


class CleanupTests(unittest.TestCase):
    ENVIRONMENT = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": retention.REPOSITORY,
                   "GITHUB_REF": "refs/heads/master", "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "GITHUB_RUN_ID": "99"}

    def test_untrusted_context_is_refused(self):
        with patch.dict(retention.os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                retention.cleanup(dry_run=True)

    def test_dry_run_writes_evidence_without_deleting(self):
        calls = []

        def request(url, method="GET"):
            calls.append((method, url))
            return {"workflow_runs": [run(1)]}, ""

        with tempfile.TemporaryDirectory() as root, patch.object(retention, "ROOT", Path(root)), \
                patch.dict(retention.os.environ, self.ENVIRONMENT, clear=True):
            retention.cleanup(dry_run=True, request=request)
            self.assertTrue((Path(root) / "build/retention-plan.json").is_file())
        self.assertFalse([c for c in calls if c[0] == "DELETE"])

    def test_changed_run_is_skipped(self):
        calls = []

        def request(url, method="GET"):
            calls.append((method, url))
            if url.endswith("/1"):
                return run(1, date="2026-11-11T00:00:00Z"), ""
            return {"workflow_runs": [run(1)]}, ""

        with tempfile.TemporaryDirectory() as root, patch.object(retention, "ROOT", Path(root)), \
                patch.dict(retention.os.environ, self.ENVIRONMENT, clear=True):
            retention.cleanup(dry_run=False, request=request)
        self.assertFalse([c for c in calls if c[0] == "DELETE"])


if __name__ == "__main__":
    unittest.main()
