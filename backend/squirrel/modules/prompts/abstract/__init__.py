#!/usr/bin/python
"""
Abstract Prompt Generator Module
=================================

Overview
~~~~~~~~

This module provides the abstract interface for prompt generators in the SQRL system.

Exports
~~~~~~~

- IPromptGenerator: Abstract base interface for all prompt generators

"""

from .IPromptGenerator import IPromptGenerator

__all__ = [
    IPromptGenerator.__name__
]
