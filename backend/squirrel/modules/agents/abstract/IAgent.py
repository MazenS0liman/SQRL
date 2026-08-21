#!/usr/bin/python
"""
Abstract Agent Module
=====================

Overview
--------

This module defines the abstract base class IAgent for agents that interact with Large Language Models (LLMs).

It provides a unified interface for communicating with various LLM providers,
handling retries, provider selection, and request management.

Features
--------

- Automatic retries with exponential backoff
- Global request counting for monitoring usage
- Support for multiple LLM providers (OpenAI, Gemini, Groq, OpenRouter)
- Per-user credential resolution via TokenService, with fallback to shared env-var keys

"""
# ——————————————————————————————————————————————————————————————
# Standard Libraries
import os
import ssl
import time
from abc import ABC
from typing import Dict, List, Any, Optional
from requests.exceptions import SSLError
from fastapi import WebSocket

# Provider Classes
from squirrel.modules.providers import (
    IProvider, Provider,
    OpenRouterProvider,
    GeminiProvider,
    GroqProvider
)

# Prompt Generator Interface
from squirrel.modules.prompts.abstract.IPromptGenerator import IPromptGenerator

# Token Service (per-user provider credentials)
# NOTE: adjust this import path to wherever token_service actually lives in your project.
from squirrel.services.auth.TokenService import token_service

# Logging
from squirrel.core.logging import Loggable
from loguru import logger

