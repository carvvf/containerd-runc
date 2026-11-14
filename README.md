# containerd and runc custom builds

> [!NOTE]
> **Written with AI assistance.** This document was generated with the help of an AI
> assistant and may contain inaccuracies. Verify anything critical against the source code.

This repository rebuilds the official containerd and runc releases for
`linux/amd64` from their verified tagged sources. Each rebuild uses the newest
Go patch of the toolchain upstream builds with and the smallest module upgrades
that remove fixable vulnerabilities, then qualifies the binaries and publishes
them as GitHub Release assets. Upstream code is never modified.

Official releases embed the Go standard library and modules of their build day.
A fix in either reaches them only with the next upstream release. On 2026-11-12
the newest official containerd 2.3.7 and runc 1.5.3 binaries showed 23 fixable
HIGH/CRITICAL Trivy findings (20 and 3), all in Go 1.26.9 or in Go modules.
Rebuilt with Go 1.26.11 and four targeted module upgrades, they showed none.

The binaries are the external container runtime of `ragtime-rag-orch`, which
selects the published releases through their attestation; see
[Consumption](#consumption).

> [!IMPORTANT]
> **Status: prototype.** The publication workflows run on `master`. The first
> releases, `containerd-2.3.7-custom.1` and `runc-1.5.3-custom.1`, were
> published on 2026-11-12, followed the same day by `custom.2` of both from a
> manual dispatch. A `ragtime-rag-orch` three-node lab setup and an identical
> suite update passed with the `custom.2` pair on 2026-11-12 and 2026-11-13.
> Replacing the runtime of an installed suite with a newer pair has not been
> exercised yet.

## Release lines

| Component | Automatic selection | Floor | Go toolchain | Build base |
| --- | --- | --- | --- | --- |
| containerd | Stable `2.3.x` | `2.3.7` | Newest patch of the Go minor in upstream's release Dockerfile | `ubuntu:22.04` by digest |
| runc | Stable `1.5.x` | `1.5.2` | Newest patch of the Go minor in upstream's Dockerfile | `debian:trixie` by digest |
| libseccomp, libpathrs | Versions and checksums declared by the runc release | — | — | built by runc's scripts |
| Trivy | Stable `0.76.x` container | `0.76.1` | — | — |

The containerd floor keeps the ExecSync drain fix first released in 2.3.5,
which is why the suite runs an external runtime. A new release line, floor or
Go minor is a reviewed change, not an automatic one.

## Build policy

1. **Source.** The official tag, verified against the commit it names.
2. **Toolchain.** The newest stable patch of the Go minor that upstream's own
   release build uses, from go.dev's release index, verified by SHA-256. The
   `go` directive in `go.mod` is not used for this: runc 1.5.3 declares
   `go 1.25.0` but is officially built with Go 1.26.
3. **Modules.** Only modules with fixable HIGH/CRITICAL findings are raised,
   each to the smallest fixed version in its major. The first set comes from a
   scan of the official binaries; each candidate scan can add more, up to three
   rounds. Blanket upgrades are rejected: in the 2026-11-12 trial,
   `go get -u=patch` broke containerd's `TestOpenUserFileCapsReads`
   (`moby/sys/user` 0.4.1) and raised unrelated minor versions (`bbolt` 1.5,
   `grpc` 1.84). When an upgrade raises the `go` directive (runc: 1.25 to 1.26
   for `golang.org/x/net` 0.62.0), `godebug default=go<original>` keeps
   upstream's runtime defaults. `go mod vendor` and `go mod verify` follow.
4. **Build method.** As upstream: containerd `make release` (dynamic
   `containerd`, `ctr` and `containerd-stress`, static
   `containerd-shim-runc-v2`); runc `make static` with libseccomp and libpathrs
   built by upstream's `script/build-seccomp.sh` and `script/build-libpathrs.sh`,
   statically linked (`static-pie`), with the same build tags.
5. **Version.** `<version>-custom.<N>`, reported by `containerd --version`, the
   CRI `RuntimeVersion` (`v2.3.7-custom.1`) and `runc --version`
   (`1.5.3-custom.1`). Local checks use `<version>-check`.

## Qualification

A candidate is published only after all of these pass on the exact files:

1. **Trivy.** Go binary analysis of every published executable, blocking on
   fixable HIGH/CRITICAL findings; findings without a released fix are reported
   and do not block. They emit explicit GitHub warning annotations with the CVE,
   package, installed version and target, also when the active release needs no
   rebuild. Their details remain in the job summary and retained evidence. A clean
   rebuild succeeds without a vulnerability warning or notification failure.
   The report must list every expected binary, because Trivy
   skips Go binaries that are not executable. libpathrs's `Cargo.lock` is scanned
   separately: the Rust code linked into runc is invisible to the Go analysis.
   libseccomp (C) is not covered by the scanner.
2. **Upstream unit tests.** containerd `make test` as a non-root user; runc
   `make localunittest` as root in a privileged container, as upstream's
   `make unittest` does, with `-vet=off` because the raised `go` directive makes
   vet reject upstream test code. runc's test helpers link libpathrs
   dynamically, so its library directory is registered with the loader, as
   upstream CI does by installing libpathrs into `/usr`. Checkpoint, systemd and
   cgroup v1 tests skip in this environment.
3. **CRI smoke test.** containerd and runc together in a privileged container:
   versions, image pulls, pod and container lifecycle, non-root `ExecSync` with
   all capabilities dropped, bounded `ExecSync` timeout and I/O drain, cleanup and
   a clean shutdown.

## Releases and assets

Each component is an independent line with immutable releases:

1. `containerd-<version>-custom.<N>`: the `make release` tarball
   `containerd-<version>-custom.<N>-linux-amd64.tar.gz`.
2. `runc-<version>-custom.<N>`: `runc.amd64` and the libseccomp and libpathrs
   source archives with their signatures, as upstream ships them.

Every release also carries `SHA256SUMS`, `inputs.json` (source commit, Go
release and checksum, base digest, module upgrades, scanner version),
`build.json` (reported version, Go toolchain, this repository's commit and the
SHA-256 of every binary), `go.mod.diff`, the Trivy reports and a build
provenance attestation:

```bash
gh attestation verify runc.amd64 --repo carvvf/containerd-runc
```

Publication has two steps. The pipeline creates a draft release with every
asset and checks each uploaded size and digest; the workflow then attests the
staged files and publishes the draft, verifying the result again. A failed run
can leave a draft behind: its tag number is never reused. Releases are not
marked as the repository's latest, because the two lines are independent.

Releases are never modified or deleted, because consumers pin their assets by
URL and hash.

## Local build and checks

Requirements: system Python 3 with its standard library, Bash, Git, Docker with
Buildx and a running Linux daemon, and outbound HTTPS. GitHub API calls use
`GH_TOKEN`, then `GITHUB_TOKEN`, then an existing `gh` login, then anonymous
access. No virtual environment is needed.

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_*.py'
/usr/bin/python3 -m tools.pipeline check --component all
```

`check` resolves the inputs, builds, scans and tests both components, runs the
CRI smoke test with the two candidates together, and never publishes. Binaries
use the version `<version>-check`. Reports, logs and binaries stay in the
ignored `build/<component>/` directories (`inputs.json`, `build.json`,
`trivy-*.json`, `upstream-tests.log`, `out/`). The smoke test needs a privileged
container. Builds use no shared remote cache.

```bash
/usr/bin/python3 -m tools.build_inputs --component runc   # print resolved inputs only
/usr/bin/python3 -m tools.scan                             # rescan the existing build/
```

The VS Code tasks follow the sibling repositories: `Build: build and qualify
local binaries` (default build), `Trivy: scan CVE`, `Test: CRI smoke` and
`Test: all` (default test: offline tests, lints and the smoke test on the
existing build).

## CI lifecycle

1. Pull requests run offline checks and the full `check` qualification without
   publishing.
2. A push to `master`, the daily scheduled scan and manual dispatch run the
   publication pipeline per component, containerd first. It rebuilds when the
   resolved inputs (module upgrades excluded, since each build derives them) or
   this repository's commit differ from the active release, when a rescan of the
   active release finds fixable findings, when no release exists, or on
   `force_rebuild`. A current, clean release completes without rebuilding. The
   smoke test pairs the candidate with the other component's active release, or
   with its official binaries before the first release.
3. The weekly retention job deletes completed workflow runs older than 60 days.
   It never touches releases.

## Consumption

`ragtime-rag-orch` runs these builds as the external runtime of its three-node
suite:

1. **Setup** takes, for each line, the newest published, non-draft,
   non-prerelease release at or above the floor, ordered by upstream patch and
   then by build number. Before any node changes it verifies `SHA256SUMS` with
   `gh attestation verify`, accepting only `.github/workflows/ci.yml` on
   `refs/heads/master` and GitHub-hosted runners as the signer, checks that
   `build.json` and `inputs.json` match their attested checksums and describe
   the release, and records the pair. Nodes download the assets from
   `https://github.com/` without credentials and verify sizes, archive checksums
   and binary hashes before staging.
2. **Suite update** replaces the runtime one server at a time when a newer pair
   is published.

The tag and version scheme, the asset names, `SHA256SUMS`, `build.json`,
`inputs.json` and the signer workflow are therefore a contract with that suite:
changing any of them breaks its release selection (see
[Release and compatibility policy](AGENTS.md#release-and-compatibility-policy)).
See `docs/k3s_containerd_runtime.md` in `ragtime-rag-orch` for its side.

## License

The repository's own files are licensed under the Apache License 2.0. The
rebuilt components keep their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
