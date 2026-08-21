#!/usr/bin/python
"""
OpenRouter Provider Module
==========================

Overview
--------

This module provides OpenRouter LLM provider implementation.

Exports
-------

- :doc:`OpenRouterProvider <modules/providers/squirrel.modules.providers.openrouter>`
    Concrete implementation of IProvider for OpenRouter LLMs

"""
# Imports
from .OpenRouterProvider import OpenRouterProvider

__all__ = [
    OpenRouterProvider.__name__
]
