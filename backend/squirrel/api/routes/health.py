#!/usr/bin/python
"""
Health Route
============

Overview
--------

Provides API endpoints for application health checks and status monitoring.
Enables monitoring and verification of the SQRL application's availability and uptime.

Endpoints
---------
.. code-block:: http

    GET /

Health check endpoint that returns application status and uptime.

"""

# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import time
from typing import Dict

# FastAPI
from fastapi import APIRouter

# Settings
from squirrel.core.config import settings

# API Router
router = APIRouter()

#: Application startup timestamp for uptime calculation
START_TIME = time.time()

# ——————————————————————————————————————————————————————————————
# Endpoints

@router.get("")
async def health_check() -> Dict:
    """
    Application health check endpoint.
    
    Performs a simple health check and returns comprehensive information about
    the SQRL application's current status, including uptime and system memory usage.
    This endpoint is useful for monitoring and load balancer health probes.

    :returns: A dictionary containing:
    
        - **status** (str): Application status ("ok" when healthy)
        - **name** (str): Application name from configuration
        - **environment** (str): Current deployment environment
        - **uptime_seconds** (float): Time elapsed since application startup (in seconds)
        - **memory** (dict): System memory information:
        
            - **memory_percent** (float): Process memory usage as percentage
            - **memory_mb** (float): Process memory usage in megabytes
            - **status** (str): "psutil not installed" if psutil unavailable
    :rtype: Dict

    .. note::
    
        - Memory information requires the psutil library to be installed
        - If psutil is unavailable, memory information will contain a status message
        - This endpoint performs minimal processing and is suitable for frequent polling
    
    .. rubric:: Example Response
    
    With psutil installed::
    
        {
            "status": "ok",
            "name": "SQRL",
            "environment": "development",
            "uptime_seconds": 3600.5,
            "memory": {
                "memory_percent": 2.5,
                "memory_mb": 128.3
            }
        }
    
    Without psutil::
    
        {
            "status": "ok",
            "name": "SQRL",
            "environment": "development",
            "uptime_seconds": 3600.5,
            "memory": {
                "status": "psutil not installed"
            }
        }
    """
    uptime = time.time() - START_TIME
    
    # Get system memory info
    memory_info = {}
    try:
        import psutil
        process = psutil.Process(os.getpid())
        memory_info = {
            "memory_percent": process.memory_percent(),
            "memory_mb": process.memory_info().rss / 1024 / 1024
        }
    except ImportError:
        # psutil not installed, just skip the memory info
        memory_info = {"status": "psutil not installed"}
    
    return {
        "status": "ok",
        "name": settings.APP_NAME,
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": uptime,
        "memory": memory_info,
    }
