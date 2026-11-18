from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from opti_route import telemetry


def user(identifier="person-1"):
    return SimpleNamespace(tenant_id="tenant", principal_id=identifier,
                           method="entra", role="atc")


def test_session_is_counted_once_and_identity_change_starts_another(monkeypatch):
    events = Mock()
    monkeypatch.setattr(telemetry, "emit", events)
    state = {}
    telemetry.bind_session(user(), state)
    first = state["_usage_session_id"]
    telemetry.bind_session(user(), state)
    assert events.call_count == 1
    telemetry.bind_session(user("person-2"), state)
    assert events.call_count == 2
    assert state["_usage_session_id"] != first


def test_context_does_not_leak_between_threads():
    telemetry.bind_session(user(), {})
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(telemetry._context.get).result() == {}
    assert telemetry._context.get()["user_id"] == "person-1"
    telemetry.reset_context()
    assert telemetry._context.get() == {}


def test_call_counts_retries_without_logging_request_data(monkeypatch):
    events = Mock()
    monkeypatch.setattr(telemetry, "emit", events)
    response = SimpleNamespace(status_code=429,
                               raw=SimpleNamespace(retries=SimpleNamespace(history=(1, 2))))
    send = Mock(return_value=response)
    assert telemetry.request(send, "https://secret", params={"key": "secret"},
                             method="GET", service="ign", operation="geocode") is response
    fields = events.call_args.kwargs
    assert fields["success"] is False
    assert fields["retry_count"] == 2
    assert "secret" not in str(events.call_args)


def test_network_failure_is_counted_and_reraised_without_message(monkeypatch):
    events = Mock()
    monkeypatch.setattr(telemetry, "emit", events)
    send = Mock(side_effect=RuntimeError("secret address"))
    with pytest.raises(RuntimeError, match="secret address"):
        telemetry.request(send, method="POST", service="azure_maps", operation="matrix")
    assert events.call_args.kwargs["success"] is False
    assert "secret address" not in str(events.call_args)


def test_route_error_is_preserved_and_counted(monkeypatch):
    events = Mock()
    monkeypatch.setattr(telemetry, "emit", events)

    @telemetry.traced("route_calculated")
    def calculate():
        raise ValueError("private data")

    with pytest.raises(ValueError):
        calculate()
    events.assert_called_once()
    assert events.call_args.kwargs["error_type"] == "ValueError"
    assert "private data" not in str(events.call_args)
