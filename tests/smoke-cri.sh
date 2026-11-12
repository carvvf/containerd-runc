#!/usr/bin/env bash
# CRI smoke test: run a containerd and runc pair in a privileged, disposable
# container and drive it through the CRI with crictl.
#
# Usage: tests/smoke-cri.sh <containerd-bin-dir> <runc-binary> \
#          <containerd-version> <runc-version> <log-file>
#
# <containerd-bin-dir> holds containerd, containerd-shim-runc-v2 and ctr.
# The versions are the expected `<version>[-custom.N|-check]` strings. The
# configuration derives from ragtime-rag-orch's external runtime profile; see
# tests/smoke/config.toml for the differences. Exit status is non-zero when any
# check fails.
set -euo pipefail

CRICTL_VERSION='v1.36.0'
CRICTL_SHA256='83855e114566a8a8c44c548d515670f51de3a5e1da8b2effb59870e2f10c25a3'
PAUSE_IMAGE='rancher/mirrored-pause:3.10.2'
PAUSE_DIGEST='sha256:f548e0e8e3dc1896ca956272154dde3314e8cc4fde0a57577ee9fa1c63f5baf4'
TEST_SYSTEM_IMAGE='ubuntu:26.04'
# The ExecSync drain check allows containerd's fixed 10 s I/O wait plus the
# configured drain_exec_sync_io_timeout (5 s) and a margin.
DRAIN_LIMIT_SECONDS=20

# Checks used by inside(); they read its crictl array and ctr variable.
containerd_version_matches() { /smoke/bin/containerd --version | grep -qF " v${EXPECTED_CONTAINERD} "; }
runc_version_matches() { [ "$(/smoke/bin/runc --version | head -n 1)" = "runc version ${EXPECTED_RUNC}" ]; }
cri_version_matches() { "${crictl[@]}" version 2>/dev/null | grep -qx "RuntimeVersion:  v${EXPECTED_CONTAINERD}"; }
runtime_ready() {
  "${crictl[@]}" info 2>/dev/null \
    | jq -e '.status.conditions[] | select(.type == "RuntimeReady") | .status == true' >/dev/null
}
sandbox_digest_pinned() {
  "${crictl[@]}" inspecti -o json "${PAUSE_IMAGE}" 2>/dev/null \
    | jq -e --arg digest "${PAUSE_DIGEST}" "[.status.repoDigests[] | endswith(\$digest)] | any" >/dev/null
}
container_running() {
  [ "$("${crictl[@]}" inspect -o go-template --template '{{.status.state}}' "${ctr}" 2>/dev/null)" = CONTAINER_RUNNING ]
}
container_log_captured() { "${crictl[@]}" logs "${ctr}" 2>&1 | grep -qx smoke-started; }
exec_is_unprivileged() {
  [ "$("${crictl[@]}" exec -s "${ctr}" id -u 2>/dev/null)" = 1000 ] \
    && "${crictl[@]}" exec -s "${ctr}" grep -q '^CapEff:.0000000000000000$' /proc/self/status
}
runc_lists_container() { /smoke/bin/runc --root /run/containerd/runc/k8s.io list -q | grep -qx "${ctr}"; }
no_runc_container_left() { [ -z "$(/smoke/bin/runc --root /run/containerd/runc/k8s.io list -q)" ]; }

