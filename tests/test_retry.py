"""Exercise docker/retry.sh with a command that fails a given number of times.

No network or Docker is needed; pauses are set to zero.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

RETRY = Path(__file__).resolve().parents[1] / "docker" / "retry.sh"

# Fails until it has run FAIL_TIMES times, counting its runs in COUNT_FILE.
FLAKY = """#!/bin/sh
count=$(cat "${COUNT_FILE}" 2>/dev/null || echo 0)
count=$((count + 1))
echo "${count}" > "${COUNT_FILE}"
echo "args: $*"
[ "${count}" -gt "${FAIL_TIMES}" ] || exit 100
"""


class RetryTest(unittest.TestCase):
    def run_retry(self, fail_times, *args, **env):
        with tempfile.TemporaryDirectory() as directory:
            flaky = Path(directory) / "flaky"
            flaky.write_text(FLAKY)
            flaky.chmod(0o755)
            count = Path(directory) / "count"
            environment = dict(os.environ, COUNT_FILE=str(count), FAIL_TIMES=str(fail_times), RETRY_DELAY="0")
            environment.update(env)
            result = subprocess.run(
                ["sh", str(RETRY), str(flaky), *args],
                env=environment, capture_output=True, text=True, check=False,
            )
            runs = int(count.read_text()) if count.exists() else 0
            return result, runs

    def test_success_needs_one_run(self):
        result, runs = self.run_retry(0, "update")
        self.assertEqual((result.returncode, runs), (0, 1))
        self.assertEqual(result.stderr, "")

    def test_transient_failures_are_retried(self):
        result, runs = self.run_retry(2, "update", "-qq")
        self.assertEqual((result.returncode, runs), (0, 3))
        self.assertIn("args: update -qq", result.stdout)
        self.assertIn("attempt 2 of 5", result.stderr)

    def test_lasting_failure_returns_the_command_status(self):
        result, runs = self.run_retry(99, "update")
        self.assertEqual((result.returncode, runs), (100, 5))
        self.assertIn("failed 5 times (exit 100); giving up", result.stderr)

    def test_attempts_are_configurable(self):
        result, runs = self.run_retry(99, RETRY_ATTEMPTS="2")
        self.assertEqual((result.returncode, runs), (100, 2))

    def test_missing_command_is_a_usage_error(self):
        result = subprocess.run(["sh", str(RETRY)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_invalid_settings_are_rejected(self):
        for env in ({"RETRY_ATTEMPTS": "x"}, {"RETRY_DELAY": "-1"}, {"RETRY_ATTEMPTS": "2.5"}):
            with self.subTest(env=env):
                result, runs = self.run_retry(0, **env)
                self.assertEqual((result.returncode, runs), (2, 0))


if __name__ == "__main__":
    unittest.main()
