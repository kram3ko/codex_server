# Settings rollout

Schema (`integrations`, `telegram_chats`, `users.tg_username`, `runtime_settings`)
comes from Alembic revisions `b7d1e5f2a9c3` and `a1f3c9d2b7e4`; the server
entrypoint applies them on start. The master key is generated on first boot
while no encrypted rows exist yet.

Tokens are no longer read from the environment at runtime. After the first
deploy either add GitHub / GitLab / Telegram in Settings → Integrations, or run
the one-shot import, which copies GH_TOKEN, GITLAB_TOKEN and TG_BOT_TOKEN into
encrypted rows when that provider has no saved integration yet. It never
prints values or edits .env. GitLab import assumes gitlab.com. Old file-vault
entries are not imported.

```sh
docker compose --env-file .env -f docker/docker-compose.yml run --rm --no-deps --entrypoint python codex-server -m scripts.prepare_settings
```

Until a Telegram integration exists the bot stays off. The guest sidecar does
not receive integration credentials.

The master key lives in `codex_integration_key`, separately from PostgreSQL.
Back it up securely: losing it makes saved credentials unreadable. Preparation
refuses to generate a replacement key when encrypted records already exist.
SSH and Git runtime credentials live in the `codex_integration_runtime` tmpfs
volume, rebuilt from encrypted records after startup. The old `codex_ssh` volume
is no longer mounted; it is left untouched and may contain old plaintext keys.
Old environment entries remain on disk until the owner removes them. Neither
runtime bot configuration nor the sidecar reads them after import/recreation.

Settings are administrator-only. Secret responses contain presence flags, never
plaintext. Replacing a bot connection cancels its active turns before rebuilding
its sessions. Workers pick up saved settings at the configured refresh interval.
Membership status is learned from Telegram events; imported chats have unknown
membership until an event arrives. A saved/enabled token is not a claim that a
provider connection has been verified.

Verification before rollout used isolated fixtures for the UI and unit tests;
real Git, SSH and Telegram connectivity must be checked after rollout using
the Check connection actions and a test message to the bot.
