#!/usr/bin/python
"""
Abstract Provider Module
========================

Overview
--------

This module defines the abstract IProvider class, 
which serves as a blueprint for various provider implementations.

Features
-------- 

- Defines the IProvider abstract class with essential methods for LLM providers.
- Supports file uploads, response generation, structured responses, JSON generation, information extraction, table extraction, embeddings, and similarity calculations.

Examples
--------

.. code-block:: python

    # Imports
    from orca.modules.providers.abstract.IProvider import IProvider, Provider

    # Initialize a provider (example with OpenAI)
    provider: IProvider = OpenAIProvider(model_name="gpt-4", config={})

    # Generate a response
    response = provider.generate_response(input={"prompt": "Hello, world!"})
    print(response)

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import numpy as np
from enum import Enum
from typing import Dict, Any
from abc import ABC, abstractmethod
import pandas as pd

# Logging
from squirrel.core.logging import Loggable

# ——————————————————————————————————————————————————————————————
# Available Providers
class Provider(Enum):
    """
    Provider Enum
    -------------
    
    **Description:**
    
        This enum defines the different provider implementations supported by the system.

    **Enum Values:**
        
        - **OPENAI (str):** OpenAI provider implementation.
            *Value:* "openai"
        - **GEMINI (str):** Google Gemini provider implementation.
            *Value:* "gemini"
        - **GROQ (str):** Groq provider implementation.
            *Value:* "groq"
    
    """
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"

# ——————————————————————————————————————————————————————————————
# Abstract Provider Class
class IProvider(ABC, Loggable):
    """
    Abstract Provider Class
    -----------------------
    
    **Description:**
    
        This class defines the interface for various LLM provider implementations,
        including methods for file uploads, response generation, structured responses,
        JSON generation, information extraction, table extraction, embeddings, and similarity calculations.

    **Attributes:**
        
        - **model_name (str):** The name of the model.
        - **config (dict):** Configuration dictionary for the provider.
        
    **Methods:**
        
        - :meth:`generate_response`
            Generate a response based on input
        - :meth:`generate_structured_response`
            Generate a pydantic structured response
        - :meth:`generate_json`
            Generate a JSON response
        - :meth:`available_model_names`
            Get list of available model names

    """

    def __init__(
        self,
        model_name: str,
        config: dict
    ) -> None:
        """
        Initialize the provider with model name and configuration.
        
        :param model_name: The name of the model
        :type model_name: str
        
        :param config: Configuration dictionary for the provider
        :type config: dict
        
        :return: None
        :rtype: None
        
        **Example:**
        
        .. code-block:: python

            from orca.modules.providers.abstract.IProvider import IProvider
            
            class MyProvider(IProvider):
                def __init__(self):
                    super().__init__(model_name="my-model", config={})
                    
                def upload_file(self, file_path: str) -> str:
                    pass
                    
                def generate_response(self, input: Dict[str, Any]) -> str:
                    pass
        
        """
        super().__init__()
        self._model_name = model_name
        self._config = config or {}
        
        # Token tracking attributes
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._request_count = 0

        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._last_request_tokens = 0

    @property
    def model_name(self) -> str:
        """
        Get the model name.
        
        :return: The name of the model
        :rtype: str
        """
        return self._model_name

    @property
    def config(self) -> dict:
        """
        Get the configuration dictionary.
        
        :return: The configuration dictionary
        :rtype: dict
        """
        return self._config
    
    @property
    def total_input_tokens(self) -> int:
        """
        Get the total input tokens used.
        
        :return: Total input tokens
        :rtype: int
        """
        return self._total_input_tokens
    
    @property
    def total_output_tokens(self) -> int:
        """
        Get the total output tokens used.
        
        :return: Total output tokens
        :rtype: int
        """
        return self._total_output_tokens

    @property
    def total_tokens(self) -> int:
        """
        Get the total tokens used.
        
        :return: Total tokens
        :rtype: int
        """
        return self._total_tokens
    
    @property
    def request_count(self) -> int:
        """
        Get the total number of requests made.
        
        :return: Total request count
        :rtype: int
        """
        return self._request_count
    
    @property
    def last_input_tokens(self) -> int:
        """
        Get the last input tokens used.
        
        :return: Last input tokens
        :rtype: int
        """
        return self._last_input_tokens
    
    @property
    def last_output_tokens(self) -> int:
        """
        Get the last output tokens used.
        
        :return: Last output tokens
        :rtype: int
        """
        return self._last_output_tokens
    
    @property
    def last_request_tokens(self) -> int:
        """
        Get the last request tokens used.
        
        :return: Last request tokens
        :rtype: int
        """
        return self._last_request_tokens

    @model_name.setter
    def model_name(
        self,
        value: str
    ) -> None:
        """
        Set the model name.

        :param value: The new model name
        :type value: str
        """
        self._model_name = value
        
    @config.setter
    def config(
        self,
        value: dict
    ) -> None:
        """
        Set the configuration dictionary.

        :param value: The new configuration dictionary
        :type value: dict
        
        :raises ValueError: If config is not a dictionary
        """
        if not isinstance(value, dict):
            raise ValueError("Config must be a dictionary.")
        self._config = value
    
    @total_input_tokens.setter
    def total_input_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the total input tokens used.

        :param value: The new total input tokens
        :type value: int
        
        :raises ValueError: If total input tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Total input tokens must be a non-negative integer.")
        self._total_input_tokens = value

    @total_output_tokens.setter
    def total_output_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the total output tokens used.

        :param value: The new total output tokens
        :type value: int
        
        :raises ValueError: If total output tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Total output tokens must be a non-negative integer.")
        self._total_output_tokens = value
        
    @total_tokens.setter
    def total_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the total tokens used.

        :param value: The new total tokens
        :type value: int
        
        :raises ValueError: If total tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Total tokens must be a non-negative integer.")
        self._total_tokens = value
        
    @request_count.setter
    def request_count(
        self,
        value: int
    ) -> None:
        """
        Set the total number of requests made.

        :param value: The new request count
        :type value: int
        
        :raises ValueError: If request count is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Request count must be a non-negative integer.")
        self._request_count = value

    @last_input_tokens.setter
    def last_input_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the last input tokens used.

        :param value: The new last input tokens
        :type value: int
        
        :raises ValueError: If last input tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Last input tokens must be a non-negative integer.")
        self._last_input_tokens = value
        
    @last_output_tokens.setter
    def last_output_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the last output tokens used.

        :param value: The new last output tokens
        :type value: int
        
        :raises ValueError: If last output tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Last output tokens must be a non-negative integer.")
        self._last_output_tokens = value
        
    @last_request_tokens.setter
    def last_request_tokens(
        self,
        value: int
    ) -> None:
        """
        Set the last request tokens used.

        :param value: The new last request tokens
        :type value: int
        
        :raises ValueError: If last request tokens is not a non-negative integer
        """
        if not isinstance(value, int) or value < 0:
            raise ValueError("Last request tokens must be a non-negative integer.")
        self._last_request_tokens = value

    def get_total_usage(self) -> Dict[str, int]:
        """
        Get the total usage statistics for all API calls made through this provider instance.
        
        **Description:**
        
            This method returns a summary of token usage including input tokens, 
            output tokens, total tokens, and the number of requests made.
        
        :return: Dictionary containing token usage with keys:
        
                 - "input_tokens" (int): Total tokens used in inputs
                 - "output_tokens" (int): Total tokens used in outputs
                 - "total_tokens" (int): Total tokens used
                 - "request_count" (int): Number of API requests made
        
        :rtype: Dict[str, int]
        
        **Example:**
        
        .. code-block:: python
        
            from orca.modules.providers.openai.OpenAIProvider import OpenAIProvider
            
            # Initialize OpenAI Provider            
            provider = OpenAIProvider(
                api_key="OPENAI_API_KEY",
                model_name="gpt-4.1-2025-04-14"
            )
            
            # Make some API calls...
            provider.generate_response({"user_prompt": "Hello!"})
            
            # Get total usage
            usage = provider.get_total_usage()
            print(f"Total tokens used: {usage['total_tokens']}")
            print(f"Requests made: {usage['request_count']}")
        
        """
        return {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": self._total_tokens,
            "request_count": self._request_count
        }
        
    def previous_usage(self) -> Dict[str, int]:
        """
        Get the usage statistics for the last API call made through this provider instance.
        
        **Description:**
        
            This method returns a summary of token usage for the most recent API call,
            including input tokens, output tokens, and total tokens used.
        
        :return: Dictionary containing token usage for the last request with keys:
        
                    - "input_tokens" (int): Tokens used in the last input
                    - "output_tokens" (int): Tokens used in the last output
                    - "total_tokens" (int): Total tokens used in the last request
        
        :rtype: Dict[str, int]
        """
        return {
            "input_tokens": self._last_input_tokens,
            "output_tokens": self._last_output_tokens,
            "total_tokens": self._last_request_tokens
        }
    
    def reset_usage(self) -> None:
        """
        Reset all usage statistics.
        
        **Description:**
        
            This method clears the accumulated usage statistics, useful for tracking
            usage per session or resetting before a new batch of requests.
        
        **Example:**
        
        .. code-block:: python
        
            from orca.modules.providers.openai.OpenAIProvider import OpenAIProvider
        
            # Initialize OpenAI Provider
            provider = OpenAIProvider(
                api_key="OPENAI_API_KEY",
                model_name="gpt-4.1-2025-04-14"
            )
            
            # Make some calls...
            provider.generate_response({"user_prompt": "Hello!"})
            usage = provider.get_total_usage()
            print(f"Tokens in session 1: {usage['total_tokens']}")
            
            # Reset for new session
            provider.reset_usage()
            usage = provider.get_total_usage()
            print(f"Tokens in session 2: {usage['total_tokens']}")  # Will be 0
        
        """
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._total_tokens = 0
        self._request_count = 0
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._last_request_tokens = 0
        
    @abstractmethod
    def _update_token_usage(
        self,
        usage: Any
    ) -> None:
        """
        Update the token usage statistics based on the provided usage data.
        
        **Description:**
        
            This method updates the internal counters for prompt tokens, total tokens,
            and request count based on the usage information received from API responses.

        :param usage: Usage data containing token counts
        :type usage: Any
        
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def generate_response(
        self,
        input: Dict[str, Any]
    ) -> str:
        """
        Generate a response based on the input.
        
        **Description:**
        
            The method processes the input and generates a response
            based on the provided prompt or data.

        :param input: The input data for generating the response
        :type input: Dict[str, Any]
        
        :return: The generated response
        :rtype: str
        """
        pass

    @abstractmethod
    def generate_structured_response(
        self,
        input: Dict[str, Any]
    ) -> str:
        """
        Generate a pydantic structured response.
        
        **Description:**
        
            The method processes the input and generates a structured response
            based on a pydantic model.

        :param input: The input data for generating the structured response
        :type input: Dict[str, Any]
        
        :return: The generated structured response based on pydantic model
        :rtype: str
        """
        pass

    @abstractmethod
    def generate_json(
        self,
        input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate a JSON response based on the provided input.
        
        **Description:**
        
            The method processes the input and generates a JSON response
            based on the provided user prompt.

        :param input: Input dictionary containing file_path (str) and user_prompt (str) for guiding JSON generation
        :type input: Dict[str, Any]
        
        :return: Generated JSON response
        :rtype: Dict[str, Any]
        """
        pass

    @abstractmethod
    def available_model_names(self) -> list:
        """
        Get a list of available model names for the provider.
        
        **Description:**

            The method retrieves and returns a list of model names
            that are supported by the specific provider implementation.
        
        :return: List of available model names
        :rtype: list
        """
        pass
