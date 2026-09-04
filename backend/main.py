"""
Squirrel API Application Entry Point

Overview
--------

Main FastAPI application module that initializes and configures the Squirrel API server.
Sets up routes, middleware, error handlers, logging, and CORS configuration for
the REST API endpoints.

Features
--------

1. **FastAPI Application Setup**: Configures the main FastAPI application instance.
2. **Middleware Integration**: Adds CORS support and request logging middleware.
3. **Error Handling**: Registers exception handlers for graceful error responses.
4. **Logging**: Initializes comprehensive application logging.
5. **API Routes**: Registers all API route modules.
6. **Startup Events**: Handles application initialization tasks

"""
# ——————————————————————————————————————————————————————————————
# Imports

import time
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from squirrel.api.api import api_router
from squirrel.core.config import settings
from squirrel.core.middleware import RequestLoggingMiddleware
from squirrel.core.error_handlers import AppException, app_exception_handler, exception_handler
from squirrel.core.logging import setup_logging

# Setup logging
logger = setup_logging()

import warnings
warnings.filterwarnings("ignore")

# Create FastAPI application
app = FastAPI(
    title=settings.APP_NAME,
    description=settings.APP_NAME + " API",
    version=settings.VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Add error handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(Exception, exception_handler)


# Add the comprehensive logging middleware
app.add_middleware(RequestLoggingMiddleware)


# Include API router
app.include_router(api_router, prefix="/api")


FRONTEND_DIR = Path(__file__).resolve().parent / "frontend-dist"
FRONTEND_INDEX = FRONTEND_DIR / "index.html"


# Root route
@app.get("/", status_code=status.HTTP_200_OK)
async def root():
    """Serve the production frontend, or the API metadata in dev images."""
    if FRONTEND_INDEX.is_file():
        return FileResponse(FRONTEND_INDEX)
    return {
        "name": settings.APP_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "docs": "/docs" if settings.ENVIRONMENT != "production" else None,
        "health": "/api/health",
    }


@app.get("/{path:path}", include_in_schema=False)
async def frontend_fallback(path: str):
    """Serve frontend assets and support browser refreshes on SPA routes."""
    if not FRONTEND_INDEX.is_file():
        return {"detail": "Not found"}

    requested = (FRONTEND_DIR / path).resolve()
    if requested.is_file() and FRONTEND_DIR in requested.parents:
        return FileResponse(requested)
    return FileResponse(FRONTEND_INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_config=None,
        timeout_keep_alive=3600
    )

