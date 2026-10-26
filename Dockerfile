ARG MMSEQS_HASH=archive/8cc5ce367b5638c4306c2d7cfc652dd099a4643f
ARG HHSUITE_HASH=archive/4c0cd66434ce0b83ccd247053f57989fdd53d82b
ARG downloader=${TARGETARCH}_downloader

FROM scratch AS amd64_downloader
ARG MMSEQS_HASH
ARG HHSUITE_HASH
WORKDIR /opt/build
ONBUILD ADD https://mmseqs.com/${MMSEQS_HASH}/mmseqs-linux-gpu.tar.gz  .
ONBUILD ADD https://mmseqs.com/${HHSUITE_HASH}/hhsuite-linux-avx2.tar.gz  .

FROM scratch AS arm64_downloader
ARG MMSEQS_HASH
ARG HHSUITE_HASH
WORKDIR /opt/build
ONBUILD ADD https://mmseqs.com/${MMSEQS_HASH}/mmseqs-linux-gpu-arm64.tar.gz  .
ONBUILD ADD https://mmseqs.com/${HHSUITE_HASH}/hhsuite-linux-arm64.tar.gz .

FROM $downloader AS downloader

FROM debian:trixie-slim AS builder
WORKDIR /opt/build
COPY --from=downloader /opt/build/* .
RUN mkdir binaries; \
    for i in *.tar.gz; do \
        if [ -e ${i} ]; then \
            tar -xzvf ${i}; \
            mv -f -- */bin/* binaries/; \
        fi; \
    done; \
    chmod -R +x binaries;

FROM debian:trixie-slim AS base
ARG TARGETPLATFORM

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
RUN apt-get update > /dev/null && \
    apt-get install --no-install-recommends --yes \
        wget bzip2 ca-certificates curl unzip \
        git \
        tini \
        python3-venv \
        > /dev/null && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    python3 -m venv $VIRTUAL_ENV

FROM base AS wheel_builder
WORKDIR /wheel_builder

RUN apt-get update > /dev/null && \
    apt-get install --no-install-recommends --yes \
        build-essential \
        python3-dev \
        > /dev/null && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /wheels && \
    python -m pip wheel --no-cache-dir --wheel-dir=/wheels --no-deps prody

FROM base
WORKDIR /api
COPY . .
COPY --from=wheel_builder /wheels /wheels
ARG EVEDESIGN_REPO=https://github.com/evedesignbio/evedesign.git
ARG EVEDESIGN_REF=develop
ARG EVEDESIGN_EXTRAS=evmutation2,esm2,mpnn,umap
RUN pip install --no-cache-dir --find-links=/wheels \
        "evedesign[${EVEDESIGN_EXTRAS}] @ git+${EVEDESIGN_REPO}@${EVEDESIGN_REF}" \
        ".[api,pipeline]" && \
    rm -rf "$HOME/.cache"

COPY --from=builder /opt/build/binaries/* $VIRTUAL_ENV/bin

# add python call to entrypoint so we can directly pass job id as parameter to image
ENTRYPOINT ["tini", "--", "/opt/venv/bin/python", "-m", "evedesign_server.backends.generic"]