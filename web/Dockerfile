# Build context = repo root (must contain ./web and ./protos):
#   docker build -f web/Dockerfile -t codex-web .
# `npm run build` запускає buf generate ../protos і vite build → static dist/.
FROM node:24-alpine AS build

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
