#!/usr/bin/python
"""
Orchestrator Agent Module
=========================

Classifies incoming chat requests into one of three execution routes using a
single LLM call:

``chat``
    General conversation — no specialised data pipeline applies.

``explore``
    Hypothesis-driven EDA — the user wants to understand, analyse, or query
    their data (find patterns, trends, anomalies, relationships) without
    necessarily training a predictive model.

``build``
    Full ML pipeline — the user wants to predict, classify, or forecast a
    specific target variable, i.e. they want a trained model.

The classification asks the LLM to return structured JSON (route + target
column + confidence + reasoning) rather than parsing free text, so the
result is deterministic to consume downstream.

"""
# ——————————————————————————————————————————————————————————————
# Imports

from __future__ import annotations

# Standard Libraries
import json
from typing import Any, Optional, Dict, List

# Abstract classes
from squirrel.modules.agents.abstract.IAgent import IAgent
from squirrel.modules.providers.abstract.IProvider import Provider

# Prompts
from squirrel.modules.prompts.orchestrator.OrchestratorPromptGenerator import (
    OrchestratorPromptGenerator,
    OrchestratorPromptType
)

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Orchestrator Agent
class OrchestratorAgent(IAgent):
    """
    Classify a request and return the route the service should execute.
    """

    _VALID_ROUTES = {"chat", "explore", "build"}

    def __init__(
        self,
        api_key:          Optional[str]                        = "GEMINI_API_KEY_V1",
        provider:         Optional[Provider]                   = Provider.GEMINI,
        model_name:       Optional[str]                        = "models/gemini-2.5-flash",
        prompt_generator: Optional[OrchestratorPromptGenerator] = None,
        config:           Optional[Dict[str, Any]]              = None,
    ) -> None:
        super().__init__(
            api_key,
            provider,
            model_name,
            prompt_generator=prompt_generator or OrchestratorPromptGenerator(),
            config=config or {}
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def orchestrate(
        self,
        query:     str,
        body:      Optional[Dict[str, Any]] = None,
        file_urls: Optional[List[Any]]      = None,
        metadata:  Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Ask the LLM to classify *query*.

        :param query:     User's natural-language instruction.
        :param body:      Optional structured payload.
        :param file_urls: Attached ``s3://`` or HTTP URLs (included in the
                          prompt for context only).
        :param metadata:  Ambient metadata forwarded from the chat layer.
        :return:          Route-decision dict — see :meth:`_decision`.
        """
        body      = body      or {}
        metadata  = metadata  or {}
        file_urls = file_urls or []

        system_prompt = self.prompt_generator.generate_system_prompt(
            prompt_type=OrchestratorPromptType.CLASSIFY_ROUTE,
        )
        user_prompt = self.prompt_generator.generate_user_prompt(
            prompt_type=OrchestratorPromptType.CLASSIFY_ROUTE,
            query=query,
            body=body,
            file_urls=file_urls,
            metadata=metadata,
        )

        response = self.get_response(
            fn_name="generate_json",
            model_input={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "schema": self._classification_schema(),
            },
            strict=False,
            provider_order=[Provider.GROQ, Provider.GEMINI],
            preference_model_names=[
                "llama-3.3-70b-versatile",
                "models/gemini-2.5-flash",
            ],
        )

        parsed = self._parse_response(response)

        route = str(parsed.get("route", "")).strip().lower()
        if route not in self._VALID_ROUTES:
            logger.warning(
                "OrchestratorAgent: LLM returned invalid route '{}', defaulting to 'chat'.",
                route,
            )
            route = "chat"

        confidence = parsed.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))

        target_column = parsed.get("target_column") or None
        reasoning = parsed.get("reasoning") or "LLM classification."

        return self._decision(
            route_key=route,
            target_column=target_column,
            confidence=confidence,
            reasoning=reasoning,
        )

    @staticmethod
    def _classification_schema() -> Dict:
        return {
            "type": "object",
            "properties": {
                "route": {
                    "type": "string",
                    "enum": ["chat", "explore", "build"],
                    "description": "The chosen route.",
                },
                "target_column": {
                    "type": ["string", "null"],
                    "description": (
                        "Name of the target column to predict, if route is 'build' "
                        "and a target can be confidently inferred. Otherwise null."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "description": "Confidence in the classification, between 0 and 1.",
                },
                "reasoning": {
                    "type": "string",
                    "description": "One-sentence explanation for the routing decision.",
                },
            },
            "required": ["route", "target_column", "confidence", "reasoning"],
        }

    # ── Parsing ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_response(response: Any) -> Dict[str, Any]:
        """
        Coerce the raw LLM response into a dict, tolerating string/None.
        """
        if response is None:
            logger.warning("OrchestratorAgent: LLM returned no response.")
            return {}
        if isinstance(response, Dict):
            return response
        if isinstance(response, str):
            try:
                parsed = json.loads(response)
                return parsed if isinstance(parsed, Dict) else {}
            except json.JSONDecodeError:
                logger.warning(
                    "OrchestratorAgent: could not parse LLM response as JSON: {}",
                    response,
                )
                return {}
        return {}

    # ── Decision assembly ────────────────────────────────────────────────────

    @staticmethod
    def _decision(
        route_key:     str,
        target_column: Optional[str],
        confidence:    float,
        reasoning:     str,
    ) -> dict[str, Any]:
        """
        Assemble the canonical route-decision dict consumed by
        :class:`OrchestratorService`.

        :param route_key:     ``'chat'``, ``'explore'``, or ``'build'``.
        :param target_column: Target column name, or ``None``.
        :param confidence:    Classifier confidence in [0, 1].
        :param reasoning:     Human-readable explanation.
        
        :return:               Route-decision dict.
        """
        agent_name = (
            "TabularDataModelBuilderAgent"
            if route_key == "build"
            else "TabularDataExploratoryAgent"
            if route_key == "explore"
            else "GeneralPurposeAgent"
        )
        return {
            "route_key":     route_key,
            "agent_name":    agent_name,
            "target_column": target_column,
            "confidence":    confidence,
            "observations":  [reasoning],
            "reason":        reasoning,
        }