# ——————————————————————————————————————————————————————————————
# IAgent Class
class IAgent(ABC, Loggable):
    """
    Abstract Agent Class
    --------------------

    **Description:**

        This class provides a unified interface for communicating with various LLM providers
        (OpenAI, Google Gemini, Groq, OpenRouter) through a consistent API. It handles provider
        selection, request management, error handling, and response processing for AI-powered
        operations.

        When a ``user_id`` is set on the agent (either at construction time or per-call via
        :meth:`get_response`), the agent will first try to use that user's own stored provider
        token (via :class:`TokenService`) before falling back to the shared, environment-variable
        based API keys. This lets a single agent instance make calls on behalf of many different
        users, each billed/rate-limited against their own credentials.

    **Attributes:**

        - :py:data:`DEFAULT_PROVIDERS`
            Default list of providers in fallback order [GEMINI, OPENAI, GROQ, OPENROUTER]

    **Key Features:**

        - Support for multiple LLM providers (OpenAI, Gemini, Groq, OpenRouter)
        - Automatic provider fallback when primary provider fails
        - Retry logic with exponential backoff for resilience
        - Global request counting for usage monitoring and quotas
        - Integration with prompt generation strategies
        - Flexible model selection and configuration
        - SSL error handling and recovery
        - Per-user credential resolution via TokenService

    **Core Methods**

        - :meth:`get_response`
            Get a response from the LLM with customizable providers and models

    **Supported Providers**

        - **OpenAI**: GPT-5, GPT-4.1, GPT-4o
        - **Google Gemini**: Gemini 2.5 Flash, Gemini 2.5 Pro

    **Example**

    .. code-block:: python

        from squirrel.modules.agents.abstract.IAgent import IAgent
        from squirrel.modules.providers.abstract.IProvider import Provider
        from squirrel.modules.prompts.abstract.IPromptGenerator import IPromptGenerator

        class CustomPromptGenerator(IPromptGenerator):
            @override
            def generate_system_prompt(self, input_data: Dict) -> str:
                return "Custom system prompt based on input data"

            @override
            def generate_user_prompt(self, input_data: Dict) -> str:
                return "Custom user prompt based on input data"

        class CustomAgent(IAgent):
            def __init__(
                self,
                api_env_var: str,
                provider: Provider,
                model_name: str,
                prompt_generator: IPromptGenerator,
                config: Dict = None,
                user_id: str = None,
            ) -> None:
                super().__init__(
                    api_env_var="GEMINI_API_KEY_V1",
                    provider=provider,
                    model_name=model_name,
                    prompt_generator=prompt_generator,
                    config=config if config is not None else {"temperature": 0.7, "max_tokens": 1000},
                    user_id=user_id,
                )

        agent = CustomAgent(
            api_env_var="GEMINI_API_KEY_V1",
            provider=Provider.GEMINI,
            model_name="models/gemini-2.5-pro",
            prompt_generator=CustomPromptGenerator(),
            config={"temperature": 0.7, "max_tokens": 1000},
            user_id="user_123",
        )

    """
    DEFAULT_PROVIDERS = [Provider.GEMINI, Provider.OPENAI, Provider.GROQ, Provider.OPENROUTER]

    def __init__(
        self,
        api_env_var: Optional[str] = "GEMINI_API_KEY_V1",
        provider: Optional[Provider] = Provider.GEMINI,
        model_name: Optional[str] = "models/gemini-2.5-pro",
        prompt_generator: IPromptGenerator = None,
        config: Dict = None,
        socket: Optional[WebSocket] = None,
        user_id: Optional[str] = None
    ) -> None:
        """
        Initialize the IAgent instance.

        **Description:**

            This constructor sets up the agent with the specified API key, provider,
            model name, prompt generator, and configuration parameters. If ``user_id``
            is provided, the agent will attempt to resolve that user's stored provider
            token (via TokenService) before falling back to ``api_env_var``.

        :param api_env_var: Environment variable name for the API key (default: "GEMINI_API_KEY_V1")
        :type api_env_var: str, optional

        :param provider: Primary LLM provider to use (default: Provider.GEMINI)
        :type provider: Provider, optional

        :param model_name: Name/ID of the model to use (default: "models/gemini-2.5-pro")
        :type model_name: str, optional

        :param prompt_generator: Prompt generation strategy for the agent
        :type prompt_generator: IPromptGenerator

        :param config: Configuration parameters (e.g., temperature, max_tokens)
        :type config: dict, optional

        :param socket: Optional WebSocket for streaming updates
        :type socket: WebSocket, optional

        :param user_id: The ID of the user this agent acts on behalf of. When set,
            provider calls prefer this user's stored token over shared env-var keys.
        :type user_id: str, optional

        :return: None
        :rtype: None

        """
        super().__init__()
        self._api_env_var = api_env_var
        self._provider: Provider = provider
        self._model_name: str = model_name
        self._prompt_generator: IPromptGenerator = prompt_generator
        self._config: Dict = config if config is not None else {}
        self._user_id: Optional[str] = user_id
        logger.info(f"Initializing IAgent with provider={self._provider}, model_name={self._model_name}, user_id={self._user_id}")

        resolved_api_key = self._resolve_api_key(self.provider) if self._user_id else None
        logger.info(f"Resolved API key for user_id={self._user_id}: {'[REDACTED]' if resolved_api_key else 'None'}")
        self._model: IProvider = (
            self._instantiate_model(
                self._api_env_var, self.provider, self.model_name, self.config,
                api_key=resolved_api_key,
            )
            if (self._api_env_var or resolved_api_key) else None
        )
        self._socket: Optional[WebSocket] = socket

        # Usage tracking
        self._total_input_tokens_used: int = 0
        self._total_output_tokens_used: int = 0
        self._total_tokens_used: int = 0
        self._total_request_count: int = 0

    @property
    def api_env_var(self) -> str:
        """Environment variable name for the API key."""
        return self._api_env_var

    @property
    def provider(self) -> Provider:
        """Provider name."""
        return self._provider

    @property
    def model_name(self) -> str:
        """Model name."""
        return self._model_name

    @property
    def prompt_generator(self) -> IPromptGenerator:
        """Prompt generator instance."""
        return self._prompt_generator

    @property
    def config(self) -> Dict:
        """Configuration dictionary."""
        return self._config

    @property
    def model(self) -> IProvider:
        """Provider instance."""
        return self._model

    @property
    def socket(self) -> Optional[WebSocket]:
        """WebSocket instance."""
        return self._socket

    @property
    def user_id(self) -> Optional[str]:
        """The user this agent makes provider calls on behalf of, if any."""
        return self._user_id

    @property
    def total_request_count(self) -> int:
        """Total number of requests made across all providers."""
        return self._total_request_count

    @property
    def total_input_tokens_used(self) -> int:
        """Total number of input tokens used across all providers."""
        return self._total_input_tokens_used

    @property
    def total_output_tokens_used(self) -> int:
        """Total number of output tokens used across all providers."""
        return self._total_output_tokens_used

    @property
    def total_tokens_used(self) -> int:
        """Total number of tokens used across all providers."""
        return self._total_tokens_used

    @api_env_var.setter
    def api_env_var(
        self,
        value: str
    ) -> None:
        """
        Set a new environment variable name for the API key.

        :param value: The new environment variable name
        :type value: str

        :return: None
        """
        self._api_env_var = value
        self._model = self._instantiate_model(self._api_env_var, self.provider, self.model_name, self.config)

    @provider.setter
    def provider(
        self,
        value: Provider
    ) -> None:
        """
        Set the provider.

        :param value: The provider to set
        :type value: Provider

        :return: None
        """
        self._provider = value

    @model_name.setter
    def model_name(
        self,
        value: str
    ) -> None:
        """
        Set the model name.

        :param value: The model name to set
        :type value: str

        :return: None
        """
        self._model_name = value

    @prompt_generator.setter
    def prompt_generator(
        self,
        value: IPromptGenerator
    ) -> None:
        """
        Set the prompt generator.

        :param value: The prompt generator to set
        :type value: IPromptGenerator

        :return: None
        """
        self._prompt_generator = value

    @config.setter
    def config(
        self,
        value: Dict[str, Any]
    ) -> None:
        """
        Set the configuration dictionary.

        :param value: The configuration dictionary to set
        :type value: dict[str, Any]

        :return: None
        """
        self._config = value

    @model.setter
    def model(
        self,
        value: IProvider
    ) -> None:
        """
        Set the provider instance.

        :param value: The provider instance to set
        :type value: IProvider

        :return: None
        """
        self._model = value

    @socket.setter
    def socket(
        self,
        value: Optional[WebSocket]
    ) -> None:
        """
        Set the WebSocket instance.

        :param value: The WebSocket instance to set
        :type value: Optional[WebSocket]

        :return: None
        """
        self._socket = value

    @user_id.setter
    def user_id(
        self,
        value: Optional[str]
    ) -> None:
        """
        Set the user this agent acts on behalf of.

        :param value: The user ID, or None to clear it
        :type value: Optional[str]

        :return: None
        """
        self._user_id = value

    @total_request_count.setter
    def total_request_count(
        self,
        value: int
    ) -> None:
        """
        Set the total number of requests made across all providers.

        :param value: The total number of requests
        :type value: int

        :return: None
        """
        self._total_request_count = value

    @total_input_tokens_used.setter
    def total_input_tokens_used(
        self,
        value: int
    ) -> None:
        """
        Set the total number of input tokens used across all providers.

        :param value: The total number of input tokens
        :type value: int

        :return: None
        """
        self._total_input_tokens_used = value

    @total_output_tokens_used.setter
    def total_output_tokens_used(
        self,
        value: int
    ) -> None:
        """
        Set the total number of output tokens used across all providers.

        :param value: The total number of output tokens
        :type value: int

        :return: None
        """
        self._total_output_tokens_used = value

    @total_tokens_used.setter
    def total_tokens_used(
        self,
        value: int
    ) -> None:
        """
        Set the total number of tokens used across all providers.

        :param value: The total number of tokens used
        :type value: int

        :return: None
        """
        self._total_tokens_used = value

    def _resolve_api_key(
        self,
        provider: Provider,
        user_id: Optional[str] = None,
        label: Optional[str] = None
    ) -> Optional[str]:
        """
        Look up a stored provider token for a specific user via TokenService.

        **Description:**

            Resolves ``user_id`` (falling back to ``self._user_id`` if not given) and,
            if present, asks TokenService for that user's decrypted token for the given
            provider. Returns None if there's no user in scope, or if the user has no
            token stored for that provider — callers should treat None as "fall back to
            shared/env-var credentials".

        :param provider: The provider to resolve a token for
        :type provider: Provider

        :param user_id: The user to resolve a token for. Falls back to self._user_id.
        :type user_id: str, optional

        :param label: Optional label, for users who store multiple tokens per provider
        :type label: str, optional

        :return: The decrypted token, or None if unavailable
        :rtype: str or None
        """
        uid = user_id or self._user_id
        if not uid:
            return None
        try:
            return token_service.get_decrypted_token(uid, provider.value, label)
        except Exception as e:
            self.logger.warning(
                f"Could not resolve stored token for user_id={uid}, provider={provider}: {e}"
            )
            return None

    def _get_key_sources(
        self,
        provider: Provider,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Optional[str]]]:
        """
        Build the ordered list of credential sources to try for a provider.

        **Description:**

            The requesting user's own stored token (if any) is tried first, followed by
            the shared environment-variable keys discovered via
            :meth:`_get_provider_api_env_vars`. Each source is a dict of the form
            ``{"api_env_var": Optional[str], "api_key": Optional[str]}`` and is consumed
            by :meth:`_build_provider` / :meth:`_instantiate_model`.

        :param provider: The provider to resolve credential sources for
        :type provider: Provider

        :param user_id: The user to resolve a personal token for. Falls back to self._user_id.
        :type user_id: str, optional

        :return: Ordered list of credential sources
        :rtype: list[dict[str, Optional[str]]]
        """
        sources: List[Dict[str, Optional[str]]] = []

        user_key = self._resolve_api_key(provider, user_id=user_id)
        if user_key:
            sources.append({"api_env_var": None, "api_key": user_key})

        for env_var in self._get_provider_api_env_vars(provider):
            sources.append({"api_env_var": env_var, "api_key": None})

        return sources

    def _build_provider(
        self,
        provider: Provider,
        source: Dict[str, Optional[str]],
        model_name: str
    ) -> IProvider:
        """
        Instantiate a provider from a resolved credential source.

        :param provider: The provider to instantiate
        :type provider: Provider

        :param source: A credential source as returned by :meth:`_get_key_sources`
        :type source: dict[str, Optional[str]]

        :param model_name: The model name to instantiate the provider with
        :type model_name: str

        :return: An instance of the provider
        :rtype: IProvider
        """
        return self._instantiate_model(
            source.get("api_env_var"), provider, model_name, self.config,
            api_key=source.get("api_key"),
        )

    def _instantiate_model(
        self,
        api_env_var: str,
        provider: Provider = Provider.GEMINI,
        model_name: str = "gemini-2.0-flash",
        config = None,
        api_key: Optional[str] = None
    ) -> IProvider:
        """
        Instantiate the model based on the provider name.

        **Description:**

            This method creates an instance of the provider class corresponding
            to the specified provider name, using the provided API key/env var, model name,
            and configuration. If ``api_key`` is given, it takes precedence over
            ``api_env_var`` inside the provider (e.g. a user's stored token).

        :param api_env_var: The environment variable name for the API key
        :type api_env_var: str

        :param provider: The name of the provider
        :type provider: Provider

        :param model_name: The name of the model
        :type model_name: str

        :param config: Additional configuration for the model
        :type config: dict

        :param api_key: An explicit API key to use instead of resolving from api_env_var
            (e.g. a user's decrypted stored token)
        :type api_key: str, optional

        :return: An instance of the provider
        :rtype: IProvider

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent

            provider_instance = agent._instantiate_model(
                api_env_var="GEMINI_API_KEY_V1",
                provider=Provider.GEMINI,
                model_name="gemini-2.0-flash",
                config={"temperature": 0.7, "max_tokens": 1000}
            )

            # Use the provider instance

        """
        if config is None:
            config = {}

        if not api_env_var and not api_key:
            raise ValueError("Either api_env_var or api_key is required to instantiate the model.")

        if provider == Provider.GEMINI:
            return GeminiProvider(api_env_var, model_name, config, api_key=api_key)
        elif provider == Provider.GROQ:
            return GroqProvider(api_env_var, model_name, config, api_key=api_key)
        elif provider == Provider.OPENROUTER:
            return OpenRouterProvider(api_env_var, model_name, config, api_key=api_key)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _get_key_with_substring(
        self,
        substring: str
    ) -> List[str]:
        """
        Retrieve all environment variable keys that contain the specified substring.

        :param substring: The substring to search for in environment variable keys
        :type substring: str

        :return: A list of matching environment variable keys
        :rtype: list[str]

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent
            keys = agent._get_key_with_substring("GEMINI")

            print(keys)
            # Output: ['GEMINI_API_KEY_V1', 'GEMINI_API_KEY_V2']

        """
        matching_keys = []
        for key in os.environ:
            if substring in key:
                matching_keys.append(key)
        return matching_keys

    def _get_provider_type(
        self,
        provider: Provider,
        user_id: Optional[str] = None
    ) -> IProvider:
        """
        Get the provider class based on the provider name.

        **Description:**

            This method returns an instance of the provider class corresponding
            to the specified provider name. It first tries the requesting user's own
            stored token (if any), then falls back to environment variables that
            contain the provider's name as a substring.

        :param provider: The name of the provider
        :type provider: Provider

        :param user_id: The user to resolve a personal token for. Falls back to self._user_id.
        :type user_id: str, optional

        :return: An instance of the provider class
        :rtype: IProvider

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent
            provider_instance = agent._get_provider_type(Provider.GEMINI)

            # Use the provider instance

        """
        sources = self._get_key_sources(provider, user_id=user_id)
        if not sources:
            raise ValueError(f"No API key found for provider: {provider}")
        return self._build_provider(provider, sources[0], model_name="")

    def _get_provider_api_env_vars(
        self,
        provider: Provider
    ) -> List[str]:
        """
        Get the API environment variables for the given provider name.

        **Description:**

            This method retrieves all API keys associated with the specified provider
            by searching environment variables that contain the provider's name as a substring.

        :param provider: The name of the provider
        :type provider: Provider

        :return: A list of API keys for the provider
        :rtype: list[str]

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent
            api_env_vars = agent._get_provider_api_env_vars(Provider.GEMINI)

            print(api_env_vars)
            # Output: ['GEMINI_API_KEY_V1', 'GEMINI_API_KEY_V2']

        """
        if provider == Provider.GEMINI:
            return self._get_key_with_substring("GEMINI")
        elif provider == Provider.GROQ:
            return self._get_key_with_substring("GROQ")
        elif provider == Provider.OPENROUTER:
            return self._get_key_with_substring("OPENROUTER")
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _call_available_model(
        self,
        provider: Provider,
        fn_name: str,
        input: Dict,
        retry_attempt: int = 3,
        available_models: List[str] = [],
        base_delay: float = 0.5,
        user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Call a function on the provider model with retries and exponential backoff.

        **Description:**

            This method attempts to call a specified function (`fn_name`) on the model
            for the given provider. It iterates through all available credential sources
            (the requesting user's own stored token first, then shared env-var keys) and
            model names, retrying the call up to `retry_attempt` times with exponential backoff.

        :param provider: The provider to use (e.g., Gemini)
        :type provider: Provider

        :param fn_name: The function name to call on the provider model
        :type fn_name: str

        :param input: The input data passed to the function
        :type input: dict

        :param retry_attempt: Number of retry attempts per model
        :type retry_attempt: int

        :param available_models: List of model names to attempt
        :type available_models: list[str]

        :param base_delay: Base delay (in seconds) for exponential backoff
        :type base_delay: float

        :param user_id: The user to resolve a personal token for. Falls back to self._user_id.
        :type user_id: str, optional

        :return: The successful model output, or None if all attempts fail
        :rtype: str or None

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent

            response = agent._call_available_model(
                fn_name="generate_text",
                provider=Provider.GEMINI,
                input={"prompt": "Hello, world!"},
                retry_attempt=3,
                available_models=["gemini-2.5-pro", "gemini-2.5-flash"],
                base_delay=1.0
            )

            print(response)
            # Output: Generated text from the model

        """
        provider_type: IProvider = self._get_provider_type(provider, user_id=user_id)
        available_model_list: List[str] = (
            available_models
            if available_models
            else provider_type.available_model_names()
        )

        sources = self._get_key_sources(provider, user_id=user_id)

        for _source in sources:
            for model_name in available_model_list:
                try:
                    # Delegate to _call_exact_model for actual model call + retry handling
                    output = self._call_exact_model(
                        provider=provider,
                        model_name=model_name,
                        fn_name=fn_name,
                        model_input=input,
                        retry_attempt=retry_attempt,
                        base_delay=base_delay,
                        user_id=user_id
                    )
                    if output is not None:
                        return output

                except Exception as e:
                    self.logger.warning(f"Failed with model '{model_name}' under provider '{provider}': {e}")
                    continue

        self.logger.info(f"All models for provider '{provider}' failed after exhausting retries and API keys.")
        return None

    def _call_exact_model(
        self,
        provider: Provider,
        model_name: str,
        fn_name: str,
        model_input: Dict,
        retry_attempt: int = 3,
        base_delay: float = 5.0,
        user_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Call a specific model function with automatic retries and exponential backoff.

        **Description:**

            This method attempts to invoke a specified function (`fn_name`) on the
            instantiated model corresponding to the given provider and model name.
            Credentials are resolved via :meth:`_get_key_sources`: the requesting user's
            own stored token is tried first (if a user is in scope), then the shared
            env-var keys. If an exception occurs, it retries the operation up to
            `retry_attempt` times per credential source, with delays between retries
            increasing exponentially by `base_delay * 2^(attempt - 1)`.

        :param provider: The name of the provider (e.g., 'Gemini')
        :type provider: Provider

        :param model_name: The exact model name to be used for the request
        :type model_name: str

        :param fn_name: The name of the method to call on the instantiated model
        :type fn_name: str

        :param model_input: The input data passed to the model function
        :type model_input: dict

        :param retry_attempt: Number of retry attempts per model (default: 3)
        :type retry_attempt: int

        :param base_delay: Base delay (in seconds) for exponential backoff (default: 0.5)
        :type base_delay: float

        :param user_id: The user to resolve a personal token for. Falls back to self._user_id.
        :type user_id: str, optional

        :return: The output returned by the model function, or None if all retries fail
        :rtype: str or None

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent
            response = agent._call_exact_model(
                provider=Provider.GEMINI,
                model_name="gemini-2.5-pro",
                fn_name="generate_text",
                model_input={"prompt": "Hello, world!"},
                retry_attempt=3,
                base_delay=1.0
            )

            print(response)
            # Output: Generated text from the model

        .. note::

            For each successful call, the method updates the agent's total token usage
            and request count based on the model's reported usage statistics.

        """
        sources = self._get_key_sources(provider, user_id=user_id)

        for source in sources:
            for attempt in range(1, retry_attempt + 1):
                try:
                    self._api_env_var = source.get("api_env_var")
                    self.model_name = model_name
                    self.provider = provider
                    self.model = self._build_provider(provider, source, model_name)
                    if not hasattr(self.model, fn_name):
                        raise ValueError(f"Function {fn_name} not found in model {self.model}")

                    # Execute request
                    fn = getattr(self.model, fn_name)
                    output = fn(model_input)

                    # Update total tokens used
                    usage: Dict[str, Any] = self.model.get_total_usage()
                    self.total_input_tokens_used += usage.get("input_tokens", 0)
                    self.total_output_tokens_used += usage.get("output_tokens", 0)
                    self.total_tokens_used += usage.get("total_tokens", 0)
                    self.total_request_count += usage.get("request_count", 0)

                    # Check if the model returned an API error (like 429)
                    if isinstance(output, dict) and "error" in output:
                        error_code = output["error"].get("code")
                        if error_code == 429:
                            raise RuntimeError(f"Rate limit exceeded: {output['error'].get('message')}")
                        else:
                            raise RuntimeError(f"API error: {output['error'].get('message')}")

                    self.logger.info(
                        f"Getting response using provider: {provider}, model: {model_name}, "
                        f"function: {fn_name}, user_id: {user_id or self._user_id}"
                    )
                    return output
                except (ssl.SSLError, SSLError) as e:
                    self.logger.info(f"[Attempt {attempt}/{retry_attempt}] Error calling model {model_name} from provider {provider}: {e}")
                    if attempt < retry_attempt:
                        delay = base_delay * (2 ** (attempt - 1))
                        self.logger.info(f"Retrying in {delay:.1f} seconds...")
                        time.sleep(delay)
                        continue
                    else:
                        raise e
                except Exception as e:
                    self.logger.info(f"[Attempt {attempt}/{retry_attempt}] Error calling model {model_name} from provider {provider}: {e}")
                    if attempt < retry_attempt:
                        delay = base_delay * (2 ** (attempt - 1))
                        self.logger.info(f"Retrying in {delay:.1f} seconds...")
                        time.sleep(delay)
                        continue
                    else:
                        raise e
        return None

    def get_response(
        self,
        fn_name: str,
        model_input: Dict,
        retry_attempt: int = 3,
        strict: bool = True,
        provider_order: List[Provider] = None,
        preference_model_names = None,
        user_id: Optional[str] = None
    ) -> Any:
        """
        Execute a function on the model with retries and provider/model selection.

        **Description:**

            This method attempts to call a specified function (`fn_name`) on the model
            using the provided input (`model_input`). It supports strict mode, where only
            the current model is used, or a more flexible mode that tries multiple
            providers and models based on preferences.

            Credential resolution always prefers a per-request user's own stored provider
            token over the shared, environment-variable based keys. Pass `user_id` to scope
            a single call to a specific user without changing the agent's default
            `self.user_id`; if omitted, `self.user_id` is used instead (if set).

        :param fn_name: The name of the function to call on the model
        :type fn_name: str

        :param model_input: The input to pass to the model
        :type model_input: dict

        :param retry_attempt: The number of retry attempts (default: 3)
        :type retry_attempt: int

        :param strict: Whether to strictly use the current model or try others (default: True)
        :type strict: bool

        :param provider_order: The order of providers to try
        :type provider_order: list[Provider]

        :param preference_model_names: Preferred model names to try first
        :type preference_model_names: list[str]

        :param user_id: The user to make this call on behalf of. Overrides self.user_id
            for this call only. Falls back to self.user_id if omitted.
        :type user_id: str, optional

        :return: The response from the model function
        :rtype: Any

        **Example:**

        .. code-block:: python

            from squirrel.modules.agents.abstract.IAgent import IAgent

            # Assume `agent` is an instance of a class derived from IAgent
            response = agent.get_response(
                fn_name="generate_text",
                model_input={"prompt": "Hello, world!"},
                retry_attempt=3,
                strict=False,
                provider_order=[Provider.GEMINI],
                preference_model_names=["gemini-2.5-pro"],
                user_id="user_123"
            )
            print(response)
            # Output: Generated text from the model

        """
        # If strict is True and no provider or model preferences are given, use the current model.
        if preference_model_names is None:
            preference_model_names = []
        if provider_order is None:
            provider_order = []
        if strict or (provider_order is None or len(provider_order) == 0) and (preference_model_names is None or len(preference_model_names) == 0):
            if self.model_name is None or self.provider is None:
                raise ValueError("Model name and provider must be set in strict mode.")

            for _ in range(retry_attempt):
                try:
                    response = self._call_exact_model(
                        self.provider, self.model_name, fn_name, model_input, retry_attempt,
                        user_id=user_id
                    )
                    if response is not None:
                        return response
                    else:
                        self.logger.info(f"Strict mode: Model {self.model_name} from provider {self.provider} returned None. Retrying...")
                        continue
                except Exception as e:
                    self.logger.warning(f"Strict mode: Model {self.model_name} from provider {self.provider} failed with error: {e}. Retrying...")
                    continue
            return None
        else:
            if provider_order is not None and len(provider_order) > 0:
                for provider in provider_order:
                    try:
                        if preference_model_names is not None and len(preference_model_names) > 0:
                            provider_type: IProvider = self._get_provider_type(provider, user_id=user_id)
                            available_models = provider_type.available_model_names()
                            for model_name in preference_model_names:
                                try:
                                    if model_name in available_models:
                                        self.logger.info(f"Trying preferred model {model_name} for provider {provider}")
                                        response = self._call_exact_model(
                                            provider, model_name, fn_name, model_input, retry_attempt,
                                            user_id=user_id
                                        )
                                        if response is not None:
                                            return response
                                        else:
                                            self.logger.info(f"Preferred model {model_name} for provider {provider} returned None. Trying next...")
                                            continue
                                    else:
                                        continue
                                except Exception as e:
                                    self.logger.warning(
                                        f"Provider {provider}, model {model_name} failed with error: {e}. Trying next..."
                                    )
                                    continue
                        else:
                            response = self._call_available_model(
                                provider, fn_name, model_input, retry_attempt, user_id=user_id
                            )
                            return response
                    except Exception as e:
                        self.logger.warning(
                            f"Provider {provider} failed with error: {e}. Trying next..."
                        )
                        continue
                return None
            else:
                for provider in provider_order or self.DEFAULT_PROVIDERS:
                    try:
                        provider_type: IProvider = self._get_provider_type(provider, user_id=user_id)
                        available_models = provider_type.available_model_names()

                        for model_name in (preference_model_names or available_models):
                            if model_name not in available_models:
                                continue

                            try:
                                response = self._call_exact_model(
                                    provider, model_name, fn_name, model_input, retry_attempt,
                                    user_id=user_id
                                )
                                if response is not None:
                                    return response
                                else:
                                    self.logger.info(f"Model {model_name} for provider {provider} returned None. Trying next...")
                                    continue
                            except Exception as e:
                                self.logger.warning(
                                    f"Provider {provider}, model {model_name} failed with error: {e}. Trying next..."
                                )
                                continue
                    except Exception as e:
                        self.logger.warning(
                            f"Provider {provider} failed with error: {e}. Trying next..."
                        )
                        continue
                return None