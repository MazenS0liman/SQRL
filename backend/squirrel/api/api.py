#!/usr/bin/python
"""
API Router Module
=================

Overview
--------

This module configures and registers all API routes for the SQRL FastAPI application.

"""
# Imports

# Standard Libraries
from fastapi import APIRouter

# Route Modules
from squirrel.api.routes import (
    health,
    files,
    chat,
    workspace,
    connector,
    notebook,
    auth,
    logs,
    token,
    jobs,
    notifications,
    pipelines,
    usage,
)

api_router = APIRouter()

# Add health check route
api_router.include_router(health.router, prefix="/health", tags=["health"])

# Add chat route
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])

# Add library route
api_router.include_router(workspace.router, prefix="/workspace", tags=["workspace"])

# Add notebook route
api_router.include_router(notebook.router, prefix="/notebook", tags=["notebook"])

# Add auth route
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Add logging route
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])

# Add connector route
api_router.include_router(connector.router, prefix="/connector", tags=["connector"])

# Add files route
api_router.include_router(files.router, prefix="/file", tags=["file"])

# Add token route
api_router.include_router(token.router, prefix="/token", tags=["token"])

# Add background-job route
api_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])

# Add notification route
api_router.include_router(
    notifications.router, prefix="/notifications", tags=["notifications"]
)

# Add ETL pipeline route
api_router.include_router(pipelines.router, prefix="/pipelines", tags=["pipelines"])

# Add LLM usage monitoring route
api_router.include_router(usage.router, prefix="/usage", tags=["usage"])
