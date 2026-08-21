#!/usr/bin/python
"""
Token Service
=============

Lets an authenticated user store third-party provider tokens (LLM API keys,
etc.) so they can be reused later without re-entering them. Tokens are
encrypted at rest with Fernet (AES128-CBC + HMAC) - the plaintext value is
never persisted and is only ever returned to the *owning* user via the
`get_decrypted_token` method used internally by other services (never
exposed over the API).

Requires a TOKEN_ENCRYPTION_KEY env var - generate one with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and add it to your .env (never commit it, same as the other secrets in
that file).
"""
# ------------------------------------------------------------------------------
# Imports

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import secrets
from threading import Lock
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from squirrel.schemas.token import TokenRead
from squirrel.services.storage.database.PostgresService import PostgresService

from loguru import logger


@dataclass(slots=True)
class TokenRecord:
    id: str
    user_id: str
    provider: str
    label: Optional[str]
    ciphertext: str
    created_at: datetime
    last_used_at: Optional[datetime]


class TokenService:
    _TABLE = "user_provider_tokens"

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._lock = Lock()
        self._db = db
        self._tables_ready = False
        self._fernet: Optional[Fernet] = None

    # -- setup -----------------------------------------------------------

    def _get_fernet(self) -> Fernet:
        if self._fernet is None:
            key = os.environ.get("TOKEN_ENCRYPTION_KEY")
            if not key:
                raise RuntimeError(
                    "TOKEN_ENCRYPTION_KEY is not set. Generate one with "
                    "`python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\"` and add it to your .env."
                )
            self._fernet = Fernet(key.encode("utf-8"))
        return self._fernet

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            self._ensure_tables(self._db)
            self._tables_ready = True
        return self._db

    def _ensure_tables(self, db: PostgresService) -> None:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._TABLE} (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
                provider        TEXT NOT NULL,
                label           TEXT,
                ciphertext      TEXT NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_used_at    TIMESTAMPTZ,
                UNIQUE (user_id, provider, label)
            );
            """,
            params={},
            fetch=False,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _mask(token: str) -> str:
        token = token.strip()
        if len(token) <= 10:
            return "•" * max(len(token) - 2, 0) + token[-2:]
        return f"{token[:6]}...{token[-6:]}"

    def _row_to_read(self, row: dict) -> TokenRead:
        return TokenRead(
            id=row["id"],
            provider=row["provider"],
            label=row.get("label"),
            token_preview=self._mask(self._decrypt(row["ciphertext"])),
            created_at=row["created_at"],
            last_used_at=row.get("last_used_at"),
        )

    def _encrypt(self, plaintext: str) -> str:
        return self._get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")

    def _decrypt(self, ciphertext: str) -> str:
        try:
            return self._get_fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("Stored token could not be decrypted") from exc

    # -- public API -----------------------------------------------------------

    def add_token(self, user_id: str, provider: str, token: str, label: Optional[str] = None) -> TokenRead:
        db = self._get_db()
        token_id = secrets.token_urlsafe(12)
        created_at = datetime.now(timezone.utc)

        with self._lock:
            existing = db.query(
                f"""
                SELECT id FROM {self._TABLE}
                WHERE user_id = %(user_id)s AND provider = %(provider)s
                  AND label IS NOT DISTINCT FROM %(label)s
                LIMIT 1
                """,
                params={"user_id": user_id, "provider": provider, "label": label},
            )
            rows = (existing or {}).get("rows") or []

            if rows:
                # Update in place instead of erroring, so re-saving a key is idempotent
                updated = db.query(
                    f"""
                    UPDATE {self._TABLE}
                    SET ciphertext = %(ciphertext)s, created_at = %(created_at)s
                    WHERE id = %(id)s
                    RETURNING id, provider, label, ciphertext, created_at, last_used_at
                    """,
                    params={
                        "ciphertext": self._encrypt(token),
                        "created_at": created_at,
                        "id": rows[0]["id"],
                    },
                )
                result_rows = (updated or {}).get("rows") or []
            else:
                inserted = db.insert(
                    {
                        "sql": f"""
                            INSERT INTO {self._TABLE} (
                                id, user_id, provider, label, ciphertext, created_at
                            ) VALUES (
                                %(id)s, %(user_id)s, %(provider)s, %(label)s, %(ciphertext)s, %(created_at)s
                            )
                            RETURNING id, provider, label, ciphertext, created_at, last_used_at
                        """,
                        "params": {
                            "id": token_id,
                            "user_id": user_id,
                            "provider": provider,
                            "label": label,
                            "ciphertext": self._encrypt(token),
                            "created_at": created_at,
                        },
                    }
                )
                result_rows = inserted or []

            if not result_rows:
                raise RuntimeError("Failed to save token")
            return self._row_to_read(result_rows[0])

    def list_tokens(self, user_id: str) -> list[TokenRead]:
        result = self._get_db().query(
            f"""
            SELECT id, provider, label, ciphertext, created_at, last_used_at
            FROM {self._TABLE}
            WHERE user_id = %(user_id)s
            ORDER BY created_at DESC
            """,
            params={"user_id": user_id},
        )
        rows = (result or {}).get("rows") or []
        return [self._row_to_read(row) for row in rows]

    def delete_token(self, user_id: str, token_id: str) -> bool:
        result = self._get_db().query(
            f"""
            DELETE FROM {self._TABLE}
            WHERE id = %(id)s AND user_id = %(user_id)s
            """,
            params={"id": token_id, "user_id": user_id},
        )
        return bool(result and result.get("success"))

    def get_decrypted_token(
        self, 
        user_id: str, 
        provider: str, 
        label: Optional[str] = None
    ) -> Optional[str]:
        """Internal use only (e.g. by other backend services that need to call
        out to a provider on the user's behalf). Never expose this over the API."""
        result = self._get_db().query(
            f"""
            SELECT id, ciphertext FROM {self._TABLE}
            WHERE user_id = %(user_id)s AND provider = %(provider)s
            LIMIT 1
            """,
            params={"user_id": user_id, "provider": provider},
        )
        rows = (result or {}).get("rows") or []
        logger.info(f"Retrieved token for user_id={user_id}, provider={provider}, label={label}: {'found' if rows else 'not found'}")
        if not rows:
            return None

        row = rows[0]
        self._get_db().execute(
            f"UPDATE {self._TABLE} SET last_used_at = %(now)s WHERE id = %(id)s",
            params={"now": datetime.now(timezone.utc), "id": row["id"]},
            fetch=False,
        )
        return self._decrypt(row["ciphertext"])


token_service = TokenService()