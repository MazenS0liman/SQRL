#!/usr/bin/python
"""
Providers Module
================

Overview
--------

This module provides LLM provider implementations for various AI platforms including
OpenAI, Gemini, Groq, OpenRouter and HuggingFace.

Exports
-------

- :doc:`IProvider <modules/providers/squirrel.modules.providers.abstract>`
    Abstract base class for all provider implementations.
- :doc:`GeminiProvider <modules/providers/squirrel.modules.providers.gemini>`
    Gemini LLM provider implementation.
- :doc:`GroqProvider <modules/providers/squirrel.modules.providers.groq>`
    Groq LLM provider implementation.
- :doc:`OpenRouterProvider <modules/providers/squirrel.modules.providers.openrouter>`
    OpenRouter LLM provider implementation.

"""
# Imports
from .abstract.IProvider import IProvider, Provider
from .gemini.GeminiProvider import GeminiProvider
from .groq.GroqProvider import GroqProvider
from .openrouter.OpenRouterProvider import OpenRouterProvider

__all__ = [
    IProvider.__name__,
    Provider.__name__,
    GeminiProvider.__name__,
    GroqProvider.__name__,
    OpenRouterProvider.__name__,
]
