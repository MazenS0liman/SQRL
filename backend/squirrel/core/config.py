#!/usr/bin/python
"""
Configuration Module
====================

Overview
--------

This module provides application configuration and settings management for Squirrel.
It defines environment variables, directory paths, and authentication tokens, and
color codes for terminal output.

Configuration Classes
---------------------

- :py:class:`Settings`
    Main settings class using Pydantic for configuration management.

- :py:class:`Colors`
    ANSI color codes for terminal output formatting.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import sys
import platform
from typing import List, Optional
from pydantic_settings import BaseSettings

from dotenv import load_dotenv
load_dotenv()

# Import appropriate toml library based on Python version
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        import toml as tomllib

def get_version() -> str:
    """
    Read version from pyproject.toml file.

    :return: Version string from pyproject.toml or "0.1.0" if not found.
    :rtype: str
    """
    try:
        # Get the project root directory (2 levels up from config.py: squirrel/core/ -> .)
        config_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(config_dir))
        pyproject_path = os.path.join(project_root, "pyproject.toml")
        
        if sys.version_info >= (3, 11) or hasattr(tomllib, 'load'):
            # Use tomllib (Python 3.11+) or tomli
            with open(pyproject_path, 'rb') as f:
                pyproject_data = tomllib.load(f)
        else:
            # Fallback to toml library
            with open(pyproject_path, 'r') as f:
                pyproject_data = tomllib.load(f)
        
        if "project" in pyproject_data and "version" in pyproject_data["project"]:
            return pyproject_data["project"]["version"]

        # Optional fallback for Poetry-only projects
        if (
            "tool" in pyproject_data
            and "poetry" in pyproject_data["tool"]
            and "version" in pyproject_data["tool"]["poetry"]
        ):
            return pyproject_data["tool"]["poetry"]["version"]
            
    except (FileNotFoundError, KeyError, Exception) as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}")
        return "0.1.0"
    
    return "0.1.0"

# ——————————————————————————————————————————————————————————————
# Settings class
class Settings(BaseSettings):
    """
    Application settings and configuration.
    
    Uses Pydantic BaseSettings to load configuration from environment variables
    and .env files. Provides centralized access to all application settings including
    paths, credentials, logging levels, and operational parameters.
    
    Settings Categories
    ~~~~~~~~~~~~~~~~~~~~

    :Application Settings:
        APP_NAME, ENVIRONMENT, LOG_LEVEL, VERSION, CURRENCY

    :Platform Settings:
        OS_TYPE, MULTIPROCESSING_CONTEXT, MIN_PROCESS_COUNT, MIN_WORKER_COUNT

    """
    # Application settings
    #: str: Application name - Default is "SQRL"
    APP_NAME: str = "SQRL"
    #: str: Application environment - Default is "development"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    #: str: Logging level - Default is "INFO"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    #: str: Application version - Read from pyproject.toml
    VERSION: str = get_version()

    # Platform settings
    #: str: Operating system type - Automatically detected using platform.system()
    OS_TYPE: str = platform.system()
    #: str: Multiprocessing context - "spawn" for Windows, "fork" for Unix-based systems
    MULTIPROCESSING_CONTEXT: str = "spawn" if platform.system() == "Windows" else "fork"
    #: int: Minimum number of processes - Default is 2
    MIN_PROCESS_COUNT: int = 2
    #: int: Minimum number of workers - Default is 4
    MIN_WORKER_COUNT: int = 4

    # Database settings
    #: str: PostgreSQL connection string used by every service.
    DATABASE_URL: str = ""
    #: str: Legacy alias for DATABASE_URL, kept for backwards compatibility.
    POSTGRES_DSN: str = ""

    # MinIO / S3-compatible blob storage settings
    MINIO_ENDPOINT: str = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
    MINIO_ACCESS_KEY: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    MINIO_SECRET_KEY: str = os.getenv("MINIO_SECRET_KEY", "strongpassword123")
    MINIO_BUCKET: str = os.getenv("MINIO_BUCKET", "squirrel")
    MINIO_REGION: Optional[str] = os.getenv("MINIO_REGION")
    MINIO_SECURE: bool = os.getenv("MINIO_SECURE", "False").lower() in ("true", "1", "yes")

    # Secret-encryption keys
    #: Optional[str]: Fernet key used to encrypt stored provider tokens.
    TOKEN_ENCRYPTION_KEY: Optional[str] = None
    #: Optional[str]: Fernet key used to encrypt connector secrets at rest.
    SQUIRREL_CONNECTOR_SECRET_KEY: Optional[str] = None

    # Bootstrap admin account. Disabled by default: create the first user
    # through /api/auth/register instead of shipping a known admin/admin login.
    #: bool: Create a default admin account on startup when it does not exist.
    CREATE_DEFAULT_ADMIN: bool = False
    DEFAULT_ADMIN_USERNAME: str = "admin"
    DEFAULT_ADMIN_EMAIL: str = "admin@squirrel.local"
    DEFAULT_ADMIN_PASSWORD: Optional[str] = None

    # CORS. Comma-separated list; never "*" together with credentials.
    #: str: Allowed browser origins, comma separated.
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8000,http://localhost:3000"

    @property
    def database_url(self) -> str:
        """Resolve the effective database URL (DATABASE_URL wins)."""
        return self.DATABASE_URL or self.POSTGRES_DSN

    @property
    def cors_origins(self) -> List[str]:
        """Return CORS origins as a list, trimming blanks."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

# ——————————————————————————————————————————————————————————————
# Colors class
class Colors:
    """
    ANSI color codes for terminal output formatting.

    Provides constants for colorizing terminal output with standard ANSI escape codes.
    Useful for making console logs and output more readable and visually distinct.

    Color Constants
    ~~~~~~~~~~~~~~~

    :py:data:`HEADER` - Purple color for headers
    :py:data:`OKBLUE` - Blue color for informational messages
    :py:data:`OKCYAN` - Cyan color for progress indicators
    :py:data:`OKGREEN` - Green color for success messages
    :py:data:`WARNING` - Yellow color for warnings
    :py:data:`FAIL` - Red color for error messages
    :py:data:`BOLD` - Bold text formatting
    :py:data:`UNDERLINE` - Underlined text formatting
    :py:data:`RED` - Bright red color
    :py:data:`ENDC` - End color formatting

    Example usage:

    .. code-block:: python

        print(f"{Colors.OKGREEN}Success!{Colors.ENDC}")
        print(f"{Colors.WARNING}Warning message{Colors.ENDC}")
    """
    #: str: ANSI escape code for purple color (headers)
    HEADER = '\033[95m'
    #: str: ANSI escape code for blue color (informational messages)
    OKBLUE = '\033[94m'
    #: str: ANSI escape code for cyan color (progress indicators)
    OKCYAN = '\033[96m'
    #: str: ANSI escape code for green color (success messages)
    OKGREEN = '\033[92m'
    #: str: ANSI escape code for yellow color (warnings)
    WARNING = '\033[93m'
    #: str: ANSI escape code for red color (error messages)
    FAIL = '\033[91m'
    #: str: ANSI escape code to end/reset color formatting
    ENDC = '\033[0m'
    #: str: ANSI escape code for bold text formatting
    BOLD = '\033[1m'
    #: str: ANSI escape code for underlined text formatting
    UNDERLINE = '\033[4m'
    #: str: ANSI escape code for bright red color
    RED = '\033[31m'
    
# ——————————————————————————————————————————————————————————————
settings = Settings()
