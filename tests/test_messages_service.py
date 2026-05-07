from typing import Any

from sqlalchemy.dialects import postgresql

from app.models import Message
from app.services.messages.service import MessageService


class _Scalars:
    def __init__(self, messages: list[Message]) -> None:
        self._messages = messages

    def all(self) -> list[Message]:
        return self._messages


class _Rows:
    def __init__(self, messages: list[Message]) -> None:
        self._messages = messages

    def scalars(self) -> _Scalars:
        return _Scalars(self._messages)


class _Session:
    def __init__(self, messages: list[Message]) -> None:
        self.messages = messages
        self.statement: Any | None = None

    async def execute(self, statement: Any) -> _Rows:
        self.statement = statement
        return _Rows(self.messages)


async def test_list_recent_fetches_latest_messages_and_returns_chronological_order() -> None:
    service = MessageService()
    newer = Message(id=12, chat_id=1, text="newer")
    older = Message(id=11, chat_id=1, text="older")
    session = _Session([newer, older])

    messages = await service.list_recent(session, chat_id=1, limit=10)  # type: ignore[arg-type]

    assert messages == [older, newer]
    sql = str(
        session.statement.compile(  # type: ignore[union-attr]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    assert "WHERE messages.chat_id = 1" in sql
    assert "ORDER BY messages.id DESC" in sql
    assert "LIMIT 10" in sql
