from app.tg.sessions import ChatSession


def _stub_session() -> ChatSession:
    return ChatSession(client=object(), db_chat_id=1, db_user_id=1)  # type: ignore[arg-type]


def test_consume_steer_returns_false_when_idle() -> None:
    session = _stub_session()

    assert session.consume_steer() is False
    assert session.steer_pending is False


def test_consume_steer_atomic_test_and_clear() -> None:
    session = _stub_session()
    session.steer_pending = True

    assert session.consume_steer() is True
    assert session.steer_pending is False
    # Second call sees idle state (no double consume).
    assert session.consume_steer() is False
