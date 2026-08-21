#!/usr/bin/python
"""
Abstract Agent Module
=====================

Overview
~~~~~~~~

This module provides abstract base classes and interfaces for all agents in the Squirrel system.
It defines the contract that all specific agent implementations must follow for interacting
with Large Language Models.

.. py:data:: IAgent
   :type: type[IAgent]

   Abstract base interface for all LLM-based agents

"""

# Imports
from squirrel.modules.agents.abstract.IAgent import IAgent

# Exports
__all__ = [
    IAgent.__name__
]
