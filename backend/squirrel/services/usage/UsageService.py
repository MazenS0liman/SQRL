#!/usr/bin/python
"""
Usage Service
=============

Append-only ledger of LLM calls plus the aggregations that power the Usage
page. One row is written per successful provider call by
:meth:`IAgent._call_exact_model`, so the page can show spend per provider,
per model, per feature, and over time for the signed-in user.

Recording must never break an LLM call: :func:`record_llm_usage` swallows and
logs storage errors instead of propagating them.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Logging
from loguru import logger

# Constants
from squirrel.constants.usage import estimate_cost

# Services
from squirrel.services.storage.database.PostgresService import PostgresService
from squirrel.services.usage.context import get_usage_source, get_usage_user_id
from squirrel.services.usage.schema import USAGE_DDL_STATEMENTS


class UsageService:
    """Read and write the per-user LLM usage ledger."""

    _TABLE = "llm_usage_events"

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._db = db
        self._tables_ready = False

    # ------------------------------------------------------------------
    # Bootstrap

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            for statement in USAGE_DDL_STATEMENTS:
                self._db.execute(statement, params={}, fetch=False)
            self._tables_ready = True
        return self._db

    # ------------------------------------------------------------------
    # Writes

    def record(
        self,
        *,
        user_id: Optional[str],
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        request_count: int = 1,
        source: Optional[str] = None,
    ) -> None:
        """
        Append one usage event.

        :param user_id: Owning user; ``None`` records an unattributed call.
        :param provider: Provider identifier (``gemini``, ``groq``, ...).
        :param model: Model identifier as reported by the provider.
        :param prompt_tokens: Input tokens consumed by the call.
        :param completion_tokens: Output tokens produced by the call.
        :param total_tokens: Provider-reported total; falls back to the sum.
        :param request_count: Requests represented by this row.
        :param source: Feature that triggered the call; defaults to the
            context value set by middleware/tasks.
        """
        prompt_tokens = max(int(prompt_tokens or 0), 0)
        completion_tokens = max(int(completion_tokens or 0), 0)
        total_tokens = max(int(total_tokens or 0), 0) or (prompt_tokens + completion_tokens)
        request_count = max(int(request_count or 0), 0)
        if request_count == 0 and total_tokens == 0:
            return

        self._get_db().query(
            f"""
            INSERT INTO {self._TABLE}
                (user_id, provider, model, source, prompt_tokens,
                 completion_tokens, total_tokens, request_count, estimated_cost_usd)
            VALUES
                (%(user_id)s, %(provider)s, %(model)s, %(source)s, %(prompt_tokens)s,
                 %(completion_tokens)s, %(total_tokens)s, %(request_count)s, %(cost)s)
            """,
            params={
                "user_id": user_id,
                "provider": (provider or "unknown").lower(),
                "model": model or "unknown",
                "source": (source or get_usage_source() or "agent").lower(),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "request_count": request_count or 1,
                "cost": estimate_cost(provider, model, prompt_tokens, completion_tokens),
            },
            fetch=False,
        )

    # ------------------------------------------------------------------
    # Reads

    def overview(self, user_id: str, days: int = 30) -> Dict[str, Any]:
        """
        Aggregate a user's usage over the trailing ``days`` window.

        :return: Totals, per-provider and per-model breakdowns, a daily time
            series, and the most recent events.
        """
        since = _since(days)
        params = {"user_id": user_id, "since": since}
        db = self._get_db()

        totals = _row(
            db.query(
                f"""
                SELECT
                    COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                    COALESCE(SUM(request_count), 0)     AS requests,
                    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND created_at >= %(since)s
                """,
                params=params,
                fetch=True,
            )
        )

        by_provider = _rows(
            db.query(
                f"""
                SELECT provider,
                       COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(request_count), 0)     AS requests,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND created_at >= %(since)s
                GROUP BY provider
                ORDER BY total_tokens DESC
                """,
                params=params,
                fetch=True,
            )
        )

        by_model = _rows(
            db.query(
                f"""
                SELECT provider, model,
                       COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(request_count), 0)     AS requests,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND created_at >= %(since)s
                GROUP BY provider, model
                ORDER BY total_tokens DESC
                LIMIT 25
                """,
                params=params,
                fetch=True,
            )
        )

        daily = _rows(
            db.query(
                f"""
                SELECT TO_CHAR(DATE_TRUNC('day', created_at), 'YYYY-MM-DD') AS day,
                       provider,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND created_at >= %(since)s
                GROUP BY day, provider
                ORDER BY day ASC
                """,
                params=params,
                fetch=True,
            )
        )

        by_source = _rows(
            db.query(
                f"""
                SELECT source,
                       COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                       COALESCE(SUM(request_count), 0)     AS requests,
                       COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND created_at >= %(since)s
                GROUP BY source
                ORDER BY total_tokens DESC
                """,
                params=params,
                fetch=True,
            )
        )

        recent = _rows(
            db.query(
                f"""
                SELECT id, user_id, provider, model, source, prompt_tokens,
                       completion_tokens, total_tokens, request_count,
                       estimated_cost_usd, created_at
                FROM {self._TABLE}
                WHERE user_id = %(user_id)s
                ORDER BY created_at DESC
                LIMIT 10
                """,
                params={"user_id": user_id},
                fetch=True,
            )
        )

        return {
            "window_days": int(days),
            "totals": _normalise_totals(totals),
            "by_provider": [_normalise_breakdown(row) for row in by_provider],
            "by_model": [_normalise_breakdown(row) for row in by_model],
            "by_source": [_normalise_breakdown(row) for row in by_source],
            "daily": [
                {
                    "day": row.get("day"),
                    "provider": row.get("provider"),
                    "total_tokens": int(row.get("total_tokens") or 0),
                    "estimated_cost_usd": float(row.get("estimated_cost_usd") or 0.0),
                }
                for row in daily
            ],
            "recent_events": [_normalise_event(row) for row in recent],
        }

    def events(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        provider: Optional[str] = None,
        source: Optional[str] = None,
        days: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Return a page of raw usage events plus the total matching count.

        :param user_id: Owner whose events are returned.
        :param limit: Page size.
        :param offset: Rows to skip.
        :param provider: Optional provider filter.
        :param source: Optional feature filter.
        :param days: Optional trailing-window filter.
        """
        clauses = ["user_id = %(user_id)s"]
        params: Dict[str, Any] = {"user_id": user_id}
        if provider:
            clauses.append("provider = %(provider)s")
            params["provider"] = provider.lower()
        if source:
            clauses.append("source = %(source)s")
            params["source"] = source.lower()
        if days:
            clauses.append("created_at >= %(since)s")
            params["since"] = _since(days)
        where = " AND ".join(clauses)

        db = self._get_db()
        total_row = _row(
            db.query(
                f"SELECT COUNT(*) AS count FROM {self._TABLE} WHERE {where}",
                params=params,
                fetch=True,
            )
        )
        rows = _rows(
            db.query(
                f"""
                SELECT id, user_id, provider, model, source, prompt_tokens,
                       completion_tokens, total_tokens, request_count,
                       estimated_cost_usd, created_at
                FROM {self._TABLE}
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params={**params, "limit": int(limit), "offset": int(offset)},
                fetch=True,
            )
        )
        return [_normalise_event(row) for row in rows], int(total_row.get("count") or 0)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers


def _since(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=max(int(days or 1), 1))


def _rows(result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list((result or {}).get("rows") or [])


def _row(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rows = _rows(result)
    return rows[0] if rows else {}


def _normalise_totals(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "requests": int(row.get("requests") or 0),
        "estimated_cost_usd": round(float(row.get("estimated_cost_usd") or 0.0), 6),
    }


def _normalise_breakdown(row: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        key: value
        for key, value in row.items()
        if key in {"provider", "model", "source"}
    }
    payload.update(_normalise_totals(row))
    return payload


def _normalise_event(row: Dict[str, Any]) -> Dict[str, Any]:
    created = row.get("created_at")
    if isinstance(created, datetime):
        created = created.isoformat()
    return {
        "id": int(row.get("id") or 0),
        "user_id": row.get("user_id"),
        "provider": row.get("provider"),
        "model": row.get("model"),
        "source": row.get("source"),
        "prompt_tokens": int(row.get("prompt_tokens") or 0),
        "completion_tokens": int(row.get("completion_tokens") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "request_count": int(row.get("request_count") or 0),
        "estimated_cost_usd": round(float(row.get("estimated_cost_usd") or 0.0), 6),
        "created_at": created,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Fire-and-forget recorder used by IAgent

_service: Optional[UsageService] = None


def _get_service() -> UsageService:
    global _service
    if _service is None:
        _service = UsageService()
    return _service


def record_llm_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    request_count: int = 1,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    """
    Record one provider call without ever raising into the caller.

    ``user_id`` falls back to the request/task context so agents do not need to
    be constructed with the authenticated user. Recording failures are logged
    and ignored — usage monitoring must never fail an LLM call.
    """
    try:
        _get_service().record(
            user_id=user_id or get_usage_user_id(),
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            request_count=request_count,
            source=source,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not record LLM usage event: {}", exc)
