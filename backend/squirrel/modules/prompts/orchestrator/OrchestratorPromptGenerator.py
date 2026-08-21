#!/usr/bin/python
"""
Orchestrator Prompt Generator Module
======================================

Overview
--------

This module provides prompt generation capabilities for the orchestrator
agent's routing task: classifying an incoming chat request into exactly one
of three execution routes:

``chat``
    General conversation — no specialised data pipeline applies.

``explore``
    Hypothesis-driven EDA — the user wants to understand, analyse, or query
    their data.

``build``
    Full ML pipeline — the user wants to predict, classify, or forecast a
    specific target variable, i.e. they want a trained model.

Prompts
-------

- :class:`OrchestratorPromptGenerator`
    Class for generating system and user prompts that drive the LLM-based
    ``chat`` vs ``explore`` vs ``build`` classification call made by
    :class:`~squirrel.modules.agents.OrchestratorAgent`.

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import json
from enum import Enum
from typing import Any, Dict, List, Optional
from typing_extensions import override

# Abstract Classes
from squirrel.modules.prompts.abstract.IPromptGenerator import IPromptGenerator

# ——————————————————————————————————————————————————————————————
# Orchestrator Prompt Type Enum


class OrchestratorPromptType(Enum):
    """
    Orchestrator Prompt Type Enum
    -----------------------------

    **Description:**

        Defines the types of prompts that :class:`OrchestratorPromptGenerator`
        can create. Each type corresponds to a specific orchestration task.

    **Enum Values:**

        - **CLASSIFY_ROUTE:** Classify a chat request as ``chat``, ``explore``,
          or ``build``.

    """
    CLASSIFY_ROUTE = "classify_route"
    """Classify a chat request as 'chat', 'explore', or 'build'."""


# ——————————————————————————————————————————————————————————————
# Orchestrator Prompt Generator Class


class OrchestratorPromptGenerator(IPromptGenerator):
    """
    Orchestrator Prompt Generator Class
    -----------------------------------

    **Description:**

        Implements the :class:`IPromptGenerator` interface and provides methods
        to generate system and user prompts for the orchestrator's single LLM
        routing call.

    **Methods:**

        - :meth:`generate_system_prompt`
            Create the system-level prompt that defines the classifier's role,
            the two routes, and the response contract.
        - :meth:`generate_user_prompt`
            Dispatch to the appropriate prompt builder based on ``prompt_type``.
        - :meth:`classify_route_prompt`
            Build the user-facing prompt containing the request to classify.

    """

    # ── System prompts ────────────────────────────────────────────────────────

    @override
    def generate_system_prompt(
        self,
        prompt_type: Optional[OrchestratorPromptType] = None,
    ) -> str:
        """
        Generate a system-level prompt.

        **Description:**

            Creates the system prompt that defines the classifier's role, the
            three available routes (``chat`` / ``explore`` / ``build``), and
            the rules for extracting a target column when the route is
            ``build``.

        :param prompt_type: The type of system prompt to generate. Currently
            only :attr:`OrchestratorPromptType.CLASSIFY_ROUTE` is supported;
            other values fall back to the same prompt since the orchestrator
            has a single responsibility.
        :type prompt_type: Optional[OrchestratorPromptType]

        :return: A system prompt string that describes the model's role,
            the routing rules, and the response contract.
        :rtype: str
        """
        return (
            "You are a routing classifier for a tabular-data analysis assistant. "
            "Given a user's request (plus any attached files and structured "
            "metadata), decide whether the request should be handled by exactly "
            "one of three pipelines:\n\n"
            "1. \"chat\" — the request is general conversation, a greeting, a "
            "clarifying question, or anything that does not require a "
            "specialised data-analysis or modelling pipeline. Use this as the "
            "default when neither \"explore\" nor \"build\" clearly applies.\n\n"
            "2. \"explore\" — the user wants to understand, analyse, query, or "
            "visualise their data: find patterns, trends, correlations, "
            "anomalies, segments, or ask open-ended analytical questions. No "
            "specific target variable to predict is implied by the request.\n\n"
            "3. \"build\" — the user wants to predict, classify, forecast, or "
            "model a specific outcome, e.g. \"predict churn\", \"classify these "
            "transactions as fraud or not\", \"build a model to forecast sales\". "
            "This implies training a machine-learning model against a target "
            "column.\n\n"
            "Rules:\n"
            "- Choose exactly one route: \"chat\", \"explore\", or \"build\".\n"
            "- If the route is \"build\", extract the target column name when it "
            "is explicitly mentioned or clearly inferable from the request "
            "(e.g. \"predict whether a customer churns\" implies a target column "
            "like 'churn'). If no target column can be confidently determined, "
            "set target_column to null — do not guess wildly.\n"
            "- If the route is \"chat\" or \"explore\", target_column must be null.\n"
            "- confidence is a float between 0 and 1 reflecting how certain you "
            "are in the chosen route.\n"
            "- reasoning is a single concise sentence explaining the decision.\n\n"
            "Respond only with the requested JSON object — no extra commentary, "
            "no markdown fences."
        )

    # ── User prompts ──────────────────────────────────────────────────────────

    @override
    def generate_user_prompt(
        self,
        prompt_type: Optional[OrchestratorPromptType] = None,
        **kwargs: Any,
    ) -> str:
        """
        Generates a user prompt based on the specified type.

        **Description:**

            Routes prompt generation to the appropriate specialised builder
            based on the ``prompt_type`` parameter.

        :param prompt_type: The type of user prompt to generate.
        :type prompt_type: Optional[OrchestratorPromptType]
        :param kwargs: Additional parameters forwarded to the builder. Expected
            keys for :attr:`OrchestratorPromptType.CLASSIFY_ROUTE`: ``query``,
            ``body``, ``file_urls``, ``metadata``.
        :type kwargs: dict

        :return: Generated user prompt string.
        :rtype: str

        :raises ValueError: If an invalid or missing prompt type is specified.
        """
        if prompt_type == OrchestratorPromptType.CLASSIFY_ROUTE:
            return self.classify_route_prompt(kwargs)
        raise ValueError("Invalid prompt type specified.")

    def classify_route_prompt(self, input: Dict[str, Any]) -> str:
        """
        Build the user-facing prompt for the ``explore`` vs ``build`` classification.

        **Description:**

            Renders the user's query alongside attached files and any structured
            request body / metadata so the LLM has full context for the routing
            decision.

        :param input: Dictionary with the following expected keys:

            - ``query`` (str): The user's natural-language instruction.
            - ``body`` (dict, optional): Structured request payload (may already
              contain ``targetColumn`` / ``target_column``).
            - ``file_urls`` (list, optional): Attached ``s3://`` or HTTP URLs.
            - ``metadata`` (dict, optional): Ambient metadata from the chat layer.

        :type input: Dict[str, Any]

        :return: Prompt to feed to the orchestrator's classification call.
        :rtype: str

        **Example:**

        .. code-block:: python

            from squirrel.modules.prompts.orchestrator import (
                OrchestratorPromptGenerator,
                OrchestratorPromptType,
            )

            generator = OrchestratorPromptGenerator()

            user_prompt = generator.generate_user_prompt(
                prompt_type=OrchestratorPromptType.CLASSIFY_ROUTE,
                query="Predict whether a customer will churn next month.",
                body={},
                file_urls=["s3://bucket/customers.csv"],
                metadata={},
            )

        """
        query:     str            = input.get("query", "") or ""
        body:      Dict[str, Any] = input.get("body", {}) or {}
        file_urls: List[Any]      = input.get("file_urls", []) or []
        metadata:  Dict[str, Any] = input.get("metadata", {}) or {}

        prompt = f"""
        # USER QUERY:
        {query or "(empty)"}

        # ATTACHED FILES:
        {file_urls or "none"}

        # REQUEST BODY:
        {json.dumps(body, default=str) if body else "none"}

        # METADATA:
        {json.dumps(metadata, default=str) if metadata else "none"}

        # TASK:
        Classify this request as "chat", "explore", or "build" following the
        rules in the system prompt, and return the JSON object with fields:
        route, target_column, confidence, reasoning.
        """

        return prompt