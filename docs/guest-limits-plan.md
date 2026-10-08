# План: runtime-настройки лимитов гостя и медиа (временный)

**Статус 2026-10-08:** уровень 1 реализован — таблица `runtime_settings`
(singleton, типизированные колонки + CHECK), сервис
`app/services/runtime_settings/`, REST `GET/PUT /api/settings/limits`,
вкладка Limits в админке.

Источник: диалог в TG от 2026-10-07 + аудит хардкода по коду.

## Текущее состояние

- Rate-limit на RunTurn захардкожен в `app/services/rate_limit/service.py`:
  admin без лимита, web user 2 active, tg guest 3 active. Часовой счётчик
  (было 30 / 10 в час) временно выключен (`hourly=None`).
- Лимита на фото нет: 1 фото на TG-сообщение, альбом приходит отдельными
  сообщениями и упирается в active-лимит. Нет лимита размера/количества
  вложений (`app/tg/media.py`).
- Прочий хардкод: TG stream/render лимиты (`app/tg/progress.py`), TTL Redis
  локов/lease (`app/services/turns/`), retry/timeout транспорта
  (`app/services/codex/transport.py`), pagination по RPC.

## Цель

Вынести guest/web/admin лимиты, media-лимиты и TTL/retry из хардкода в
управляемую конфигурацию, менять без рестарта где возможно.

## Подход

Лимиты turn-ов, фото/файлов, TTL/window/retry хранятся в БД
(`runtime_settings`), перечитываются на каждый turn через кэш с TTL.
Native tools sidecar не трогаем, они вне плана.

## Предлагаемые настройки

```
tg_guest.active_limit
tg_guest.hourly_limit
tg_guest.window_s
tg_guest.max_images_per_turn
tg_guest.max_upload_bytes
web_user.active_limit
web_user.hourly_limit
```

## Открытые вопросы

- Где хранить: таблица settings (уже есть `app/api/settings/`) vs env.
- Как кэшировать на worker (refresh interval уже есть для integrations).
- Нужен ли media-group batching для альбомов вместо лимита.
