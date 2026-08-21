#!/usr/bin/python
"""
Request Context Module
======================

Overview
--------

This module provides request context management using contextvars for tracking
and accessing request-scoped information across the application.

Context Variables
-----------------

:py:data:`current_request_id`
    ContextVar storing the current request ID for request tracing and correlation.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from typing import Optional
from fastapi import Request
from contextvars import ContextVar

# Context variable to store the current request ID
current_request_id: ContextVar[Optional[str]] = ContextVar('current_request_id', default=None)

def get_request_id() -> str:
    """
    Get the current request ID from context or return 'unknown'.

    :returns: The current request ID or 'unknown' if not available in context
    :rtype: str
    """
    request_id = current_request_id.get()
    return request_id if request_id else 'unknown'


def set_request_id(request_id: str) -> None:
    """
    Set the current request ID in context.

    Stores the request ID in the context variable for access across the application.

    :param request_id: The request ID to store in context
    :type request_id: str
    """
    current_request_id.set(request_id)


def get_request_id_from_request(request: Request) -> str:
    """
    Get request ID from FastAPI request object.

    Retrieves the request ID stored in the FastAPI request state object,
    typically set by the RequestLoggingMiddleware.

    :param request: FastAPI Request object
    :type request: Request
    
    :returns: The request ID from request state or 'unknown' if not available
    :rtype: str
    """
    return getattr(request.state, 'request_id', 'unknown')