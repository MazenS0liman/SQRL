#!/usr/bin/python
"""
Groq Provider Module
====================

Overview:
---------

This module defines the GroqProvider class, which integrates with the Groq API to provide language model services.

Features
--------

- Generate text responses using Groq models.
- Generate structured responses.
- Generate JSON responses based on a schema.
- Available model listing.
- Token usage tracking.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import os
import re
import json
from typing import Dict, List, Any
from typing_extensions import override

# Third-Party Libraries
from groq import Groq

# Abstract Classes
from squirrel.modules.providers.abstract.IProvider import IProvider

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Groq Provider Class
class GroqProvider(IProvider):
    """
    Groq Provider Class
    -------------------
    
    **Description:**
    
        This class provides integration with the Groq API to generate text responses,
        structured responses, JSON responses with schema enforcement, and token usage tracking.

    **Attributes:**
        
        - **model_name (str):** The name of the Groq model to use.
        - **config (dict):** Configuration dictionary for the Groq provider.
        - **api_version (str):** The version of the API key to use.
        - **client (Groq):** The initialized Groq client.
        
    **Methods:**
        
        - :meth:`generate_response`
            Generate a response using Groq model
        - :meth:`generate_structured_response`
            Generate a structured response
        - :meth:`generate_json`
            Generate a JSON response constrained by a schema
        - :meth:`available_model_names`
            Get list of available Groq model names
    
    """
    def __init__(
        self,
        api_env_var: str,
        model_name: str,
        config: dict,
        api_key: str = None
    ) -> None:
        super().__init__(model_name=model_name, config=config)
        self.model_name = model_name
        self.config = config
        self.api_env_var = api_env_var
        self.api_key = api_key or (os.getenv(api_env_var, "") if api_env_var else "")
        self.client = Groq(api_key=self.api_key)

    @override
    def _update_token_usage(
        self,
        usage: Any
    ) -> None:
        """
        Update token usage counters from API response usage object.

        **Description:**

            This method updates the total prompt tokens, completion tokens, total tokens,
            and request count based on the usage object returned by Groq after each request.

        :param usage: Usage object from Groq response (response.usage)
        :type usage: Any

        :return: None
        :rtype: None
        """
        if usage is None:
            return

        input_tokens  = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens  = getattr(usage, "total_tokens", 0) or 0

        # Cumulative counters (mirrors GeminiProvider pattern)
        self._total_input_tokens  += input_tokens
        self._total_output_tokens += output_tokens
        self._total_tokens        += total_tokens
        self._request_count       += 1

        # Per-request snapshot
        self._last_input_tokens   = input_tokens
        self._last_output_tokens  = output_tokens
        self._last_request_tokens = total_tokens

        self.logger.info(
            f"Groq request #{self._request_count} | "
            f"Input Tokens: {input_tokens} | "
            f"Output Tokens: {output_tokens} | "
            f"Total Tokens: {total_tokens} | "
            f"Cumulative Total Tokens: {self._total_tokens}"
        )

    # ── Core generation methods ───────────────────────────────────────────────

    @override
    def generate_response(
        self,
        input: Dict
    ) -> str:
        """
        Generate a text response based on the input dictionary.
        
        :param input: Input dictionary with keys:
                      - "system_prompt" (str, optional): System-level instructions
                      - "user_prompt" (str): The user message
        :type input: Dict
        
        :return: The generated text response
        :rtype: str
        """
        system_prompt = input.get("system_prompt", "")
        user_prompt   = input.get("user_prompt", "")

        messages = []
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
            logger.warning("Error occurred while generating Groq response: %s", err)
            raise

        self._update_token_usage(getattr(response, "usage", None))
        return response.choices[0].message.content.strip()

    @override
    def generate_structured_response(
        self,
        input: Dict[str, Any]
    ) -> Any:
        """
        Generate a structured (Pydantic) response via instructor.

        :param input: Input dictionary with keys:
                      - "system_prompt" (str, optional): System-level instructions
                      - "user_prompt" (str): The user message
                      - "response_model" (Type[BaseModel]): Pydantic model class
        :type input: Dict[str, Any]

        :return: Validated Pydantic model instance
        :rtype: Any
        """
        import instructor
        from groq import Groq as _Groq

        patched_client = instructor.from_groq(_Groq(api_key=self.api_key))

        system_prompt  = input.get("system_prompt", "")
        user_prompt    = input.get("user_prompt", "")
        response_model = input.get("response_model")

        messages = []
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
            logger.warning("Error occurred while generating structured Groq response: %s", err)
            raise

        # instructor responses don't carry standard usage; update if available
        self._update_token_usage(getattr(response, "usage", None))
        return response

    @override
    def generate_json(
        self,
        input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate a JSON response constrained by a provided schema.

        **Description:**

            Sends the schema as a system-level instruction so Groq's JSON mode
            produces output that matches the expected structure. Falls back to
            regex extraction if the model wraps the JSON in markdown fences.

        :param input: Input dictionary with keys:
                      - "system_prompt" (str, optional): Additional system instructions
                      - "user_prompt" (str): The user message / task description
                      - "schema" (dict): JSON Schema the response must conform to
        :type input: Dict[str, Any]

        :return: Parsed JSON response as a Python dict
        :rtype: Dict[str, Any]

        :raises ValueError: If the model response cannot be parsed as valid JSON
        """
        system_prompt = input.get("system_prompt", "")
        user_prompt   = input.get("user_prompt", "")
        schema        = input.get("schema", {})

        # Embed the schema in the system prompt so Groq honours the structure.
        # Groq's JSON mode guarantees valid JSON but not schema conformance,
        # so explicit schema injection is the most reliable approach.
        schema_instruction = (
            "You must respond with a single JSON object that strictly conforms "
            "to the following JSON Schema. Do not include any explanation, markdown "
            "fences, or text outside the JSON object.\n\n"
            f"Schema:\n{json.dumps(schema, indent=2)}"
        )
        full_system = f"{system_prompt}\n\n{schema_instruction}" if system_prompt else schema_instruction

        messages = [
            {"role": "system", "content": full_system},
            {"role": "user",   "content": user_prompt},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=self.config.get("temperature", 0.0),  # low temp for deterministic JSON
                max_tokens=self.config.get("max_tokens", 4096),
                response_format={"type": "json_object"},  # Groq JSON mode
            )
        except Exception as err:
            logger.warning("Error occurred while generating Groq JSON response: %s", err)
            raise

        raw_text = response.choices[0].message.content or ""
        logger.info(f"Raw Groq JSON response: {raw_text[:500]}")

        self._update_token_usage(getattr(response, "usage", None))

        # Parse — strip markdown fences if the model adds them despite instructions
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            # Try stripping ```json ... ``` fences
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", raw_text, re.DOTALL)
            if fence_match:
                return json.loads(fence_match.group(1))
            # Last resort: find the first {...} or [...] block
            bare_match = re.search(r"(\{.*\}|\[.*\])", raw_text, re.DOTALL)
            if bare_match:
                return json.loads(bare_match.group(1))
            raise ValueError(f"Could not parse Groq JSON response: {raw_text[:500]}")

    @override
    def available_model_names(self) -> List[str]:
        """
        Get a list of available Groq model names.

        :return: List of available model names
        :rtype: List[str]
        """
        models = self.client.models.list()
        return [model.id for model in models.data]