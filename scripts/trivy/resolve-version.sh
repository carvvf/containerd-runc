#!/usr/bin/env bash
set -euo pipefail

# Local and CI scans follow the newest patch release of this Trivy minor series
# published as an aquasec/trivy image, so scanner and vulnerability-matching
# fixes arrive without a repository change. Trivy is still 0.x: a new minor
# series can change flags and results, so moving to it is a deliberate edit
# here. Versions are read from the Docker Hub tags of the image the scans pull,
# so a resolved version always has a pullable scanner image.
#
# Prints the resolved version without a "v" prefix (for example 0.76.1).
# --series prints the minor series and --floor the lowest accepted patch
# release instead. TRIVY_VERSION selects an explicit release, which must belong
# to the series, be at or above the floor, and be published on Docker Hub.
TRIVY_SERIES='0.76'
# 0.76.1 is built with Go 1.27.3, which fixes CVE-2026-111943 in the scanner.
TRIVY_FLOOR='0.76.1'
TRIVY_TAGS_URL="https://hub.docker.com/v2/repositories/aquasec/trivy/tags?page_size=100&name=${TRIVY_SERIES}."

trivy_version_error() {
  printf 'Trivy version error: %s\n' "$1" >&2
  exit 1
}

case "${1:-}" in
  --series)
    printf '%s\n' "${TRIVY_SERIES}"
    exit 0
    ;;
  --floor)
    printf '%s\n' "${TRIVY_FLOOR}"
    exit 0
    ;;
esac

for required in curl jq; do
  command -v "${required}" >/dev/null || trivy_version_error "${required} is required."
done

version_below_floor() {
  [ "$1" != "${TRIVY_FLOOR}" ] \
    && [ "$(printf '%s\n%s\n' "$1" "${TRIVY_FLOOR}" | sort -V | head -n 1)" = "$1" ]
}

series_pattern="^${TRIVY_SERIES//./\\.}\\.[0-9]+$"
requested="${TRIVY_VERSION:-}"
requested="${requested#v}"
if [ -n "${requested}" ]; then
  if [[ ! "${requested}" =~ ${series_pattern} ]]; then
    trivy_version_error "A Trivy ${TRIVY_SERIES}.x release is required: ${requested}"
  fi
  if version_below_floor "${requested}"; then
    trivy_version_error "Trivy ${requested} is below the ${TRIVY_FLOOR} floor."
  fi
fi

# Only exact X.Y.Z tags count: release candidates and other suffixed tags never
# match. A registry error stops the caller instead of falling back to an older
# scanner.
tags=()
url="${TRIVY_TAGS_URL}"
while [ -n "${url}" ]; do
  page="$(curl --fail --silent --show-error --location --retry 3 "${url}")"
  mapfile -t -O "${#tags[@]}" tags < <(jq -r '.results[]?.name' <<<"${page}")
  url="$(jq -r '.next // empty' <<<"${page}")"
done

version="$(printf '%s\n' "${tags[@]}" | jq -R -r -s --arg pattern "${series_pattern}" --arg requested "${requested}" '
  [ split("\n")[]
    | select(test($pattern) and ($requested == "" or . == $requested)) ]
  | max_by(split(".") | map(tonumber)) // empty
')"
if [ -z "${version}" ]; then
  trivy_version_error "No aquasec/trivy:${requested:-${TRIVY_SERIES}.x} tag is listed at ${TRIVY_TAGS_URL}."
fi
if version_below_floor "${version}"; then
  trivy_version_error "Newest aquasec/trivy ${TRIVY_SERIES}.x is ${version}, below the ${TRIVY_FLOOR} floor."
fi
printf '%s\n' "${version}"
