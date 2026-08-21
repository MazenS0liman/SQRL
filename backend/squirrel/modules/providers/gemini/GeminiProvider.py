#!/usr/bin/python
"""
Gemini Provider Module
=======================

Overview
--------

A module that defines the GeminiProvider class for interacting with Google's Gemini models.

Features
--------

- Generate text responses using Gemini models.
- Generate structured responses. (Not yet implemented)
- Generate JSON responses based on provided schema.
- Available model listing.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import re
import os
import json
import numpy as np
import pandas as pd
from enum import Enum
from io import StringIO

from google.genai.types import File
from typing_extensions import override
from typing import Dict, List, Any, Set, Optional

import dotenv
dotenv.load_dotenv()

# Third-Party Libraries
from google import genai
from google.genai import types

# Abstract Classes
from squirrel.modules.providers.abstract.IProvider import IProvider

# ——————————————————————————————————————————————————————————————
# Gemini Model Type Enum
class GeminiModelType(Enum):
    """
    Gemini Model Type Enum
    ----------------------
    
    **Description:**
    
        This enum defines the different Gemini model implementations supported by the system.
    
    **Enum Values:**
        
        - **GEMINI_2_5_PRO (str):** Gemini 2.5 Pro model.
            *Value:* "models/gemini-2.5-pro"
        - **GEMINI_2_5_FLASH (str):** Gemini 2.5 Flash model.
            *Value:* "models/gemini-2.5-flash"
            
    """
    GEMINI_2_5_PRO = "models/gemini-2.5-pro"
    GEMINI_2_5_FLASH = "models/gemini-2.5-flash"

# ——————————————————————————————————————————————————————————————
# Gemini Provider Class
class GeminiProvider(IProvider):
    """
    Gemini Provider Class
    ---------------------
    
    **Description:**
    
        This class provides comprehensive integration with Google's Gemini models through the IProvider interface.
        It supports file uploads and response generation.

    **Attributes:**

        - **model_name (str):** 
            The name of the Gemini model to use (e.g., "models/gemini-2.5-pro").
        - **config (dict):** 
            Verified configuration dictionary for the Gemini provider.
        - **api_env_var (str):** 
            The environment variable name for the API key.
        - **api_key (str):**
            The API key for authenticating with the Gemini API.
        - **client (genai.Client):** 
            The initialized Google Gemini client.
        
    **Methods:**
        
        - :meth:`generate_response`
            Generate a response using Gemini model
        - :meth:`generate_structured_response`
            Generate a pydantic structured response
        - :meth:`generate_json`
            Generate a JSON response with schema
        - :meth:`available_model_names`
            Get list of available Gemini model names
        - :meth:`get_verified_config`
            Verify and merge configuration based on model name

    **Example:**
    
    .. code-block:: python

        from squirrel.modules.providers.gemini.GeminiProvider import GeminiProvider

        # Initialize Gemini Provider
        gemini_provider = GeminiProvider(
            api_key="YOUR_GEMINI_API_KEY",
            model_name="models/gemini-2.5-pro",
            config={"temperature": 0.7}
        )

        # Generate a response
        response = gemini_provider.generate_response({"user_prompt": "Hello, Gemini!"})
        print(response)

    """
    def __init__(
        self,
        api_env_var: str,
        model_name: str,
        config: dict,
        api_key: Optional[str] = None,
    ) -> None:
        """
        Initialize the Gemini provider.
        
        **Description:**
        
            This constructor initializes the GeminiProvider with the necessary credentials
            and configuration for interacting with Google's Gemini models.

        :param api_env_var: The environment variable name for the API key
        :type api_env_var: str
        :param model_name: The name of the Gemini model to use (e.g., "models/gemini-2.5-pro")
        :type model_name: str
        :param config: Configuration dictionary for the Gemini provider
        :type config: dict
        
        :raises ValueError: If api_key is empty or model_name is not provided
        
        **Example:**
            
        .. code-block:: python

            from orca.modules.providers.gemini.GeminiProvider import GeminiProvider
        
            provider = GeminiProvider(
                api_env_var="GOOGLE_API_KEY",
                model_name="models/gemini-2.5-pro",
                config={"temperature": 0.7}
            )

        """
        super().__init__(
            model_name=model_name, 
            config=config
        )
        self._model_name = model_name
        self._config = self.get_verified_config(model_name, config or {})
        self._api_env_var = api_env_var
        self._api_key = api_key or (os.getenv(api_env_var, "") if api_env_var else "")
        self._client = genai.Client(api_key=self._api_key)

    @property
    def model_name(self) -> str:
        """
        Get the model name.
        
        :return: The name of the Gemini model
        :rtype: str
        """
        return self._model_name

    @property
    def config(self) -> dict:
        """
        Get the verified configuration.
        
        :return: The verified configuration dictionary
        :rtype: dict
        """
        return self._config

    @property
    def api_env_var(self) -> str:
        """
        Get the API environment variable name.
        
        :return: The environment variable name for the API key
        :rtype: str
        """
        return self._api_env_var

    @property
    def api_key(self) -> str:
        """
        Get the API key.
        
        :return: The API key for Gemini API
        :rtype: str
        """
        return self._api_key

    @property
    def client(self) -> genai.Client:
        """
        Get the initialized Gemini client.
        
        :return: The Google Gemini client instance
        :rtype: genai.Client
        """
        return self._client

    @model_name.setter
    def model_name(
        self,
        value: str
    ) -> None:
        """
        Set the model name.

        :param value: The new model name
        :type value: str
        
        :raises ValueError: If model name is not a non-empty string
        """
        if not isinstance(value, str) or not value:
            raise ValueError("Model name must be a non-empty string.")

        self._model_name = value

        # Re-verify config if model changes
        self._config = self.get_verified_config(value, self._config)

    @config.setter
    def config(
        self,
        value: Dict[str, Any]
    ) -> None:
        """
        Set and verify the configuration.

        :param value: The new configuration dictionary
        :type value: Dict[str, Any]
        
        :raises ValueError: If config is not a dictionary
        """
        if not isinstance(value, dict):
            raise ValueError("Config must be a dictionary.")
        self._config = self.get_verified_config(self._model_name, value)

    @api_env_var.setter
    def api_env_var(
        self,
        value: str
    ) -> None:
        """
        Set the API environment variable name.

        :param value: The new API environment variable name
        :type value: str
        
        :raises ValueError: If api_env_var is not a non-empty string
        """
        if not isinstance(value, str) or not value:
            raise ValueError("API environment variable name must be a non-empty string.")
        self._api_env_var = value
        self._api_key = os.getenv(value, "") if os.getenv(value, "") else ""
        self._client = genai.Client(api_key=self._api_key)

    @api_key.setter
    def api_key(
        self,
        value: str
    ) -> None:
        """
        Set a new API key directly.
        
        :param value: The new API key
        :type value: str
        
        .. note::

            If the value matches an environment variable name, it will resolve it automatically.
        
        """
        self._api_key = value
        self._client = genai.Client(api_key=self._api_key)

    def _update_token_usage(
        self, 
        usage: Any
    ) -> None:
        """
        Update token usage counters from API response usage object.
        
        **Description:**
        
            This method updates the total prompt tokens, total request count, and total tokens
            based on the usage metadata returned by Gemini after each request.

        :param usage: Usage metadata returned by Gemini (typically response.usage_metadata)
        :type usage: Any
        
        :return: None
        :rtype: None
        
        """
        if usage is None:
            return

        # Update cumulative token counts
        self._total_input_tokens += getattr(usage, "prompt_token_count", 0)
        self._total_output_tokens += getattr(usage, "thoughts_token_count", 0)
        self._total_tokens += getattr(usage, "total_token_count", 0)
        self._request_count += 1
        
        # Update last request token counts
        self._last_input_tokens = getattr(usage, "prompt_token_count", 0)
        self._last_output_tokens = getattr(usage, "thoughts_token_count", 0)
        self._last_request_tokens = getattr(usage, "total_token_count", 0)

        # Log per-request usage to help monitor costs
        self.logger.info(
            f"Gemini request #{self._request_count} | "
            f"Input Tokens: {getattr(usage, 'prompt_token_count', 0)} | "
            f"Output Tokens: {getattr(usage, 'thoughts_token_count', 0)} | "
            f"Total Tokens: {getattr(usage, 'total_token_count', 0)} | "
            f"Cumulative Total Tokens: {self._total_tokens}"
        )

    @staticmethod
    def get_verified_config(
        model_name: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Return verified configuration based on the model name.
        
        **Description:**
        
            This method ensures only allowed keys are included, 
            and merges with user config. Also, it provides model-specific 
            defaults for temperature, top_p, max_output_tokens, etc.

        :param model_name: The name of the model
        :type model_name: str
        
        :param config: User-provided configuration
        :type config: Dict[str, Any]
        
        :return: Verified configuration dictionary with allowed keys
        :rtype: Dict[str, Any]
        
        **Example:**
        
        .. code-block:: python
        
            from orca.modules.providers.gemini.GeminiProvider import GeminiProvider
            
            # Get verified config for Gemini 2.5 Pro
            verified_config = GeminiProvider.get_verified_config(
                "models/gemini-2.5-pro",
                {"temperature": 0.5, "max_output_tokens": 1500}
            )
            
            print(verified_config)
            # Output: {'temperature': 0.5, 'top_p': 0.9, 'max_output_tokens': 1500 }
        
        """
        model_type = model_name.lower()
        defaults: Dict[str, Any] = {}
        allowed_keys: Set = set()

        if model_type == GeminiModelType.GEMINI_2_5_PRO.value:
            defaults = {
                "temperature": 0.7,
                "top_p": 0.9,
                "max_output_tokens": 2048,
                "candidate_count": 1,
                "response_mime_type": "text/plain"
            }
            allowed_keys = set(defaults.keys())

        elif model_type == GeminiModelType.GEMINI_2_5_FLASH.value:
            defaults = {
                "temperature": 0.0,
                "top_p": 0.9,
                "max_output_tokens": 5024,
                "candidate_count": 1,
                "response_mime_type": "text/plain"
            }
            allowed_keys = set(defaults.keys())

        else:
            defaults = {}
            allowed_keys = set()

        # Merge with user config, but only keep allowed keys
        merged = {
            key: config.get(key, defaults[key])
            for key in allowed_keys
            if key in config
        }

        return merged

    @override
    def generate_response(
        self,
        input: Dict[str, Any]
    ) -> str:
        """
        Generate a response based on the input dictionary.
        
        **Description:**
        
            This method generates a text response from the Gemini model based on the
            user prompt provided in the input dictionary.

        :param input: Input dictionary containing user prompt and optional file_path with keys:
                      - "user_prompt" (str): Prompt for content generation
        :type input: Dict[str, Any]
        
        :return: The generated response from the model
        :rtype: str
        
        .. note::
        
            If a file_path is provided in the input, extracts information from that file.
            Otherwise, generates a response based on the user prompt.
        
        """
        try:
            system_prompt = input.get("system_prompt", '')
            user_prompt = input.get("user_prompt", '')
            contents = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
            
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=self.config
            )
        except Exception as err:
            self.logger.warning("Error occurred while generating content:", err)
            raise err

        # Update token usage
        self._update_token_usage(getattr(response, "usage_metadata", None))
        
        response = response.text
        return response

    @override
    def generate_structured_response(
        self,
        input: Dict[str, Any]
    ) -> Any:
        """
        Generate a structured response based on the input dictionary.
        
        **Description:**
        
            This method is intended to generate a structured response (e.g., pydantic model)
            from the Gemini model based on the system prompt, user prompt, and expected response model.
        
        :param input: Input dictionary containing system_prompt, user_prompt, and response_model with keys:
                      - "system_prompt" (str): System prompt for the model
                      - "user_prompt" (str): User prompt for the model
                      - "response_model" (Type): The expected response model type
        :type input: Dict[str, Any]
        
        :return: Generated structured response
        :rtype: Any
        
        :raises NotImplementedError: This feature is not yet supported
        
        .. warning::
        
            This method is not yet implemented for GeminiProvider.
        
        """
        raise NotImplementedError("GeminiProvider does not support structured response generation yet.")

    @override
    def generate_json(
        self,
        input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate a JSON response based on the provided input.
        
        **Description:**
        
            This method generates a JSON response from the Gemini model based on the
            user prompt and a specified JSON schema. It ensures that the output adheres
            to the provided schema.

        :param input: Input dictionary containing user prompt and schema with keys:
                      - "system_prompt" (str): System prompt for the model (optional)
                      - "user_prompt" (str): Prompt for JSON generation
                      - "schema" (dict): JSON schema to constrain the output
        :type input: Dict[str, Any]
        
        :return: The generated JSON response from the model
        :rtype: Dict[str, Any]
        
        :raises ValueError: If JSON parsing fails
        
        **Example:**
        
        .. code-block:: python
        
            from orca.modules.providers.gemini.GeminiProvider import GeminiProvider
            
            # Initialize Gemini Provider
            gemini_provider = GeminiProvider(
                api_key="YOUR_GEMINI_API_KEY",
                model_name="models/gemini-2.5-pro",
                config={"temperature": 0.7}
            )
            
            # Generate JSON response
            response = gemini_provider.generate_json({
                "user_prompt": "Provide a summary of the latest AI advancements.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "date": {"type": "string", "format": "date"}
                    },
                    "required": ["summary", "date"]
                }
            })
            
            print(response)
            # Output: {'summary': '...', 'date': '2024-06-01'}
        
        """
        system_prompt = input.get("system_prompt", '')
        user_prompt = input.get("user_prompt", '')
        contents = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": input.get("schema", '')
                }
            )
        except Exception as err:
            self.logger.warning("Error occurred while generating content:", err)
            raise err

        print(f"Raw response text: {response.text[:500]}")  # Log raw response for debugging

        # Update token usage
        try:
            self._update_token_usage(getattr(response, "usage_metadata", None))
        except Exception as err:
            self.logger.warning("Error occurred while updating token usage:", err)

        try:
            response = json.loads(response.text)
        except Exception:
            # If model adds extra text around JSON then strip it
            match = re.search(r"(\[.*\])", response.text, re.DOTALL)
            if match:
                response = json.loads(match.group(1))
            else:
                raise ValueError(f"Could not parse model response: {response.text[:500]}")

        return response

    @override
    def available_model_names(self) -> List[str]:
        """
        Get a list of available Gemini model names.
        
        Retrieves all available models from the Gemini API.
        
        :return: List of available model names
        :rtype: List[str]
        """
        model_iter = self.client.models.list(config={"page_size": 50})
        model_names = []
        
        for model in model_iter:
            model_names.append(model.name)

        return model_names
