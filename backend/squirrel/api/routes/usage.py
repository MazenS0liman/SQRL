#!/usr/bin/python
"""
Usage Routes
============

Read-only endpoints behind the Usage page. All figures are scoped to the
signed-in user; no route exposes another user's spend.
"""
# ——————————————————————————————————————————————————————————————
# Imports

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool

from squirrel.api.routes.auth import get_current_user
from squirrel.constants.usage import (
    KNOWN_PROVIDERS,
    MODEL_PRICING,
    PROVIDER_LABELS,
)
from squirrel.schemas.auth import AuthUserRead
from squirrel.schemas.usage import (
    UsageEventListResponse,
    UsageOverviewResponse,
    UsagePricingEntry,
    UsagePricingResponse,
)
from squirrel.services.usage.UsageService import UsageService


router = APIRouter()


def _svc() -> UsageService:
    return UsageService()


@router.get(
    "/overview",
    summary="Aggregated LLM token usage for the current user",
    response_model=UsageOverviewResponse,
)
async def usage_overview(
    days: int = Query(default=30, ge=1, le=365),
    current_user: AuthUserRead = Depends(get_current_user),
) -> UsageOverviewResponse:
    """Return totals, per-provider/model/source breakdowns, and a daily series."""
    payload = await run_in_threadpool(_svc().overview, current_user.id, days)
    return UsageOverviewResponse(**payload)


@router.get(
    "/events",
    summary="Raw LLM usage events for the current user",
    response_model=UsageEventListResponse,
)
async def usage_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    provider: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1, le=365),
    current_user: AuthUserRead = Depends(get_current_user),
) -> UsageEventListResponse:
    """Return a newest-first page of recorded provider calls."""
    items, total = await run_in_threadpool(
        _svc().events,
        current_user.id,
        limit=limit,
        offset=offset,
        provider=provider,
        source=source,
        days=days,
    )
    return UsageEventListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/pricing",
    summary="Static pricing table used to estimate spend",
    response_model=UsagePricingResponse,
)
async def usage_pricing() -> UsagePricingResponse:
    """Expose the approximate per-model prices so the UI can explain costs."""
    return UsagePricingResponse(
        providers={provider: PROVIDER_LABELS.get(provider, provider) for provider in KNOWN_PROVIDERS},
        pricing=[
            UsagePricingEntry(
                model=model,
                input_per_million=prices[0],
                output_per_million=prices[1],
            )
            for model, prices in sorted(MODEL_PRICING.items())
        ],
        note=(
            "Estimated from provider-reported token counts using public list "
            "prices. Actual invoices may differ; unknown models are counted as $0."
        ),
    )
