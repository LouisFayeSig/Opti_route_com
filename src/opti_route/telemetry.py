"""Structured usage events collected from stdout by Azure Container Apps.

Never send URLs, request parameters, addresses, credentials or exception messages.
Context belongs to the current Streamlit execution, never a shared client instance.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import Lock
from uuid import uuid4

_context: ContextVar[dict] = ContextVar("usage_context", default={})
_logger = logging.getLogger("opti_route.usage")
_handler_lock = Lock()


def emit(event: str, **fields) -> None:
    with _handler_lock:
        if not _logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
            _logger.setLevel(logging.INFO)
            _logger.propagate = False
    payload = {
        "schema": "opti_route.usage.v1",
        "event_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **_context.get(),
        **fields,
    }
    _logger.info(json.dumps(payload, ensure_ascii=True))


def bind_session(user, state) -> None:
    identity = (user.tenant_id, user.principal_id, user.method, user.role)
    previous = state.get("_usage_identity")
    new_session = previous != identity or "_usage_session_id" not in state
    if new_session:
        state["_usage_session_id"] = str(uuid4())
    _context.set({
        "session_id": state["_usage_session_id"],
        "user_id": user.principal_id or "anonymous",
        "tenant_id": user.tenant_id,
        "auth_method": user.method,
        "role": user.role,
    })
    if new_session:
        emit("session_started")
        state["_usage_identity"] = identity


def reset_context() -> None:
    _context.set({})


def session_event(user, state, event: str) -> None:
    """Streamlit callbacks can run before the script rebinds its context."""
    bind_session(user, state)
    emit(event)


def request(send, *args, service: str, operation: str, method: str, **kwargs):
    started = time.perf_counter()
    try:
        response = send(*args, **kwargs)
    except Exception as exc:
        emit("api_call", service=service, operation=operation, method=method,
             success=False, status_code=None, error_type=type(exc).__name__,
             duration_ms=round((time.perf_counter() - started) * 1000, 2))
        raise
    retries = getattr(getattr(response, "raw", None), "retries", None)
    history = getattr(retries, "history", ())
    emit("api_call", service=service, operation=operation, method=method,
         status_code=response.status_code, success=response.status_code < 400,
         retry_count=len(history) if isinstance(history, (tuple, list)) else 0,
         duration_ms=round((time.perf_counter() - started) * 1000, 2))
    return response


def traced(event: str):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                emit(event, success=False, error_type=type(exc).__name__,
                     duration_ms=round((time.perf_counter() - started) * 1000, 2))
                raise
            emit(event, success=True,
                 duration_ms=round((time.perf_counter() - started) * 1000, 2))
            return result
        return wrapped
    return decorate
