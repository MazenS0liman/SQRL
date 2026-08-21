#!/usr/bin/python
"""
Prompt Generator Abstract Module
================================

Overview
~~~~~~~~

This module defines the abstract class for prompt generators used in the Squirrel system.
Prompt generators are responsible for creating system and user prompts that guide language model 
behavior and provide context for various agent operations.

The IPromptGenerator abstract class extends both ABC (Abstract Base Class) and Loggable to provide 
a standardized contract for implementing specialized prompt generators across different agent types.

Prompts
~~~~~~~

- System Prompts: Define the overall behavior and guidelines for the language model.
- User Prompts: Provide specific instructions or context for individual tasks or interactions.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from abc import ABC, abstractmethod
from typing import Any

# Logging
from squirrel.core.logging import Loggable

class IPromptGenerator(ABC, Loggable):
    """
    Abstract base class for prompt generators in the SQRL system.
    
    This class defines the interface for generating system and user prompts that guide
    the behavior of language models used by various agents. Concrete implementations
    must provide methods to generate these prompts according to specific requirements.
    
    :inherits: ABC, Loggable
    
    :raises NotImplementedError: If the abstract methods are not implemented in subclasses.
    
    Example
    ~~~~~~~
    
    .. code-block:: python
    
        class MyPromptGenerator(IPromptGenerator):
            def generate_system_prompt(self) -> str:
                return "System prompt content"
                
            def generate_user_prompt(self, **kwargs) -> str:
                return "User prompt content"
    
    """

    def __init__(self):
        super().__init__()

    @abstractmethod
    def generate_system_prompt(self, prompt_type: Any) -> str:
        """
        Generate the system prompt that defines the overall behavior and guidelines for the language model.
        
        :param prompt_type:
        
        :return: A string representing the system prompt.
        :rtype: str
        """
        pass

    @abstractmethod
    def generate_user_prompt(self, prompt_type: Any, **kwargs) -> str:
        """
        Generate the user prompt that provides specific instructions or context for individual tasks or interactions.
        
        :param prompt_type:
        :param kwargs: Additional keyword arguments for customizing the user prompt.

        :return: A string representing the user prompt.
        :rtype: str
        """
        pass
