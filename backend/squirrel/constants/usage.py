#!/usr/bin/python
"""
LLM Usage Constants
===================

Pricing metadata used to turn the token counts reported by every provider into
an approximate spend figure. Prices are USD per **one million** tokens and are
intentionally conservative: they exist to help a user spot expensive models,
not to replace a provider invoice.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from typing import Dict, Optional, Tuple


#: Human-readable labels for the providers the app can call.
PROVIDER_LABELS: Dict[str, str] = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "groq": "Groq",
    "openrouter": "OpenRouter",
}

#: Every provider name known to the usage monitor, in display order.
KNOWN_PROVIDERS: Tuple[str, ...] = ("gemini", "groq", "openrouter", "openai")

#: ``(input_per_million, output_per_million)`` in USD. Keys are matched as
#: case-insensitive substrings of the model name, longest key first, so
#: ``models/gemini-2.5-pro`` resolves to the ``gemini-2.5-pro`` entry.
MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
    # Groq (hosted open-weight models)
    "llama-4-scout": (0.11, 0.34),
    "llama-3.3-70b": (0.59, 0.79),
    "llama-3.1-8b": (0.05, 0.08),
    "gpt-oss-120b": (0.15, 0.75),
    "gpt-oss-20b": (0.075, 0.30),
    "mixtral-8x7b": (0.24, 0.24),
    # OpenRouter (routed models)
    "anthropic/claude-3.5-sonnet": (3.00, 15.00),
    "anthropic/claude-3-haiku": (0.25, 1.25),
    "deepseek/deepseek-chat": (0.14, 0.28),
}

#: Fallback price used when a model is not in :data:`MODEL_PRICING`. Zero keeps
#: unknown/free models from inflating the reported spend.
DEFAULT_PRICING: Tuple[float, float] = (0.0, 0.0)


def resolve_pricing(model_name: Optional[str]) -> Tuple[float, float]:
    """
    Return ``(input_per_million, output_per_million)`` for a model name.

    Matching is case-insensitive and prefers the longest known key so a
    specific entry (``gpt-4o-mini``) wins over a shorter prefix (``gpt-4o``).

    :param model_name: Provider-reported model identifier.
    :return: Price tuple in USD per million tokens.
    """
    if not model_name:
        return DEFAULT_PRICING
    haystack = model_name.lower()
    for key in sorted(MODEL_PRICING, key=len, reverse=True):
        if key.lower() in haystack:
            return MODEL_PRICING[key]
    return DEFAULT_PRICING


def estimate_cost(
    provider: Optional[str],
    model_name: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """
    Estimate the USD cost of one LLM call.

    :param provider: Provider identifier (kept for future provider-level
        pricing overrides; the model name is what is priced today).
    :param model_name: Model identifier reported by the provider.
    :param prompt_tokens: Input/prompt tokens consumed by the call.
    :param completion_tokens: Output/completion tokens produced by the call.
    :return: Estimated cost in USD, rounded to six decimal places.
    """
    input_price, output_price = resolve_pricing(model_name)
    cost = (
        (max(int(prompt_tokens or 0), 0) / 1_000_000.0) * input_price
        + (max(int(completion_tokens or 0), 0) / 1_000_000.0) * output_price
    )
    return round(cost, 6)
