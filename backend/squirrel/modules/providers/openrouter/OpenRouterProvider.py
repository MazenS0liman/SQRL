#!/usr/bin/python
"""
OpenRouter Provider Module
==========================

Overview:
---------

This module defines the OpenRouterProvider class, which integrates with the OpenRouter API
to provide language model services. OpenRouter exposes an OpenAI-compatible REST API,
so this provider uses the ``openai`` SDK with a custom ``base_url`` and passes the
API key via the ``Authorization`` header.

Features
--------

- Generate text responses using any OpenRouter-hosted model.
- Generate structured (Pydantic) responses via ``instructor``.
- Generate JSON responses constrained by a JSON Schema.
- Available model listing.
- Token usage tracking.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import re
import json
from typing import Dict, List, Any, Optional
from typing_extensions import override

# Third-Party Libraries
from openai import OpenAI

# Abstract Classes
from squirrel.modules.providers.abstract.IProvider import IProvider

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Constants

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ——————————————————————————————————————————————————————————————
# OpenRouter Provider Class
class OpenRouterProvider(IProvider):
    """
    OpenRouter Provider Class
    -------------------------

    **Description:**

        This class provides integration with the OpenRouter API to generate text responses,
        structured responses, JSON responses with schema enforcement, and token usage tracking.
        OpenRouter exposes an OpenAI-compatible interface, so this provider uses the ``openai``
        SDK pointed at ``https://openrouter.ai/api/v1``.

    **Attributes:**

        - **model_name (str):** The name of the OpenRouter model to use
          (e.g. ``"meta-llama/llama-3.3-70b-instruct"``).
        - **config (dict):** Configuration dictionary (``temperature``, ``max_tokens``, etc.).
        - **api_env_var (str):** Name of the environment variable that holds the API key.
        - **api_key (str):** Resolved API key value.
        - **client (OpenAI):** The ``openai`` SDK client pointed at OpenRouter.

    **Methods:**

        - :meth:`generate_response`
            Generate a plain-text response using an OpenRouter model.
        - :meth:`generate_structured_response`
            Generate a validated Pydantic model instance via ``instructor``.
        - :meth:`generate_json`
            Generate a JSON response constrained by a JSON Schema.
        - :meth:`available_model_names`
            Return a list of model IDs available through OpenRouter.

    """

    def __init__(
        self,
        api_env_var: str,
        model_name: str,
        config: dict,
        site_url: Optional[str] = None,
        site_name: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        """
        Initialise the OpenRouterProvider.

        :param api_env_var: Environment variable name that holds the OpenRouter API key.
        :type api_env_var: str

        :param model_name: OpenRouter model identifier
            (e.g. ``"meta-llama/llama-3.3-70b-instruct"``).
        :type model_name: str

        :param config: Provider configuration dict.  Recognised keys:

            - ``temperature`` (float, default 0.7)
            - ``max_tokens``  (int,   default 2048)
        :type config: dict

        :param site_url: Optional URL sent in the ``HTTP-Referer`` header for
            OpenRouter rankings / analytics.
        :type site_url: str, optional

        :param site_name: Optional name sent in the ``X-Title`` header.
        :type site_name: str, optional
        """
        super().__init__(model_name=model_name, config=config)
        self.model_name = model_name
        self.config = config
        self.api_env_var = api_env_var
        self.api_key = api_key or (os.getenv(self.api_env_var, "") if self.api_env_var else "")

        # Optional headers used by OpenRouter for attribution / rate-limit tiers.
        extra_headers: Dict[str, str] = {}
        if site_url:
            extra_headers["HTTP-Referer"] = site_url
        if site_name:
            extra_headers["X-Title"] = site_name

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=OPENROUTER_BASE_URL,
            default_headers=extra_headers if extra_headers else None,
        )

    # ── Token tracking ────────────────────────────────────────────────────────

    @override
    def _update_token_usage(self, usage: Any) -> None:
        """
        Update token usage counters from API response usage object.

        **Description:**

            Updates the cumulative and per-request token counters based on the
            ``usage`` object returned by OpenRouter (which follows the OpenAI schema).

        :param usage: Usage object from the API response (``response.usage``).
        :type usage: Any

        :return: None
        :rtype: None
        """
        if usage is None:
            return

        input_tokens  = getattr(usage, "prompt_tokens",     0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens  = getattr(usage, "total_tokens",      0) or 0

        # Cumulative counters
        self._total_input_tokens  += input_tokens
        self._total_output_tokens += output_tokens
        self._total_tokens        += total_tokens
        self._request_count       += 1

        # Per-request snapshot
        self._last_input_tokens   = input_tokens
        self._last_output_tokens  = output_tokens
        self._last_request_tokens = total_tokens

        logger.info(
            "OpenRouter request #{} | Input: {} | Output: {} | Total: {} | Cumulative: {}",
            self._request_count,
            input_tokens,
            output_tokens,
            total_tokens,
            self._total_tokens,
        )

    # ── Core generation methods ───────────────────────────────────────────────

    @override
    def generate_response(self, input: Dict[str, Any]) -> str:
        """
        Generate a plain-text response.

        :param input: Input dictionary with keys:

            - ``"system_prompt"`` (str, optional): System-level instructions.
            - ``"user_prompt"``   (str): The user message.
        :type input: Dict[str, Any]

        :return: The generated text response.
        :rtype: str

        :raises Exception: Propagates any API-level exception after logging.
        """
        system_prompt = input.get("system_prompt", "")
        user_prompt   = input.get("user_prompt",   "")

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=self.config.get("temperature", 0.7),
                max_tokens=self.config.get("max_tokens", 2048),
            )
        except Exception as err:
            logger.warning("Error occurred while generating OpenRouter response: {}", err)
            raise

        self._update_token_usage(getattr(response, "usage", None))
        return response.choices[0].message.content.strip()

    @override
    def generate_structured_response(self, input: Dict[str, Any]) -> Any:
        """
        Generate a validated Pydantic model instance via ``instructor``.

        :param input: Input dictionary with keys:

            - ``"system_prompt"``  (str, optional): System-level instructions.
            - ``"user_prompt"``    (str): The user message.
            - ``"response_model"`` (Type[BaseModel]): Pydantic model class.
        :type input: Dict[str, Any]

        :return: Validated Pydantic model instance.
        :rtype: Any

        :raises Exception: Propagates any API-level exception after logging.
        """
        import instructor

        patched_client = instructor.from_openai(
            OpenAI(
                api_key=self.api_key,
                base_url=OPENROUTER_BASE_URL,
            )
        )

        system_prompt  = input.get("system_prompt",  "")
        user_prompt    = input.get("user_prompt",    "")
        response_model = input.get("response_model")

        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        try:
            response = patched_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                response_model=response_model,
                temperature=self.config.get("temperature", 0.7),
                max_tokens=self.config.get("max_tokens", 2048),
            )
        except Exception as err:
            logger.warning(
                "Error occurred while generating structured OpenRouter response: {}", err
            )
            raise

        # ``instructor`` responses may not carry standard usage; update if present.
        self._update_token_usage(getattr(response, "usage", None))
        return response

    @override
    def generate_json(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a JSON response constrained by a provided JSON Schema.

        **Description:**

            Embeds the schema in the system prompt and enables JSON mode so the
            model returns a syntactically valid JSON object.  Falls back to regex
            extraction if the model wraps the output in markdown fences.

        :param input: Input dictionary with keys:

            - ``"system_prompt"`` (str, optional): Additional system instructions.
            - ``"user_prompt"``   (str): The user message / task description.
            - ``"schema"``        (dict): JSON Schema the response must conform to.
        :type input: Dict[str, Any]

        :return: Parsed JSON response as a Python dict.
        :rtype: Dict[str, Any]

        :raises ValueError: If the model response cannot be parsed as valid JSON.
        :raises Exception: Propagates any API-level exception after logging.
        """
        system_prompt = input.get("system_prompt", "")
        user_prompt   = input.get("user_prompt",   "")
        schema        = input.get("schema",        {})

        schema_instruction = (
            "You must respond with a single JSON object that strictly conforms "
            "to the following JSON Schema. Do not include any explanation, markdown "
            "fences, or text outside the JSON object.\n\n"
            f"Schema:\n{json.dumps(schema, indent=2)}"
        )
        full_system = (
            f"{system_prompt}\n\n{schema_instruction}" if system_prompt else schema_instruction
        )

        messages = [
            {"role": "system", "content": full_system},
            {"role": "user",   "content": user_prompt},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=self.config.get("temperature", 0.0),
                max_tokens=self.config.get("max_tokens", 4096),
                response_format={"type": "json_object"},
            )
        except Exception as err:
            logger.warning(
                "Error occurred while generating OpenRouter JSON response: {}", err
            )
            raise

        raw_text = response.choices[0].message.content or ""
        logger.info("Raw OpenRouter JSON response: {}", raw_text[:500])

        self._update_token_usage(getattr(response, "usage", None))

        # Parse — strip markdown fences if the model adds them despite instructions.
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            fence_match = re.search(
                r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw_text, re.DOTALL
            )
            if fence_match:
                return json.loads(fence_match.group(1))
            bare_match = re.search(r"(\{.*\}|\[.*\])", raw_text, re.DOTALL)
            if bare_match:
                return json.loads(bare_match.group(1))
            raise ValueError(
                f"Could not parse OpenRouter JSON response: {raw_text[:500]}"
            )

    # ── Model discovery ───────────────────────────────────────────────────────

    @override
    def available_model_names(self) -> List[str]:
        """
        Return a list of model IDs available through OpenRouter.

        **Description:**

            Calls the ``/models`` endpoint (exposed by the ``openai`` SDK as
            ``client.models.list()``) and returns the ``id`` field of each entry.

        :return: List of model identifier strings.
        :rtype: List[str]

        :raises Exception: Propagates any API-level exception after logging.
        """
        try:
            models = self.client.models.list()
            return [model.id for model in models.data]
        except Exception as err:
            logger.warning("Could not fetch OpenRouter model list: {}", err)
            raise