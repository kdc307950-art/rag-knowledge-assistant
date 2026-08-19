"""FastAPI 会话历史的边界与隔离测试。"""

import uuid

from backend import session
from enterprise_rag.core.constants import MAX_MESSAGES


def test_session_history_is_bounded_to_max_messages():
    session_id = uuid.uuid4().hex
    for index in range(MAX_MESSAGES + 2):
        session.append_message(session_id, "user", str(index))

    messages = session.get_messages(session_id)

    assert len(messages) == MAX_MESSAGES
    assert messages[0]["content"] == "2"


def test_session_histories_are_isolated():
    first_id = uuid.uuid4().hex
    second_id = uuid.uuid4().hex
    session.append_message(first_id, "user", "first")
    session.append_message(second_id, "user", "second")

    assert session.get_messages(first_id) == [{"role": "user", "content": "first"}]
    assert session.get_messages(second_id) == [{"role": "user", "content": "second"}]


def test_session_is_bound_to_principal():
    first_id = session.get_or_create_session_id(None, "hr.viewer")
    assert session.get_or_create_session_id(first_id, "hr.viewer") == first_id
    try:
        session.get_or_create_session_id(first_id, "it.viewer")
    except session.SessionOwnershipError:
        pass
    else:
        raise AssertionError("session must not cross principal boundary")


def test_unknown_client_session_is_rejected():
    try:
        session.get_or_create_session_id("client-controlled-id", "hr.viewer")
    except session.SessionOwnershipError:
        pass
    else:
        raise AssertionError("unknown client session ids must not be accepted")
