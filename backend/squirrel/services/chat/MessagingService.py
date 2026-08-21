"""
Messaging Service
=================

Owns all PostgreSQL writes for chat messages: incoming user messages,
attached file URLs, assistant replies, and associated token/timing metadata.

Schema expectation
------------------

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS chat_messages (
        id                  BIGSERIAL PRIMARY KEY,
        conversation_id     TEXT        NOT NULL,
        user_message        TEXT        NOT NULL,
        file_urls           TEXT[]      DEFAULT '{}',
        assistant_reply     TEXT        NOT NULL,
        prompt_tokens       INT         DEFAULT 0,
        completion_tokens   INT         DEFAULT 0,
        total_tokens        INT         DEFAULT 0,
        response_time_ms    INT         DEFAULT 0,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
from datetime import datetime, timezone
from typing import Optional

# Services
from squirrel.services.storage.database.PostgresService import PostgresService

# Schemas
from squirrel.schemas.api import AgentRequest, AgentResponse

# Logging
from loguru import logger

# ——————————————————————————————————————————————————————————————
# Messaging Service


class MessagingService:
    """
    Handle messaging operations for chat interactions.

    Each public method is intentionally narrow: callers pass in the objects
    they already have; this service handles all SQL concerns.

    :param db: An open :class:`PostgresService` instance. When omitted a new
        one is created using environment-variable credentials.
    :type db: Optional[PostgresService]

    """

    _TABLE = "chat_messages"

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._db = db or PostgresService()
        
        # Create the chat_messages table if it doesn't exist. This is a no-op if the
        # table already exists, so it's safe to run on every init.
        table_exists = self._db.retrieve(
            self._TABLE,
            filters={},
        )
        
        if not table_exists or not table_exists.get("rows"):
            logger.info("Creating chat_messages table in PostgreSQL")
            
            self._db.execute(
                f"""
                    CREATE TABLE IF NOT EXISTS {self._TABLE} (
                        id                  BIGSERIAL PRIMARY KEY,
                        conversation_id     TEXT        NOT NULL,
                        user_message        TEXT        NOT NULL,
                        file_urls           TEXT[]      DEFAULT '{{}}',
                        assistant_reply     TEXT        NOT NULL,
                        prompt_tokens       INT         DEFAULT 0,
                        completion_tokens   INT         DEFAULT 0,
                        total_tokens        INT         DEFAULT 0,
                        response_time_ms    INT         DEFAULT 0,
                        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                """,
                params={},
                fetch=False
            )

    # ------------------------------------------------------------------
    # Public API

    def save_chat_turn(
        self,
        request: AgentRequest,
        response: AgentResponse,
        file_urls: list[str],
    ) -> bool:
        """
        Persist a completed chat turn to the ``chat_messages`` table.

        :param request: The original chat request from the user.
        :type request: AgentRequest
        :param response: The assistant's response.
        :type response: AgentResponse
        :param file_urls: Resolved ``s3://`` URLs for any files attached to
            the request (may be empty).
        :type file_urls: list[str]
        
        :return: ``True`` when the row was written successfully.
        :rtype: bool
        """
        user_message = request.query if request.query else str(request.message or "")

        # psycopg accepts Python lists for array columns directly.
        ok = self._db.insert(
            {
                "sql": """
                    INSERT INTO chat_messages (
                        conversation_id,
                        user_message,
                        file_urls,
                        assistant_reply,
                        prompt_tokens,
                        completion_tokens,
                        total_tokens,
                        response_time_ms,
                        created_at
                    ) VALUES (
                        %(conversation_id)s,
                        %(user_message)s,
                        %(file_urls)s,
                        %(assistant_reply)s,
                        %(prompt_tokens)s,
                        %(completion_tokens)s,
                        %(total_tokens)s,
                        %(response_time_ms)s,
                        %(created_at)s
                    )
                """,
                "params": {
                    "conversation_id": str(request.conversationId or ""),
                    "user_message": user_message,
                    "file_urls": file_urls,
                    "assistant_reply": response.reply or "",
                    "prompt_tokens": response.llmTokens.promptTokens if response.llmTokens else 0,
                    "completion_tokens": response.llmTokens.completionTokens if response.llmTokens else 0,
                    "total_tokens": response.llmTokens.totalTokens if response.llmTokens else 0,
                    "response_time_ms": response.responseTime or 0,
                    "created_at": datetime.now(timezone.utc),
                },
            }
        )

        if ok:
            logger.info(
                "Persisted chat turn for conversation={}", request.conversationId
            )
        else:
            logger.error(
                "Failed to persist chat turn for conversation={}", request.conversationId
            )

        return ok

    def get_conversation_history(
        self,
        conversation_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """
        Return recent messages for *conversation_id*, oldest first.

        :param conversation_id: The conversation to fetch.
        :type conversation_id: str
        :param limit: Maximum number of rows to return.
        :type limit: int
        
        :return: List of row dicts ordered by ``created_at`` ascending.
        :rtype: list[dict]
        """
        result = self._db.retrieve(
            self._TABLE,
            filters={"conversation_id": conversation_id},
        )
        rows = result.get("rows", []) if result else []
        # Sort ascending (retrieve() may not guarantee order) and apply limit.
        rows.sort(key=lambda r: r.get("created_at", ""))
        return rows[:limit]


