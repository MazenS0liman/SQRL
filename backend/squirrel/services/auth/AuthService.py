#!/usr/bin/python
"""
Authentication Service
======================

PostgreSQL-backed helpers for the user authentication API.

"""
# ------------------------------------------------------------------------------
# Imports

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import hmac
import os
import secrets
from threading import Lock
from typing import Optional

from squirrel.schemas.auth import AuthUserRead
from squirrel.services.storage.database.PostgresService import PostgresService


@dataclass(slots=True)
class UserRecord:
    id: str
    username: str
    email: str
    password_hash: str
    full_name: Optional[str]
    created_at: datetime


@dataclass(slots=True)
class TokenRecord:
    token: str
    user_id: str
    expires_at: datetime


class AuthService:
    _USERS_TABLE = "auth_users"
    _TOKENS_TABLE = "auth_tokens"

    def __init__(self, db: Optional[PostgresService] = None) -> None:
        self._lock = Lock()
        self._db = db
        self._tables_ready = False

    def _get_db(self) -> PostgresService:
        if self._db is None:
            self._db = PostgresService()
        if not self._tables_ready:
            self._ensure_tables(self._db)
            self._tables_ready = True
        return self._db

    def _ensure_tables(
        self, 
        db: PostgresService
    ) -> None:
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._USERS_TABLE} (
                id              TEXT PRIMARY KEY,
                username        TEXT UNIQUE NOT NULL,
                email           TEXT UNIQUE NOT NULL,
                password_hash   TEXT NOT NULL,
                full_name       TEXT,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            params={},
            fetch=False,
        )
        db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._TOKENS_TABLE} (
                token       TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL REFERENCES {self._USERS_TABLE}(id) ON DELETE CASCADE,
                expires_at  TIMESTAMPTZ NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """,
            params={},
            fetch=False,
        )
        self._ensure_default_admin(db)

    def _ensure_default_admin(self, db: PostgresService) -> None:
        existing = db.query(
            f"""
            SELECT id
            FROM {self._USERS_TABLE}
            WHERE lower(username) = 'admin'
            LIMIT 1
            """,
            params={},
        )
        if (existing or {}).get("rows"):
            return

        now = datetime.now(timezone.utc)
        db.insert(
            {
                "sql": f"""
                    INSERT INTO {self._USERS_TABLE} (
                        id,
                        username,
                        email,
                        password_hash,
                        full_name,
                        created_at,
                        updated_at
                    ) VALUES (
                        %(id)s,
                        %(username)s,
                        %(email)s,
                        %(password_hash)s,
                        %(full_name)s,
                        %(created_at)s,
                        %(updated_at)s
                    )
                """,
                "params": {
                    "id": "admin",
                    "username": "admin",
                    "email": "admin@squirrel.local",
                    "password_hash": self._hash_password("admin"),
                    "full_name": "Admin User",
                    "created_at": now,
                    "updated_at": now,
                },
            }
        )

    @staticmethod
    def _normalize_identifier(identifier: str) -> str:
        return identifier.strip().lower()

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
        salt = salt or os.urandom(16)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 390000)
        return "pbkdf2_sha256$390000$%s$%s" % (
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )

    @staticmethod
    def _verify_password(password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations, salt_b64, hash_b64 = password_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(hash_b64.encode("ascii"))
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                int(iterations),
            )
            return hmac.compare_digest(derived, expected)
        except Exception:
            return False

    @staticmethod
    def _row_to_user(row: dict) -> UserRecord:
        return UserRecord(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"],
            full_name=row.get("full_name"),
            created_at=row["created_at"],
        )

    def _fetch_user_row(self, identifier: str) -> Optional[dict]:
        normalized = self._normalize_identifier(identifier)
        result = self._get_db().query(
            f"""
            SELECT id, username, email, password_hash, full_name, created_at
            FROM {self._USERS_TABLE}
            WHERE lower(username) = %(identifier)s OR lower(email) = %(identifier)s
            LIMIT 1
            """,
            params={"identifier": normalized},
        )
        rows = (result or {}).get("rows") or []
        return rows[0] if rows else None

    def register_user(self, username: str, email: str, password: str, full_name: Optional[str] = None) -> UserRecord:
        username_key = self._normalize_identifier(username)
        email_key = self._normalize_identifier(email)
        db = self._get_db()

        with self._lock:
            existing = db.query(
                f"""
                SELECT id
                FROM {self._USERS_TABLE}
                WHERE lower(username) = %(username)s OR lower(email) = %(email)s
                LIMIT 1
                """,
                params={"username": username_key, "email": email_key},
            )
            if (existing or {}).get("rows"):
                raise ValueError("Username or email already exists")

            user_id = secrets.token_urlsafe(16)
            created_at = datetime.now(timezone.utc)
            inserted = db.insert(
                {
                    "sql": f"""
                        INSERT INTO {self._USERS_TABLE} (
                            id,
                            username,
                            email,
                            password_hash,
                            full_name,
                            created_at,
                            updated_at
                        ) VALUES (
                            %(id)s,
                            %(username)s,
                            %(email)s,
                            %(password_hash)s,
                            %(full_name)s,
                            %(created_at)s,
                            %(updated_at)s
                        )
                        RETURNING id, username, email, password_hash, full_name, created_at
                    """,
                    "params": {
                        "id": user_id,
                        "username": username,
                        "email": email,
                        "password_hash": self._hash_password(password),
                        "full_name": full_name,
                        "created_at": created_at,
                        "updated_at": created_at,
                    },
                }
            )
            rows = inserted or []
            if not rows:
                raise RuntimeError("Failed to create user")
            return self._row_to_user(rows[0])

    def authenticate(self, identifier: str, password: str) -> UserRecord:
        row = self._fetch_user_row(identifier)
        if row is None or not self._verify_password(password, row["password_hash"]):
            raise ValueError("Invalid credentials")
        return self._row_to_user(row)

    def issue_token(self, user_id: str, expires_in_hours: int = 24) -> TokenRecord:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        db = self._get_db()

        inserted = db.insert(
            {
                "sql": f"""
                    INSERT INTO {self._TOKENS_TABLE} (
                        token,
                        user_id,
                        expires_at,
                        created_at
                    ) VALUES (
                        %(token)s,
                        %(user_id)s,
                        %(expires_at)s,
                        %(created_at)s
                    )
                    RETURNING token, user_id, expires_at
                """,
                "params": {
                    "token": token,
                    "user_id": user_id,
                    "expires_at": expires_at,
                    "created_at": datetime.now(timezone.utc),
                },
            }
        )
        rows = inserted or []
        if not rows:
            raise RuntimeError("Failed to issue token")

        row = rows[0]
        return TokenRecord(token=row["token"], user_id=row["user_id"], expires_at=row["expires_at"])

    def revoke_token(self, token: str) -> bool:
        result = self._get_db().query(
            f"DELETE FROM {self._TOKENS_TABLE} WHERE token = %(token)s",
            params={"token": token},
        )
        return bool(result and result.get("success"))

    def get_user_by_token(self, token: str) -> Optional[UserRecord]:
        result = self._get_db().query(
            f"""
            SELECT u.id, u.username, u.email, u.password_hash, u.full_name, u.created_at, t.expires_at
            FROM {self._TOKENS_TABLE} t
            JOIN {self._USERS_TABLE} u ON u.id = t.user_id
            WHERE t.token = %(token)s
            LIMIT 1
            """,
            params={"token": token},
        )
        rows = (result or {}).get("rows") or []
        if not rows:
            return None

        row = rows[0]
        if row["expires_at"] <= datetime.now(timezone.utc):
            self.revoke_token(token)
            return None

        return self._row_to_user(row)

    def serialize_user(self, user: UserRecord) -> AuthUserRead:
        return AuthUserRead(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            created_at=user.created_at,
        )


auth_service = AuthService()