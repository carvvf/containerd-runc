#!/usr/bin/env bash
# Apply targeted module upgrades to a verified upstream Go source tree.
#
# Usage: apply-module-overrides.sh <evidence-dir> [module@version ...]
#
# Without arguments the tree is only verified. With arguments the listed modules
# are required at least at the given versions (go mod edit -require), and
# go mod tidy applies minimal version selection: a module that another upgrade
# needs at a higher version gets that higher version. When the upgrade raised
# the go directive, a godebug line keeps upstream's runtime defaults (the
# GODEBUG defaults derive from the go directive). The vendor tree is
# regenerated when upstream has one.
# go.mod.orig and go.mod.diff are written to the evidence directory.
set -euo pipefail

evidence="$1"
shift
mkdir -p "${evidence}"
cp go.mod "${evidence}/go.mod.orig"
original="$(awk '$1 == "go" { print $2 }' go.mod)"

if [ "$#" -gt 0 ]; then
  for requirement in "$@"; do
    go mod edit -require="${requirement}"
  done
  GOFLAGS=-mod=mod go mod tidy
  current="$(awk '$1 == "go" { print $2 }' go.mod)"
  if [ "${current}" != "${original}" ]; then
    go mod edit -godebug="default=go$(cut -d. -f1,2 <<<"${original}")"
  fi
  if [ -d vendor ]; then
    GOFLAGS=-mod=mod go mod vendor
  fi
fi
go mod verify
diff -u "${evidence}/go.mod.orig" go.mod > "${evidence}/go.mod.diff" || true
