FROM mcr.microsoft.com/playwright:v1.60.0-noble

ARG PLAYWRIGHT_MCP_VERSION=0.0.75

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1440x900x24

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        supervisor \
        tini \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Base image включає Playwright 1.60 браузери, але @playwright/mcp тягне
# свою версію (1.61-alpha) — браузери треба download'ити окремо.
RUN npm install -g "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" \
 && cd /usr/lib/node_modules/@playwright/mcp \
 && ./node_modules/.bin/playwright install --with-deps chromium

RUN mkdir -p /profile /var/log/supervisor \
 && chown -R pwuser:pwuser /profile /var/log/supervisor

COPY docker/playwright/supervisord.conf /etc/supervisor/conf.d/playwright-mcp.conf

EXPOSE 8931 6080

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
