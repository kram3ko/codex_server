"""TaskIQ broker для durable turn execution.

`codex-worker` контейнер запускає `taskiq worker app.worker:broker
app.services.turns.tasks --workers 2 --ack-type when_executed`. Web container
залишається тонким — `RunTurn` RPC робить `execute_turn.kiq(turn.id)` і tail-ить
per-turn Redis stream.

Idempotency: `execute_turn` перевіряє `turn.status in TERMINAL` на старті і
return-ає no-op. Worker crash → at-least-once redelivery → safe replay.
"""

from taskiq_redis import RedisAsyncResultBackend, RedisStreamBroker

from app.config import settings

broker = RedisStreamBroker(url=settings.REDIS_URL).with_result_backend(
    RedisAsyncResultBackend(redis_url=settings.REDIS_URL)
)
