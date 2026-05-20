FROM node:24-alpine AS builder

WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY protos /protos
COPY web/ ./
RUN npm run build


FROM nginx:1.27-alpine

COPY --from=builder /web/dist /usr/share/nginx/html
COPY docker/web/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
