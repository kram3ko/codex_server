"""Unit tests для `TurnService` lifecycle transitions.

Spec mock-аємо `AsyncSession` через `_FakeSession` що знає `add`/`flush`/
`execute`/`get` рівно стільки, скільки треба для відповідного method-у.
Postgres unique constraint / CAS — гарантовано БД-сайдом, тестимо логіку
shrink: правильний `WHERE`, правильні `values`, повертає `bool` за `rowcount`.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects import postgresql

from app.models import Turn, TurnStatus
from app.services.codex.sidecar import SidecarName
from app.services.turns.schemas import TurnCreate
from app.services.turns.service import TurnService


class _Result:
    def __init__(
        self,
        rowcount: int = 0,
        scalar: Any = None,
        all_rows: Iterable | None = None,
        first_row: Any = None,
    ) -> None:
        self.rowcount = rowcount
        self._scalar = scalar
        self._all = list(all_rows) if all_rows is not None else []
        self._first = first_row

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> _Scalars:
        return _Scalars(self._all)

    def first(self) -> Any:
        return self._first


class _Scalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """Async session double — без real engine. Запам'ятовує statements."""

    def __init__(
        self,
        rowcount: int = 0,
        get_row: Any = None,
        rows: Iterable | None = None,
        scalar: Any = None,
        first_row: Any = None,
    ) -> None:
        self.added: list = []
        self.flushed = 0
        self.executed: list = []
        self._rowcount = rowcount
        self._get_row = get_row
        self._rows = rows
        self._scalar = scalar
        self._first = first_row

    def add(self, obj: Any) -> None:
        # SQLAlchemy ORM зазвичай заповнює `id` після flush; для тесту назначаємо
        # детерміністично, щоб `create_starting` міг побудувати stream_key.
        if isinstance(obj, Turn) and obj.id is None:
            obj.id = 1
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def execute(self, statement: Any) -> _Result:
        self.executed.append(statement)
        return _Result(
            rowcount=self._rowcount,
            scalar=self._scalar,
            all_rows=self._rows,
            first_row=self._first,
        )

    async def get(self, _entity: Any, _pk: Any) -> Any:
        return self._get_row

    async def refresh(self, obj: Any) -> None:
        # No-op — у реальному SessionLocal це підтягне server_default-и
        # (`created_at`/`updated_at`); у тесті об'єкт уже має значення які
        # модель валідує через Pydantic.
        if isinstance(obj, Turn):
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            if obj.created_at is None:
                obj.created_at = now
            if obj.updated_at is None:
                obj.updated_at = now
            if obj.heartbeat_at is None:
                obj.heartbeat_at = now


def _sql(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


async def test_create_starting_inserts_row_and_sets_stream_key() -> None:
    session = _FakeSession()
    service = TurnService()

    row = await service.create_starting(
        session,
        TurnCreate(chat_id=1, user_id=2, user_message_id=3, sidecar="admin"),
    )

    assert row.id == 1
    assert row.status is TurnStatus.STARTING
    assert row.stream_key == "turn:1:events"
    assert row.chat_id == 1 and row.user_id == 2
    assert session.flushed == 2  # first flush bumps id, second persists stream_key


async def test_mark_running_returns_true_on_rowcount() -> None:
    session = _FakeSession(rowcount=1)
    service = TurnService()

    promoted = await service.mark_running(session, 42, "thread-x", "turn-y")

    assert promoted is True
    sql = _sql(session.executed[0])
    assert "WHERE turns.id = 42" in sql
    assert "turns.status = 'STARTING'" in sql
    assert "status='RUNNING'" in sql.replace(" ", "")
    assert "codex_thread_id='thread-x'" in sql.replace(" ", "")
    assert "codex_turn_id='turn-y'" in sql.replace(" ", "")


async def test_mark_running_returns_false_when_already_running() -> None:
    session = _FakeSession(rowcount=0)
    service = TurnService()

    promoted = await service.mark_running(session, 42, "thread-x", "turn-y")

    assert promoted is False


async def test_finalize_once_emits_conditional_update(monkeypatch) -> None:
    # Publish moved to an after_commit hook; FakeSession has no commit lifecycle,
    # so we only assert the SQL contract here. The hook itself is exercised via
    # integration paths in the live runner.
    monkeypatch.setattr(
        "app.services.turns.service._publish_after_commit", lambda *_a, **_kw: None
    )
    session = _FakeSession(rowcount=1, first_row=(99, 42))
    service = TurnService()

    finalized = await service.finalize_once(
        session,
        7,
        TurnStatus.FAILED,
        error_code="codex_error",
        error_detail="boom",
    )

    assert finalized is True
    sql = _sql(session.executed[0])
    assert "WHERE turns.id = 7" in sql
    assert "turns.status IN ('STARTING', 'RUNNING')" in sql
    assert "status='FAILED'" in sql.replace(" ", "")
    assert "error_code='codex_error'" in sql.replace(" ", "")


async def test_finalize_once_rejects_non_terminal() -> None:
    session = _FakeSession()
    service = TurnService()
    try:
        await service.finalize_once(session, 7, TurnStatus.RUNNING)
    except ValueError as exc:
        assert "terminal" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-terminal status")


async def test_heartbeat_updates_starting_or_running_rows() -> None:
    session = _FakeSession(rowcount=1)
    service = TurnService()

    owned = await service.heartbeat(session, 99)

    assert owned is True
    sql = _sql(session.executed[0])
    assert "WHERE turns.id = 99" in sql
    assert "turns.status IN ('STARTING', 'RUNNING')" in sql


async def test_get_active_for_chat_filters_by_live_statuses() -> None:
    now = datetime.now(UTC)
    live = Turn(
        id=5,
        chat_id=1,
        user_id=1,
        user_message_id=1,
        status=TurnStatus.RUNNING,
        stream_key="turn:5:events",
        sidecar=SidecarName.ADMIN.value,
        heartbeat_at=now,
        created_at=now,
        updated_at=now,
    )
    session = _FakeSession(get_row=None, scalar=live)
    service = TurnService()

    found = await service.get_active_for_chat(session, 1)

    assert found is not None and found.id == 5
    sql = _sql(session.executed[0])
    assert "turns.chat_id = 1" in sql
    assert "turns.status IN ('STARTING', 'RUNNING')" in sql


async def test_find_stale_active_uses_threshold() -> None:
    threshold = timedelta(minutes=2)
    now = datetime.now(UTC)
    stale_at = now - timedelta(minutes=5)
    stale = Turn(
        id=8,
        chat_id=3,
        user_id=1,
        user_message_id=1,
        status=TurnStatus.RUNNING,
        stream_key="turn:8:events",
        sidecar=SidecarName.ADMIN.value,
        heartbeat_at=stale_at,
        created_at=stale_at,
        updated_at=stale_at,
    )
    session = _FakeSession(rows=[stale])
    service = TurnService()

    rows = await service.find_stale_active(session, threshold)

    assert len(rows) == 1 and rows[0].id == 8
    sql = _sql(session.executed[0])
    assert "turns.status IN ('STARTING', 'RUNNING')" in sql
    assert "turns.heartbeat_at <" in sql
