#!/usr/bin/python
"""
Middleware Module
=================

Overview
--------

This module provides FastAPI middleware components for request/response processing,
logging, and context management. Middleware components handle cross-cutting concerns
like request tracking, logging, and context propagation.

Middleware Classes
------------------

:py:class:`RequestLoggingMiddleware`
    Middleware for request ID generation, request/response logging, and processing time tracking.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import time
import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

# Logging
from loguru import logger

# Request Context
from squirrel.core.request_context import set_request_id

# Middleware Classes
class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for request ID generation and request/response logging.

    Functionality:
    ==============
    - Generates unique request IDs for tracking requests through the system
    - Logs incoming requests with method, path, and client information
    - Tracks request processing time and logs responses with status codes
    - Stores request ID in request state and context variables for access in handlers
    - Adds request ID to response headers (X-Request-ID) for client correlation
    - Logs errors with timing and exception information if request processing fails
    """

    async def dispatch(self, request: Request, call_next):
        """
        Process an incoming request and generate logging output.

        Processing Pipeline:
        ====================

        1. **Generate Request ID** - Create unique UUID for request tracking
        2. **Store in Request State** - Make ID accessible in request context
        3. **Set in Context Variable** - Store for cross-context access
        4. **Log Incoming Request** - Record request method, path, and client
        5. **Process Request** - Call next middleware/handler
        6. **Calculate Processing Time** - Measure request duration
        7. **Log Response** - Record status code and timing (success path)
        8. **Add Response Header** - Include X-Request-ID in response headers
        9. **Handle Errors** - Log exceptions and re-raise (error path)

        :param request: The incoming HTTP request
        :type request: Request
        
        :param call_next: Callable to process the request through next middleware
        :type call_next: Callable
        
        :returns: Response object with X-Request-ID header
        :rtype: Response
        
        :raises Exception: Re-raises any exceptions from request processing
        """
        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Store request ID in request state for access in other parts of the app
        request.state.request_id = request_id

        # Also set in context variable for easier access
        set_request_id(request_id)

        start_time = time.time()

        # Log the incoming request (basic info only)
        logger.info(
            f"🔵 REQUEST [{request_id}] {request.method} {request.url.path} from {request.client.host if request.client else 'unknown'}")

        try:
            # Process the request
            response = await call_next(request)

            # Calculate processing time
            process_time = time.time() - start_time

            # Log successful response (basic info only)
            logger.info(f"🟢 RESPONSE [{request_id}] - {response.status_code} ({process_time:.4f}s)")

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            # Calculate processing time for error case
            process_time = time.time() - start_time

            # Log the error
            logger.error(f"🔴 ERROR [{request_id}] - {type(e).__name__}: {str(e)} ({process_time:.4f}s)")

            # Re-raise the exception to let the exception handlers deal with it
            raise