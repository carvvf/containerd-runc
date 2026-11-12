# syntax=docker/dockerfile:1
# check=skip=InvalidDefaultArgInFrom
# BASE_IMAGE has no default by design: tools/build_inputs.py always passes a digest.
# Rebuild an official runc release as upstream's release build does
# (Dockerfile + script/release_build.sh: Debian trixie, libseccomp and libpathrs
# built by upstream's scripts and linked statically, `make static`), with the
# newest Go patch of upstream's Go minor and targeted module upgrades. Only the
# amd64 target is built. Every argument is resolved and verified by
# tools/build_inputs.py; there are no defaults.
ARG BASE_IMAGE

FROM ${BASE_IMAGE} AS source
ARG GO_VERSION
ARG GO_SHA256
ARG UPSTREAM_TAG
ARG UPSTREAM_REVISION
ARG LIBSECCOMP_VERSION
ARG LIBPATHRS_VERSION
ARG MODULE_OVERRIDES=""
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN for name in GO_VERSION GO_SHA256 UPSTREAM_TAG UPSTREAM_REVISION LIBSECCOMP_VERSION LIBPATHRS_VERSION; do \
      if [ -z "${!name}" ]; then echo "${name} is a required build argument." >&2; exit 1; fi; \
    done
# The build packages of upstream's Dockerfile, without its cross-compilers and
# integration-test tools.
# hadolint ignore=DL3008
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential ca-certificates cargo cargo-auditable clang curl gawk git gperf \
      lld pkg-config xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz \
    && echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz
ENV PATH=/usr/local/go/bin:/root/go/bin:${PATH} \
    GOTOOLCHAIN=local \
    DYLIB_DIR=/opt/runc-dylibs \
    PKG_CONFIG_PATH=/opt/runc-dylibs/amd64/lib/pkgconfig
ENV SRC=/go/src/github.com/opencontainers/runc
RUN go telemetry off \
    && git clone --depth 1 --branch "${UPSTREAM_TAG}" https://github.com/opencontainers/runc.git "${SRC}" \
    && test "$(git -C "${SRC}" rev-parse HEAD)" = "${UPSTREAM_REVISION}" \
    && git config --global --add safe.directory "${SRC}"
WORKDIR ${SRC}
# Upstream's scripts download and verify both libraries against their own
# checksum tables, then keep the source archives and signatures in
# ${DYLIB_DIR}/src for release.
RUN mkdir -p "${DYLIB_DIR}" \
    && script/build-seccomp.sh "${LIBSECCOMP_VERSION}" "${DYLIB_DIR}" amd64 \
    && script/build-libpathrs.sh "${LIBPATHRS_VERSION}" "${DYLIB_DIR}" amd64
COPY --chmod=0755 apply-module-overrides.sh /usr/local/bin/apply-module-overrides.sh
# hadolint ignore=SC2086
RUN apply-module-overrides.sh /out ${MODULE_OVERRIDES}

FROM source AS build
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ARG UPSTREAM_VERSION
ARG LIBSECCOMP_VERSION
ARG LIBPATHRS_VERSION
ARG BUILD_SUFFIX
# COMMIT omits --dirty: the only change to the tree is recorded in go.mod.diff.
RUN test -n "${UPSTREAM_VERSION}" && test -n "${BUILD_SUFFIX}" \
    && make COMMIT="$(git describe --long --always)" EXTRA_FLAGS="-a" \
      EXTRA_LDFLAGS="-w -s -buildid=" EXTRA_VERSION="-${BUILD_SUFFIX}" static \
    && strip runc \
    && ./runc --version | tee /tmp/version \
    && grep -qx "runc version ${UPSTREAM_VERSION}-${BUILD_SUFFIX}" <(head -n 1 /tmp/version) \
    && grep -qx "libseccomp: ${LIBSECCOMP_VERSION}" /tmp/version \
    && grep -qx "libpathrs: ${LIBPATHRS_VERSION}" /tmp/version \
    && mkdir -p /out/release /out/bin /out/libpathrs \
    && cp runc /out/bin/runc \
    && cp runc /out/release/runc.amd64 \
    && cp "${DYLIB_DIR}"/src/* /out/release/ \
    && tar -xJf "/out/release/libpathrs-${LIBPATHRS_VERSION}.tar.xz" --strip-components=1 \
      -C /out/libpathrs "libpathrs-${LIBPATHRS_VERSION}/Cargo.lock" \
    && go version > /out/go-version.txt

FROM scratch AS artifacts
COPY --from=build /out /
