# syntax=docker/dockerfile:1
# check=skip=InvalidDefaultArgInFrom
# BASE_IMAGE has no default by design: tools/build_inputs.py always passes a digest.
# Rebuild an official containerd release exactly as upstream's release workflow
# does (.github/workflows/release/Dockerfile: Ubuntu 22.04, `make release`),
# with the newest Go patch of upstream's Go minor and targeted module upgrades.
# Every argument is resolved and verified by tools/build_inputs.py; there are no
# defaults, so a direct build without resolved inputs fails.
ARG BASE_IMAGE

FROM ${BASE_IMAGE} AS source
ARG GO_VERSION
ARG GO_SHA256
ARG UPSTREAM_TAG
ARG UPSTREAM_REVISION
ARG MODULE_OVERRIDES=""
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
RUN for name in GO_VERSION GO_SHA256 UPSTREAM_TAG UPSTREAM_REVISION; do \
      if [ -z "${!name}" ]; then echo "${name} is a required build argument." >&2; exit 1; fi; \
    done
# hadolint ignore=DL3008
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl dpkg-dev gcc git libc6-dev make pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz \
    && echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz
ENV PATH=/usr/local/go/bin:/root/go/bin:${PATH} \
    GOTOOLCHAIN=local
RUN go telemetry off \
    && git clone --depth 1 --branch "${UPSTREAM_TAG}" https://github.com/containerd/containerd.git /src \
    && test "$(git -C /src rev-parse HEAD)" = "${UPSTREAM_REVISION}"
WORKDIR /src
COPY --chmod=0755 apply-module-overrides.sh /usr/local/bin/apply-module-overrides.sh
# hadolint ignore=SC2086
RUN apply-module-overrides.sh /out ${MODULE_OVERRIDES}

FROM source AS build
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]
ARG UPSTREAM_VERSION
ARG UPSTREAM_REVISION
ARG BUILD_SUFFIX
RUN test -n "${UPSTREAM_VERSION}" && test -n "${BUILD_SUFFIX}" \
    && make release VERSION="v${UPSTREAM_VERSION}-${BUILD_SUFFIX}" REVISION="${UPSTREAM_REVISION}" \
    && mkdir -p /out/release /out/bin \
    && cp releases/containerd-*-linux-amd64.tar.gz /out/release/ \
    && cp bin/containerd bin/containerd-shim-runc-v2 bin/containerd-stress bin/ctr /out/bin/ \
    && /out/bin/containerd --version | grep -F -- "v${UPSTREAM_VERSION}-${BUILD_SUFFIX} ${UPSTREAM_REVISION}" \
    && go version > /out/go-version.txt

FROM scratch AS artifacts
COPY --from=build /out /
