# Third-party notices

The release assets of this repository contain software built from the
following upstream projects. Each keeps its own license; the Apache License 2.0
of this repository covers only its own files (tooling, workflows, tests and
documentation).

| Component | Upstream | License | Where it ships |
| --- | --- | --- | --- |
| containerd | https://github.com/containerd/containerd | Apache-2.0 | containerd tarball |
| runc | https://github.com/opencontainers/runc | Apache-2.0 | `runc.amd64` |
| libseccomp | https://github.com/seccomp/libseccomp | LGPL-2.1 | statically linked into `runc.amd64` |
| libpathrs | https://github.com/cyphar/libpathrs | MPL-2.0 OR LGPL-3.0-or-later (per file; see its `COPYING.md`) | statically linked into `runc.amd64` |
| Go standard library and modules | https://go.dev and each module's repository | Per module (listed in `go.mod` and `vendor/modules.txt` of the release's source) | compiled into every binary |
| Rust crates used by libpathrs | https://crates.io | Per crate (listed in libpathrs's `Cargo.lock`) | statically linked into `runc.amd64` |

## Statically linked libraries

runc links libseccomp and libpathrs statically. As upstream runc does, every
runc release of this repository ships the exact source archives of both
libraries, with their upstream signatures, as release assets next to the
binary. The versions and checksums are those declared by the rebuilt runc
release's `script/release_build.sh`, `script/build-seccomp.sh` and
`script/build-libpathrs.sh`.

## Source

Each release's `inputs.json` names the upstream tag and commit, and its
`go.mod.diff` lists the only change applied to the upstream source tree: the
targeted module upgrades and, when needed, a `godebug` line that keeps
upstream's runtime defaults. No upstream code is modified.
