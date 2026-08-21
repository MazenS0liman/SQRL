#!/usr/bin/python
"""
Logging Module
==============

Overview
--------

This module configures and provides logging functionality for Squirrel using loguru.
It sets up structured logging with console and file handlers, log rotation, and
integration with standard logging libraries.

Features
--------

- Loguru-based logging with color formatting
- Console and file output with daily rotation
- Log compression and retention policies
- Logging interception for stdout/stderr
- Integration with FastAPI and Uvicorn
- Class-level logger descriptor for convenient access
- Structured JSON logging support

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import sys
from datetime import datetime
from pathlib import Path

# Logging
import logging
from loguru import logger

# Settings
from squirrel.core.config import settings

# Create logs directory
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

logger.level("DEBUG",  color="<blue>")
logger.level("INFO", color="<blue>")
logger.level("SUCCESS", color="<green>")
logger.level("WARNING", color="<yellow>")
logger.level("ERROR", color="<red>")
logger.level("CRITICAL", color="<red>")

# Silent PaddleOCR logging
logging.getLogger("ppocr").setLevel(logging.ERROR)

from loguru import logger as _loguru_logger
from typing import Any

class ClassLogger:
    """
    Descriptor that returns a loguru logger bound to the owner class name.

    Provides convenient class-level logging by returning a logger instance
    bound to the owner class name. Can be used as a class attribute.

    Example usage:

    .. code-block:: python

        class MyClass:
            logger = ClassLogger()

    :returns: Loguru logger instance bound to the class name
    :rtype: loguru.Logger
    """
    def __get__(self, instance: Any, owner: type):
        # owner will be the class; bind once per call (cheap)
        return _loguru_logger.bind(class_name=owner.__name__)

class Loggable:
    """
    Mixin class that provides logging capabilities.

    Classes that inherit from Loggable gain access to a class-level logger
    descriptor that provides convenient logging functionality.
    
    Example
    ~~~~~~~

        .. code-block:: python

            class MyClass(Loggable):
                def do_something(self):
                    self.logger.info("Doing something")
    
    """
    logger = ClassLogger()
    
    def __init__(self) -> None:
        super().__init__()

class InterceptHandler(logging.Handler):
    """
    Custom handler for intercepting and formatting logs.

    Intercepts standard Python logging output and redirects it to loguru
    for consistent formatting and handling. Supports both direct logging
    calls and stream output (stdout/stderr).
    """
    # static shared logger
    
    def __init__(self):
        """
        Initialize the InterceptHandler.

        Sets up the handler with INFO level as default for intercepted messages.
        """
        super().__init__(level=logging.NOTSET)
        
    def emit(self, record):
        """
        Emit a log record.

        Processes a logging record and forwards it to loguru with appropriate
        level translation and caller frame detection.

        :param record: The logging record to emit
        :type record: logging.LogRecord
        """
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where the logged message originated
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    def write(self, message):
        """
        Write a message to the logging system.

        Formats raw message strings and logs them as INFO level messages.

        :param message: The message to write
        :type message: str
        """
        if message.strip():
            record = {
                "time": datetime.now().isoformat(),
                "level": "INFO",
                "message": message.strip(),
            }
            logger.info(record["message"])

    def isatty(self):
        """Report a non-interactive stream so console formatters can configure safely."""
        return False

    def fileno(self):
        """Provide a best-effort file descriptor for code that probes stream metadata."""
        try:
            return sys.__stdout__.fileno()
        except Exception:
            return -1
    
    def flush(self):
        """
        Flush any pending log records.

        This is a no-op for loguru since it handles flushing internally.
        """
        pass

def setup_logging():
    """
    Configure logging with loguru.

    .. rubric:: Configuration Steps

    1. **Remove default handlers** - Clear default loguru handlers
    2. **Add console handler** - Stream logs to stderr with color formatting
    3. **Add file handler** - Write logs to daily-rotated files with JSON structure
    4. **Configure rotation** - Create new log files daily at midnight
    5. **Set retention** - Keep logs for 30 days before deletion
    6. **Setup compression** - Compress rotated logs to save disk space
    7. **Intercept streams** - Redirect stdout/stderr to logging system
    8. **Replace loggers** - Integrate FastAPI and Uvicorn loggers

    :returns: Configured loguru logger instance
    :rtype: loguru.Logger
    """
    # Remove default handlers
    logger.remove()
    
    # Add console logger
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # Add file logger
    logger.add(
        "logs/app_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # New file created each day at midnight
        retention="30 days",  # Keep logs for 30 days
        compression="zip",  # Compress rotated logs
        level=settings.LOG_LEVEL,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        serialize=True,  # JSON format for structured logging
    )

    # Intercept stdout/stderr
    sys.stdout = InterceptHandler()
    sys.stderr = InterceptHandler()
    
    # Replace other loggers with loguru
    for name in ["uvicorn", "uvicorn.access", "fastapi"]:
        logging_logger = logging.getLogger(name)
        logging_logger.handlers = [InterceptHandler()]
    
    logger.info(f"Logging configured with level: {settings.LOG_LEVEL}")
    return logger

# Import this after the function definition to avoid circular imports
import logging