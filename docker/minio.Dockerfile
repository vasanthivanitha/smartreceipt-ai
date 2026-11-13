# syntax=docker/dockerfile:1
# MinIO no longer distributes the pinned release through all public image registries.
# Build the unmodified AGPLv3 source; keep this source URL with distributions.
FROM golang:1.24.3-bookworm AS build
ARG MINIO_RELEASE=RELEASE.2025-04-22T22-12-26Z
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export CURL_CA_BUNDLE=/run/secrets/proxy_ca; fi; \
    curl -fsSL "https://github.com/minio/minio/archive/refs/tags/${MINIO_RELEASE}.tar.gz" -o /tmp/minio.tar.gz && \
    echo '1a63209927b9d1fb239b2a776b7b01d1902eff19379a108f4bc7005edcc0968c  /tmp/minio.tar.gz' | sha256sum -c - && \
    mkdir /src && tar -xzf /tmp/minio.tar.gz -C /src --strip-components=1
WORKDIR /src
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    CGO_ENABLED=0 go build -buildvcs=false -p 4 -trimpath -ldflags='-s -w' -o /minio .
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* && useradd -u 10001 -m minio && mkdir /data && chown minio /data
COPY --from=build /minio /usr/local/bin/minio
USER minio
VOLUME /data
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
