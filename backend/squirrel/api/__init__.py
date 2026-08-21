#!/usr/bin/python
"""
API Module
==========

Overview
--------

This module provides the main API configuration and route registration for SQRL.

This is the entry point for the SQRL API package, which aggregates all route modules.

Modules Exported
----------------

:py:mod:`api_router`
    The main API router aggregating all route modules and endpoints from:
    
    - :py:mod:`health`
    - :py:mod:`files`
    - :py:mod:`chat`
    - :py:mod:`dummy`

Usage
-----

Import the main router in your FastAPI application:

.. code-block:: python

    from squirrel.api import api_router
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(api_router, prefix="/api")

"""

from squirrel.api.api import api_router

__all__ = [
    "api_router"
]
 