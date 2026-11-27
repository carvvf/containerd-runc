#!/bin/sh
# Runs a command and retries it with growing pauses while it fails.
#
# Package index downloads fail while a distribution mirror is mid-sync: apt reports
# "Hash Sum mismatch" or "File has unexpected size (...) Mirror sync in progress?" and
# exits non-zero, and the same download succeeds a few minutes later. apt's own
# Acquire::Retries retries a failed file at once, with no pause, and its documentation
# does not say it covers those mismatches, so the builds and the package-upgrade check
# retry the whole index update here. A failure that outlasts every attempt still fails,
# with the command's own exit status.
#
# usage: retry.sh COMMAND [ARGUMENT...]
# RETRY_ATTEMPTS (default 5) sets the number of attempts and RETRY_DELAY (default 30) the
# pause in seconds after the first failure; the pause after failure N is N times it.
set -u

if [ "$#" -eq 0 ]; then
  echo "usage: retry.sh COMMAND [ARGUMENT...]" >&2
  exit 2
fi
attempts="${RETRY_ATTEMPTS:-5}"
delay="${RETRY_DELAY:-30}"
case "${attempts}:${delay}" in
  *[!0-9:]* | :* | *:) echo "retry.sh: RETRY_ATTEMPTS and RETRY_DELAY must be whole numbers." >&2; exit 2 ;;
esac

attempt=1
while :; do
  "$@" && exit 0
  status=$?
  if [ "${attempt}" -ge "${attempts}" ]; then
    echo "retry.sh: $1 failed ${attempt} times (exit ${status}); giving up." >&2
    exit "${status}"
  fi
  pause=$((delay * attempt))
  echo "retry.sh: $1 failed (exit ${status}, attempt ${attempt} of ${attempts}); retrying in ${pause} seconds." >&2
  sleep "${pause}"
  attempt=$((attempt + 1))
done
