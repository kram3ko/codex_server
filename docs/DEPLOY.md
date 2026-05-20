# Production deploy — Dokploy + Traefik

Compose-orchestrated production deploy. Dokploy керує lifecycle (pull → build → restart), Traefik робить TLS (Let's Encrypt) + path-routing на твій домен.

## Передумови

- VPS (Hetzner CX22 / AWS Lightsail / DigitalOcean Droplet) з public IP
- Ubuntu 24.04 LTS, ≥ 2 vCPU, ≥ 4 GB RAM, ≥ 40 GB disk
- DNS-запис `A codex.your-domain.com → <VPS-IP>`
- SSH-доступ як `root` (один раз для install)

## 1. Install Dokploy

SSH на VPS і запустити офіційний installer:

```bash
ssh root@<VPS-IP>
curl -sSL https://dokploy.com/install.sh | sh
```

Скрипт встановить Docker, Traefik (як `dokploy-traefik` контейнер) і Dokploy UI на `:3000`.

Створи перший admin user через браузер: `http://<VPS-IP>:3000`.

## 2. Initial domain config у Dokploy

У Dokploy UI → Settings → Server:
- Server Domain: `dokploy.your-domain.com` (для самого UI під TLS)
- HTTPS enabled
- Let's Encrypt email: `your@email`

## 3. Create application

У Dokploy UI:
1. **Projects** → New Project → `codex-server`
2. **Add Service** → Application → name `codex-stack`
3. **Source** → Git (Provider: GitHub)
   - Авторизуйся через Dokploy → GitHub OAuth app (один раз на server)
   - Repository: `kram3ko/codex_server`
   - Branch: `main`
4. **Build Type** → Docker Compose
   - Compose Path: `docker/docker-docker-compose.prod.yml`
5. **Environment** — paste значення з `.env` (Dokploy має UI текстове поле для всіх env vars). Обов'язкові:
   - `DOMAIN=codex.your-domain.com`
   - `GH_TOKEN`, `TG_BOT_TOKEN`, всі ключі і паролі з `.env`
6. **Deploy**

При першому деплої Dokploy:
- `git clone` твоєї репи
- `docker compose -f docker/docker-docker-compose.prod.yml build`
- `docker compose -f docker/docker-docker-compose.prod.yml up -d`
- Traefik автоматично підхопить labels і виставить TLS

## 4. Auto-deploy on push

У Dokploy → твоя application → Settings → **Webhook URL** (Dokploy генерує).

GitHub → Settings → Webhooks → Add webhook:
- Payload URL: `<dokploy-webhook-url>`
- Content type: `application/json`
- Events: just the `push` event

Push до `main` → GitHub шле POST → Dokploy робить pull+build+up. Без SSH у CI.

## 5. Backups

Dokploy → твоя application → Backups → New Backup:
- Database: postgres (codex-postgres)
- Destination: S3 (Cloudflare R2 / AWS S3)
- Schedule: `0 3 * * *` (3:00 щодня)
- Retention: 30 днів

## Локальний run prod-like

Без Dokploy/Traefik, але з тими ж production images:

```sh
DOMAIN=localhost docker compose \
  --env-file ../.env \
  -p codex_server \
  -f docker/docker-docker-compose.prod.yml \
  up -d --build
```

(Запускати з директорії `docker/` — `env_file` у compose йде по relative path.)

Зверни увагу: Traefik labels у `docker-compose.prod.yml` посилаються на `dokploy-network` яка локально не існує. Для чистого локального prod-run створи її:

```sh
docker network create dokploy-network || true
```

Без Traefik у мережі labels просто ігноруються — сервіси крутяться, але не маршрутизуються. Для public-доступу локально (наприклад через ngrok чи тестовий nginx) — запусти свій reverse-proxy окремо.

## Що НЕ робить Dokploy за тебе

- Migrations: `entrypoint.sh` codex-server вже виконує `alembic upgrade head` на старті. Якщо ти змінив schema — push → auto-deploy → migration run автоматично. Перед руйнівними migrations завжди роби `pg_dump` руками.
- Secret rotation: Dokploy не ротує токени. Один раз на 3 місяці онови `GH_TOKEN` (expiration 2026-08-16 для поточного `private_codex_server`).
- Codex-cli sidecars: уже у `docker/docker-docker-compose.prod.yml` (admin + guest). Без `docker.sock` mount-у, без `~/.ssh` — токен через `GH_TOKEN` env.

## Емердженси

VPS не відповідає / Dokploy зависнув:
- SSH-сися напряму: `ssh root@<VPS-IP>`
- `docker ps -a` побачиш чи контейнери крутяться
- `docker logs codex-server --tail 100` подивишся логи
- `systemctl restart docker` якщо щось зависло на рівні Docker daemon
- Перезапуск Dokploy UI: `docker restart dokploy`
