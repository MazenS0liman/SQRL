#!/usr/bin/python
"""
Usage Schemas
=============

API models for the per-user LLM usage monitor.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class UsageTotals(BaseModel):
    """Aggregated token and spend figures."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    estimated_cost_usd: float = 0.0


class UsageProviderBreakdown(UsageTotals):
    """Usage attributed to one provider."""

    provider: str


class UsageModelBreakdown(UsageTotals):
    """Usage attributed to one provider/model pair."""

    provider: str
    model: str


class UsageSourceBreakdown(UsageTotals):
    """Usage attributed to one product feature."""

    source: str


class UsageDailyPoint(BaseModel):
    """One day of usage for one provider."""

    day: str
    provider: str
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class UsageEventRead(BaseModel):
    """A single recorded provider call."""

    id: int
    user_id: Optional[str] = None
    provider: str
    model: str
    source: str = "agent"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    estimated_cost_usd: float = 0.0
    created_at: Optional[datetime] = None


class UsageOverviewResponse(BaseModel):
    """Everything the Usage page needs for one trailing window."""

    window_days: int = 30
    totals: UsageTotals = Field(default_factory=UsageTotals)
    by_provider: List[UsageProviderBreakdown] = Field(default_factory=list)
    by_model: List[UsageModelBreakdown] = Field(default_factory=list)
    by_source: List[UsageSourceBreakdown] = Field(default_factory=list)
    daily: List[UsageDailyPoint] = Field(default_factory=list)
    recent_events: List[UsageEventRead] = Field(default_factory=list)


class UsageEventListResponse(BaseModel):
    """A page of raw usage events."""

    items: List[UsageEventRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


class UsagePricingEntry(BaseModel):
    """Pricing metadata surfaced to the UI so costs are explainable."""

    model: str
    input_per_million: float
    output_per_million: float


class UsagePricingResponse(BaseModel):
    """Static pricing table plus the provider labels used by the UI."""

    providers: Dict[str, str] = Field(default_factory=dict)
    pricing: List[UsagePricingEntry] = Field(default_factory=list)
    note: str = ""
