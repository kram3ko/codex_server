# Codex CLI у режимі WebSocket app-server.
# Доустановлено python3 + ripgrep + grep + git + jq + curl/wget + bubblewrap,
# щоб модель могла вільно користуватися shell_tool / unified_exec у workspace.
FROM alpine:3.20

ARG CODEX_VERSION=0.130.0
ARG TARGETARCH

RUN apk add --no-cache \
        ca-certificates \
        bash \
        coreutils \
        curl \
        wget \
        docker-cli \
        docker-cli-compose \
        git \
        grep \
        ripgrep \
        jq \
        bubblewrap \
        libgcc \
        python3 \
        py3-pip \
 && set -eux; \
    case "${TARGETARCH:-arm64}" in \
        arm64) CODEX_ARCH=aarch64-unknown-linux-musl ;; \
        amd64) CODEX_ARCH=x86_64-unknown-linux-musl ;; \
        *) echo "Unsupported arch: ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${CODEX_ARCH}.tar.gz" -o /tmp/codex.tar.gz; \
    tar -xzf /tmp/codex.tar.gz -C /tmp; \
    mv "/tmp/codex-${CODEX_ARCH}" /usr/local/bin/codex; \
    chmod +x /usr/local/bin/codex; \
    rm -f /tmp/codex.tar.gz

COPY docker/codex/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN adduser -D -u 1000 -h /home/codex codex \
 && mkdir -p /home/codex/.codex /home/codex/workspace \
 && chown -R codex:codex /home/codex

ENV CODEX_HOME=/home/codex/.codex \
    HOME=/home/codex

USER codex
WORKDIR /home/codex/workspace

EXPOSE 4500

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