inside() {
  export CONTAINER_RUNTIME_ENDPOINT=unix:///run/containerd-smoke/containerd.sock
  export IMAGE_SERVICE_ENDPOINT="${CONTAINER_RUNTIME_ENDPOINT}"
  local crictl=(/smoke/crictl --timeout 60s) fails=0 pod="" ctr="" start elapsed rc
  pass() { echo "PASS  $*"; }
  fail() { echo "FAIL  $*"; fails=$((fails + 1)); }
  check() {
    local description="$1" output
    shift
    if output="$("$@" 2>&1)"; then pass "${description}"; else fail "${description}: $(tail -n 2 <<<"${output}")"; fi
  }

  # cgroup v2: move this container's processes out of its root so controllers can be delegated.
  mkdir -p /sys/fs/cgroup/init
  xargs -rn1 </sys/fs/cgroup/cgroup.procs >/sys/fs/cgroup/init/cgroup.procs 2>/dev/null || true
  sed -e 's/ / +/g' -e 's/^/+/' </sys/fs/cgroup/cgroup.controllers >/sys/fs/cgroup/cgroup.subtree_control
  mkdir -p /tmp/smoke-logs

  /smoke/bin/containerd --config /smoke/config.toml >/tmp/containerd.log 2>&1 &
  local daemon=$!
  for _ in $(seq 1 120); do [ -S /run/containerd-smoke/containerd.sock ] && break; sleep 0.5; done

  check "containerd --version reports ${EXPECTED_CONTAINERD}" containerd_version_matches
  check "runc --version reports ${EXPECTED_RUNC}" runc_version_matches
  check "CRI RuntimeVersion is v${EXPECTED_CONTAINERD}" cri_version_matches
  check "CRI RuntimeReady" runtime_ready
  check "pull pinned sandbox image" "${crictl[@]}" pull "${PAUSE_IMAGE}"
  check "sandbox image digest is pinned" sandbox_digest_pinned
  check "pull test image by digest" "${crictl[@]}" pull "$(jq -r .image.image /smoke/ctr.json)"

  if pod="$("${crictl[@]}" runp /smoke/pod.json 2>/tmp/error)"; then pass "RunPodSandbox"; else fail "RunPodSandbox: $(tail -n 2 /tmp/error)"; fi
  if ctr="$("${crictl[@]}" create "${pod}" /smoke/ctr.json /smoke/pod.json 2>/tmp/error)"; then pass "CreateContainer"; else fail "CreateContainer: $(tail -n 2 /tmp/error)"; fi
  check "StartContainer" "${crictl[@]}" start "${ctr}"
  sleep 2
  check "container is running" container_running
  check "container log is captured" container_log_captured
  check "ExecSync runs as uid 1000 with no effective capabilities" exec_is_unprivileged
  check "the configured runc manages the container" runc_lists_container

  start=$(date +%s)
  rc=0
  "${crictl[@]}" exec -s --timeout 2 "${ctr}" sleep 30 >/dev/null 2>&1 || rc=$?
  elapsed=$(($(date +%s) - start))
  if [ "${rc}" -ne 0 ] && [ "${elapsed}" -le 6 ]; then pass "ExecSync timeout is bounded (${elapsed}s)"; else fail "ExecSync timeout not bounded (rc=${rc}, ${elapsed}s)"; fi

  start=$(date +%s)
  rc=0
  "${crictl[@]}" exec -s --timeout 30 "${ctr}" sh -c 'sleep 60 & echo detached' >/dev/null 2>&1 || rc=$?
  elapsed=$(($(date +%s) - start))
  if [ "${elapsed}" -le "${DRAIN_LIMIT_SECONDS}" ]; then
    pass "ExecSync I/O drain is bounded with a child holding stdout (${elapsed}s)"
  else
    fail "ExecSync I/O drain not bounded (rc=${rc}, ${elapsed}s > ${DRAIN_LIMIT_SECONDS}s)"
  fi

  check "StopContainer" "${crictl[@]}" stop --timeout 5 "${ctr}"
  check "RemoveContainer" "${crictl[@]}" rm "${ctr}"
  check "StopPodSandbox" "${crictl[@]}" stopp "${pod}"
  check "RemovePodSandbox" "${crictl[@]}" rmp "${pod}"
  check "no runc container is left" no_runc_container_left

  kill -TERM "${daemon}"
  rc=0
  wait "${daemon}" || rc=$?
  if [ "${rc}" -eq 0 ]; then pass "containerd exits cleanly on SIGTERM"; else fail "containerd exit status ${rc} on SIGTERM"; fi
  # Expected: the two bounded ExecSync checks, and the shim's runtime-info probe,
  # which looks runc up in PATH instead of BinaryName, as under the profile's
  # systemd unit.
  echo "containerd error lines (expected: runtime-info probe and the two bounded ExecSync checks):"
  grep 'level=error' /tmp/containerd.log | cut -c1-240 || true
  echo "RESULT failures=${fails}"
  [ "${fails}" -eq 0 ]
}

if [ "${1:-}" = "--inside" ]; then
  inside
  exit
fi

if [ "$#" -ne 5 ]; then
  echo "usage: $0 <containerd-bin-dir> <runc-binary> <containerd-version> <runc-version> <log-file>" >&2
  exit 2
fi
bin_dir="$(realpath "$1")"
runc_binary="$(realpath "$2")"
log_file="$5"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cache="${root}/.cache/crictl"
archive="${cache}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz"
mkdir -p "${cache}" "$(dirname "${log_file}")"
if ! echo "${CRICTL_SHA256}  ${archive}" | sha256sum -c --status - 2>/dev/null; then
  curl --fail --silent --show-error --location --retry 3 -o "${archive}" \
    "https://github.com/kubernetes-sigs/cri-tools/releases/download/${CRICTL_VERSION}/crictl-${CRICTL_VERSION}-linux-amd64.tar.gz"
  echo "${CRICTL_SHA256}  ${archive}" | sha256sum -c -
fi
tar -xzf "${archive}" -C "${cache}" crictl

stage="$(mktemp -d)"
trap 'rm -rf "${stage}"' EXIT
for name in containerd containerd-shim-runc-v2 ctr; do
  install -m 0755 "${bin_dir}/${name}" "${stage}/${name}"
done
install -m 0755 "${runc_binary}" "${stage}/runc"

docker run --rm --privileged --cgroupns=private \
  --tmpfs /var/lib/containerd-smoke:exec --tmpfs /run:exec \
  -e EXPECTED_CONTAINERD="$3" -e EXPECTED_RUNC="$4" \
  -e PAUSE_IMAGE="${PAUSE_IMAGE}" -e PAUSE_DIGEST="${PAUSE_DIGEST}" \
  -v "${stage}:/smoke/bin:ro" \
  -v "${root}/tests/smoke/config.toml:/smoke/config.toml:ro" \
  -v "${root}/tests/smoke/pod.json:/smoke/pod.json:ro" \
  -v "${root}/tests/smoke/ctr.json:/smoke/ctr.json:ro" \
  -v "${cache}/crictl:/smoke/crictl:ro" \
  -v "${root}/tests/smoke-cri.sh:/smoke/smoke-cri.sh:ro" \
  "${TEST_SYSTEM_IMAGE}" bash -c \
  'apt-get -qq update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y --no-install-recommends ca-certificates jq >/dev/null && exec bash /smoke/smoke-cri.sh --inside' \
  2>&1 | tee "${log_file}"
