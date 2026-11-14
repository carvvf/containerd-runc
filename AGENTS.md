# AGENTS.md

## Purpose and scope

This repository rebuilds the official containerd and runc releases for
`linux/amd64` from their verified tagged sources, using the newest Go patch of
the toolchain upstream builds with and the smallest module upgrades that remove
fixable vulnerabilities. It is a build-and-qualification repository, not a fork
of containerd or runc: upstream code is never modified. The binaries are
published as GitHub Release assets of `carvvf/containerd-runc` and are intended
for the external container runtime of `ragtime-rag-orch`.

## Language and communication

1. Chat with the user in Italian. Repository content must be English, including
   code, comments, documentation, diagnostics, test names, and commit messages.
2. Use numbered lists for individually reviewable findings and decisions.
3. Classify proposed fixes as exactly one of REAL SOLUTION, PARTIAL SOLUTION,
   WORKAROUND, or UNCONFIRMED / HYPOTHESIS. Explain root causes and evidence.
4. Architectural workarounds require an ADR-lite, trade-offs, and explicit user
   confirmation before implementation. Never mask errors or weaken security
   checks to make a candidate pass.

## Release and compatibility policy

1. Automatic selection is limited to stable containerd `2.3.x` (floor `2.3.7`,
   which contains the ExecSync drain fix first released in `2.3.5`) and stable
   runc `1.5.x` (floor `1.5.2`). Each component is an independent release line.
2. The Go toolchain is the newest stable patch of the Go minor that upstream's
   own release build declares (containerd
   `.github/workflows/release/Dockerfile`, runc `Dockerfile`), never older than
   upstream's pinned patch. Moving to another Go minor is a deliberate change.
3. Module upgrades are targeted: only modules with fixable HIGH/CRITICAL
   findings are raised, each to the smallest fixed version within its major.
   Blanket upgrades (`go get -u`, `-u=patch`) are not allowed: they changed
   behavior in upstream tests and raised unrelated minor versions. When an
   upgrade raises the `go` directive, add `godebug default=go<original>` so the
   binaries keep upstream's runtime defaults.
4. Build exactly as upstream does: containerd `make release`, runc
   `make static` with libseccomp and libpathrs built by upstream's scripts.
   Same build tags, flags, and linking. Ubuntu 22.04 for containerd and Debian
   trixie for runc, as upstream; both by digest.
5. A change to a release line, floor, Go minor, module policy, build method,
   input schema, asset layout, or externally visible behavior requires an
   explanation of compatibility, tests, consumers, and rollout, then explicit
   user confirmation before implementation. No opportunistic bumps.
6. Publish only `containerd-<version>-custom.<N>` and `runc-<version>-custom.<N>`
   releases. The binaries report the same `<version>-custom.<N>`. Never overwrite,
   modify, or delete a published release: consumers pin assets by URL and hash.
7. Record `schema_version` and `producer_version` in generated input manifests.
   Fail explicitly on unsupported schemas. The initial input schema is 1 and the
   tooling producer version is `0.1.0`; these are not upstream versions.

## Security and supply chain

1. Verify every input: source tags against the commits they name, the Go archive
   against go.dev's SHA-256, libseccomp and libpathrs against the checksums in
   upstream's scripts, base images by digest. Never silently fall back after a
   resolution or verification failure.
2. Resolve inputs once per operation and record them. Do not substitute inputs
   between qualification and publication: publish the exact qualified files.
3. Block publication on every fixable Trivy HIGH/CRITICAL finding. Report
   findings without a released fix separately; they do not block. Emit explicit
   GitHub warnings and include their details in the summary, including when an
   active scan requires no rebuild. A clean rebuild emits no vulnerability warning
   and has no intentional notification failure. Fail when the
   report does not contain every expected binary: Trivy ignores Go binaries that
   are not executable. Scan libpathrs's `Cargo.lock` separately, because the Rust
   code linked into runc is invisible to the Go binary analysis. State that
   libseccomp (C) is not covered by the scanner.
4. Upstream unit tests must pass for every candidate. Running them with
   `-vet=off` for runc is an approved WORKAROUND: the raised `go` directive makes
   vet reject upstream test code, not product code. Any other weakening of a
   test or a gate requires explicit user confirmation.
5. Serialize publication and retention cleanup. Retention deletes only completed
   workflow runs older than 60 days; it never deletes releases or tags. Support
   dry runs and never upload credentials as artifacts.
6. Ship the libseccomp and libpathrs source archives with runc, as upstream does,
   and keep `THIRD_PARTY_NOTICES.md` current. The repository license does not
   relicense the rebuilt components.

## Verification and environment

1. Reuse shared resolver and pipeline functions for local and CI behavior.
   Keep core selection logic, integrations, and test utilities separate.
2. Do not automatically run test suites, builds, scans, or smoke tests
   immediately after edits. First report changes, reason, exact recommended
   commands, interpreter/environment, and dependency-update requirements.
3. Run tests only after an explicit user request or readiness confirmation.
   Offline tests use system Python's standard library. Builds, upstream tests,
   and the CRI smoke test run in disposable Docker resources; the smoke test needs
   a privileged container. Nothing is installed in a developer environment.
4. Never bypass installed environments with PYTHONPATH changes or import-path
   manipulation. If an environment needs updating, state the minimal update
   steps and wait for the user before testing.
5. Scan added text for English-only compliance. Use mode `775` for directories
   and executable files with a shebang; use `664` for other files. Never grant
   write permission to others. Keep generated reports out of version control.

## Git and delivery

1. Before Git history or remote operations, inspect `git status` and
   `git branch --show-current`. Work on a dedicated feature branch, never
   directly on `master`, `main`, or another integration head.
2. The user performs commits. Provide English one-line commit messages. Do not
   commit, push, rewrite history, merge PRs, or change remote state without exact
   explicit authorization naming the operation, branch, remote, and refs.
3. Use feature branch, user commits, feature-branch push, PR, and user-controlled
   merge. Force pushes require separate explicit authorization and recovery plan.
4. Do not change `ragtime-rag-orch`, persisted installation profiles, or live
   clusters as part of this work. The suite selects the published releases by
   attestation and reads their tags, asset names, `SHA256SUMS`, `build.json` and
   `inputs.json`; a change to any of them needs a coordinated change there.
5. Report what changed, why, verification actually performed, remaining limits,
   and the operator actions needed to publish and consume the binaries. Do not
   claim publication or cluster compatibility without the corresponding evidence.
