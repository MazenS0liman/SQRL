#!/usr/bin/python
"""
Error Handlers Module
=====================

Overview
--------

This module provides global exception handling and custom exception classes for Squirrel.
It defines exception handlers for FastAPI and custom application exceptions with
appropriate HTTP status codes and error messages.

Exception Classes
-----------------

:py:class:`AppException`
    Base application exception class with customizable status codes and detail messages.

:py:class:`NotFoundException`
    Exception raised when a resource is not found (HTTP 404).

:py:class:`BadRequestException`
    Exception raised when the request is invalid (HTTP 400).

:py:class:`UnauthorizedException`
    Exception raised when the user is not authorized (HTTP 401).

"""

import traceback
from fastapi import Request, status
from fastapi.responses import JSONResponse
from loguru import logger

async def exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Global exception handler for unhandled exceptions.

    Catches all unhandled exceptions, logs them with full traceback,
    and returns a standardized 500 Internal Server Error response.

    :param request: The incoming HTTP request
    :type request: Request
    
    :param exc: The unhandled exception that was raised
    :type exc: Exception
    
    :returns: JSON response with error details and HTTP 500 status code
    :rtype: JSONResponse
    """
    logger.error(f"Unhandled exception: {str(exc)}")
    logger.error(traceback.format_exc())
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "message": str(exc) if str(exc) else "An unexpected error occurred",
        },
    )


class AppException(Exception):
    """
    Base application exception class.

    Provides customizable status codes and detail messages for application errors.
    All custom exception classes inherit from this base class.

    :param status_code: HTTP status code for the error (default: 500)
    :type status_code: int
    
    :param detail: Detailed error message for the client
    :type detail: str
    """
    
    def __init__(
        self,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail: str = "Internal server error",
    ):
        self.status_code = status_code
        self.detail = detail


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """
    Handler for custom application exceptions.

    Processes AppException instances and returns a JSON response with the
    exception's status code and detail message.

    :param request: The incoming HTTP request
    :type request: Request
    
    :param exc: The custom application exception
    :type exc: AppException
    
    :returns: JSON response with exception details and appropriate HTTP status code
    :rtype: JSONResponse
    """
    logger.error(f"Application exception: {exc.detail}")
    
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


class NotFoundException(AppException):
    """
    Exception raised when a resource is not found.

    Returns HTTP 404 Not Found status code with optional custom detail message.

    :param detail: Descriptive message about the missing resource (default: "Resource not found")
    :type detail: str
    """
    
    def __init__(self, detail: str = "Resource not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class BadRequestException(AppException):
    """
    Exception raised when the request is invalid.

    Returns HTTP 400 Bad Request status code with optional custom detail message.

    :param detail: Descriptive message about the request error (default: "Invalid request")
    :type detail: str
    """
    
    def __init__(self, detail: str = "Invalid request"):
        super().__init__(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


class UnauthorizedException(AppException):
    """
    Exception raised when the user is not authorized.

    Returns HTTP 401 Unauthorized status code with optional custom detail message.

    :param detail: Descriptive message about the authorization error (default: "Not authorized")
    :type detail: str
    """
    
    def __init__(self, detail: str = "Not authorized"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail) 

