FROM alpine:3.24

RUN set -eux \
 && apk add --no-cache \
        ca-certificates \
        tzdata \
        bash \
        coreutils \
        curl \
        wget \
        docker-cli \
        docker-cli-compose \
        git \
        github-cli \
        openssh-client \
        grep \
        ripgrep \
        jq \
        libgcc \
        python3 \
        py3-pip \
        glab \
 && curl -fsSL https://chatgpt.com/codex/install.sh -o /tmp/codex-install.sh \
 && CODEX_HOME=/opt/codex CODEX_INSTALL_DIR=/usr/local/bin CODEX_NON_INTERACTIVE=1 sh /tmp/codex-install.sh \
 && ln -sf /opt/codex/packages/standalone/current/bin/codex-code-mode-host /usr/local/bin/codex-code-mode-host \
 && rm -f /tmp/codex-install.sh

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
