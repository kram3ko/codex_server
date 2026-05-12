# Build context = repo root (must contain ./web and ./protos):
#   docker build -f web/Dockerfile -t codex-web .
# `npm run build` запускає buf generate ../protos і vite build → static dist/.
# Sentry vite plugin (build-time): SENTRY_AUTH_TOKEN + SENTRY_URL +
# SENTRY_ORG + SENTRY_PROJECT → uploads sourcemaps до Bugsink і прибирає
# .map з final dist (вони лишаються лише у Bugsink). Без цих ARG'ів plugin
# no-op, build проходить без source maps upload.
FROM node:24-alpine AS build

ARG SENTRY_URL
ARG SENTRY_AUTH_TOKEN
ARG SENTRY_ORG
ARG SENTRY_PROJECT
ARG VITE_SENTRY_DSN
ARG VITE_SENTRY_RELEASE
ARG VITE_SENTRY_TRACES_SAMPLE_RATE

ENV SENTRY_URL=$SENTRY_URL
ENV SENTRY_AUTH_TOKEN=$SENTRY_AUTH_TOKEN
ENV SENTRY_ORG=$SENTRY_ORG
ENV SENTRY_PROJECT=$SENTRY_PROJECT
ENV VITE_SENTRY_DSN=$VITE_SENTRY_DSN
ENV VITE_SENTRY_RELEASE=$VITE_SENTRY_RELEASE
ENV VITE_SENTRY_TRACES_SAMPLE_RATE=$VITE_SENTRY_TRACES_SAMPLE_RATE

WORKDIR /app

COPY web/package*.json ./
RUN npm install

COPY protos/ ../protos/
COPY web/ ./
RUN npm run build

FROM nginx:1.29-alpine

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
