#!/usr/bin/python
"""
Usage Context
=============

Request-scoped attribution for LLM usage events.

The HTTP middleware stamps the feature ``source`` (chat, workspace, notebook,
pipeline, ...) and :func:`get_current_user` stamps the authenticated ``user_id``.
:class:`IAgent` reads both when it records a call, so no agent constructor needs
to know which HTTP route triggered it.

``contextvars`` are copied into ``run_in_threadpool`` work, which is where most
agents actually run, so values set in a route remain visible inside agents.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


#: Authenticated user that owns the current request / task.
current_usage_user_id: ContextVar[Optional[str]] = ContextVar(
    "current_usage_user_id", default=None
)

#: Feature that triggered the call (chat, workspace, notebook, pipeline, ...).
current_usage_source: ContextVar[str] = ContextVar(
    "current_usage_source", default="agent"
)


def set_usage_user_id(user_id: Optional[str]) -> None:
    """Attribute subsequent LLM calls in this context to ``user_id``."""
    current_usage_user_id.set(user_id or None)


def get_usage_user_id() -> Optional[str]:
    """Return the user id stamped on the current context, if any."""
    return current_usage_user_id.get()


def set_usage_source(source: Optional[str]) -> None:
    """Attribute subsequent LLM calls to a feature name."""
    current_usage_source.set((source or "agent").strip().lower() or "agent")


def get_usage_source() -> str:
    """Return the feature name stamped on the current context."""
    return current_usage_source.get() or "agent"


def source_for_path(path: str) -> str:
    """
    Map an API path to a coarse feature name used in the usage ledger.

    :param path: Request path, e.g. ``/api/workspace/abc/build/structured``.
    :return: One of ``chat``, ``workspace``, ``notebook``, ``pipeline``,
        ``files``, ``connector``, or ``api``.
    """
    lowered = (path or "").lower()
    if "/chat" in lowered:
        return "chat"
    if "/workspace" in lowered:
        return "workspace"
    if "/notebook" in lowered:
        return "notebook"
    if "/pipeline" in lowered:
        return "pipeline"
    if "/files" in lowered:
        return "files"
    if "/connector" in lowered:
        return "connector"
    return "api"


@contextmanager
def usage_context(
    user_id: Optional[str] = None,
    source: Optional[str] = None,
) -> Iterator[None]:
    """
    Temporarily override usage attribution for a block of work.

    Used by Celery tasks, which have no HTTP request to inherit context from.
    """
    user_token = current_usage_user_id.set(user_id or current_usage_user_id.get())
    source_token = current_usage_source.set(
        (source or current_usage_source.get()).strip().lower() or "agent"
    )
    try:
        yield
    finally:
        current_usage_user_id.reset(user_token)
        current_usage_source.reset(source_token)
