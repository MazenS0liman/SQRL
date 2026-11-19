"""Tests for the authentication and provider-token services.

Both services talk to PostgreSQL through :class:`PostgresService`, so the tests
drive them with the in-memory :class:`tests.fakes.FakeDB`. That keeps the
password hashing and Fernet round-trips real while never touching a database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from squirrel.core.config import settings
from squirrel.services.auth.AuthService import AuthService, TokenRecord, UserRecord
from squirrel.services.auth.TokenService import TokenService
from tests.fakes import FakeDB


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _user_row(**overrides):
    row = {
        "id": "user-1",
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": AuthService._hash_password("correct horse"),
        "full_name": "Alice",
        "created_at": NOW,
    }
    row.update(overrides)
    return row


# ——————————————————————————————————————————————————————————————
# AuthService — password primitives


def test_password_hash_round_trip() -> None:
    password_hash = AuthService._hash_password("s3cret")

    assert password_hash.startswith("pbkdf2_sha256$390000$")
    assert AuthService._verify_password("s3cret", password_hash) is True
    assert AuthService._verify_password("wrong", password_hash) is False


def test_password_hash_uses_supplied_salt() -> None:
    salt = b"0123456789abcdef"

    first = AuthService._hash_password("s3cret", salt=salt)
    second = AuthService._hash_password("s3cret", salt=salt)

    assert first == second


@pytest.mark.parametrize(
    "malformed",
    ["", "not-a-hash", "bcrypt$10$salt$hash", "pbkdf2_sha256$abc$notb64$notb64"],
)
def test_verify_password_rejects_malformed_hashes(malformed: str) -> None:
    assert AuthService._verify_password("s3cret", malformed) is False


def test_normalize_identifier_trims_and_lowercases() -> None:
    assert AuthService._normalize_identifier("  Alice@Example.COM ") == "alice@example.com"


def test_row_to_user_maps_optional_full_name() -> None:
    record = AuthService._row_to_user(_user_row(full_name=None))

    assert isinstance(record, UserRecord)
    assert record.id == "user-1"
    assert record.full_name is None


# ——————————————————————————————————————————————————————————————
# AuthService — schema bootstrap


def test_get_db_creates_tables_once() -> None:
    db = FakeDB()
    service = AuthService(db=db)

    service._get_db()
    service._get_db()

    assert len(db.executes) == 2
    assert "auth_users" in db.executes[0].sql
    assert "auth_tokens" in db.executes[1].sql


def test_default_admin_is_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CREATE_DEFAULT_ADMIN", False)
    db = FakeDB()

    AuthService(db=db)._get_db()

    assert db.inserts == []


def test_default_admin_requires_a_password(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CREATE_DEFAULT_ADMIN", True)
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "")

    with pytest.raises(RuntimeError, match="DEFAULT_ADMIN_PASSWORD"):
        AuthService(db=FakeDB())._get_db()


def test_default_admin_is_created_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CREATE_DEFAULT_ADMIN", True)
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "admin-password")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_USERNAME", "admin")
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_EMAIL", "admin@example.com")
    db = FakeDB(results=[{"rows": []}])

    AuthService(db=db)._get_db()

    assert len(db.inserts) == 1
    params = db.inserts[0].params
    assert params["username"] == "admin"
    assert params["email"] == "admin@example.com"
    assert AuthService._verify_password("admin-password", params["password_hash"])


def test_default_admin_is_skipped_when_present(monkeypatch) -> None:
    monkeypatch.setattr(settings, "CREATE_DEFAULT_ADMIN", True)
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "admin-password")
    db = FakeDB(results=[{"rows": [{"id": "existing"}]}])

    AuthService(db=db)._get_db()

    assert db.inserts == []


# ——————————————————————————————————————————————————————————————
# AuthService — users and tokens


def test_register_user_inserts_and_returns_record() -> None:
    db = FakeDB(insert_results=[[_user_row()]])
    service = AuthService(db=db)

    user = service.register_user("Alice", "Alice@Example.com", "correct horse", "Alice")

    assert user.id == "user-1"
    assert user.username == "alice"
    # The duplicate check normalises both identifiers.
    assert db.queries[0].params == {
        "username": "alice",
        "email": "alice@example.com",
    }
    assert AuthService._verify_password("correct horse", db.inserts[0].params["password_hash"])


def test_register_user_rejects_duplicates() -> None:
    db = FakeDB(results=[{"rows": [{"id": "user-1"}]}])

    with pytest.raises(ValueError, match="already exists"):
        AuthService(db=db).register_user("alice", "alice@example.com", "pw")

    assert db.inserts == []


def test_register_user_raises_when_insert_returns_nothing() -> None:
    with pytest.raises(RuntimeError, match="Failed to create user"):
        AuthService(db=FakeDB(insert_results=[[]])).register_user(
            "alice", "alice@example.com", "pw"
        )


def test_authenticate_accepts_identifier_and_password() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}])

    user = AuthService(db=db).authenticate("  ALICE ", "correct horse")

    assert user.id == "user-1"
    assert db.queries[0].params == {"identifier": "alice"}


def test_authenticate_rejects_unknown_user_and_bad_password() -> None:
    with pytest.raises(ValueError, match="Invalid credentials"):
        AuthService(db=FakeDB(results=[{"rows": []}])).authenticate("alice", "pw")

    with pytest.raises(ValueError, match="Invalid credentials"):
        AuthService(db=FakeDB(results=[{"rows": [_user_row()]}])).authenticate(
            "alice", "wrong"
        )


def test_issue_token_returns_record() -> None:
    expires_at = NOW + timedelta(hours=24)
    db = FakeDB(
        insert_results=[[{"token": "tok", "user_id": "user-1", "expires_at": expires_at}]]
    )

    token = AuthService(db=db).issue_token("user-1", expires_in_hours=24)

    assert isinstance(token, TokenRecord)
    assert token.token == "tok"
    assert token.user_id == "user-1"
    assert token.expires_at == expires_at
    assert db.inserts[0].params["expires_at"] > db.inserts[0].params["created_at"]


def test_issue_token_raises_when_insert_returns_nothing() -> None:
    with pytest.raises(RuntimeError, match="Failed to issue token"):
        AuthService(db=FakeDB(insert_results=[[]])).issue_token("user-1")


def test_revoke_token_reports_success() -> None:
    assert AuthService(db=FakeDB(results=[{"success": True}])).revoke_token("tok") is True
    assert AuthService(db=FakeDB(results=[{"success": False}])).revoke_token("tok") is False


def test_get_user_by_token_returns_user() -> None:
    row = _user_row(expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    db = FakeDB(results=[{"rows": [row]}])

    user = AuthService(db=db).get_user_by_token("tok")

    assert user is not None
    assert user.id == "user-1"
    assert db.queries[0].params == {"token": "tok"}


def test_get_user_by_token_returns_none_when_missing() -> None:
    assert AuthService(db=FakeDB(results=[{"rows": []}])).get_user_by_token("tok") is None


def test_get_user_by_token_revokes_expired_tokens() -> None:
    row = _user_row(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db = FakeDB(results=[{"rows": [row]}, {"success": True}])

    assert AuthService(db=db).get_user_by_token("tok") is None
    assert "DELETE FROM auth_tokens" in db.queries[1].sql


def test_serialize_user_returns_api_model() -> None:
    user = AuthService._row_to_user(_user_row())

    payload = AuthService(db=FakeDB()).serialize_user(user)

    assert payload.id == "user-1"
    assert payload.username == "alice"
    assert payload.full_name == "Alice"


# ——————————————————————————————————————————————————————————————
# AuthService — profile and password management


def test_get_user_returns_record() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}])

    user = AuthService(db=db).get_user("user-1")

    assert user is not None
    assert user.id == "user-1"
    assert db.queries[0].params == {"user_id": "user-1"}


def test_get_user_returns_none_when_missing() -> None:
    assert AuthService(db=FakeDB(results=[{"rows": []}])).get_user("ghost") is None


def test_update_profile_writes_name_and_email() -> None:
    updated = _user_row(email="new@example.com", full_name="Alice B.")
    db = FakeDB(results=[{"rows": []}, {"rows": [updated]}])

    user = AuthService(db=db).update_profile(
        "user-1", full_name="  Alice B.  ", email="New@Example.com"
    )

    assert user.email == "new@example.com"
    assert user.full_name == "Alice B."
    update = db.queries[1]
    assert "UPDATE auth_users" in update.sql
    assert update.params["full_name"] == "Alice B."
    assert update.params["email"] == "new@example.com"
    assert update.params["user_id"] == "user-1"


def test_update_profile_rejects_an_email_taken_by_another_user() -> None:
    db = FakeDB(results=[{"rows": [{"id": "someone-else"}]}])

    with pytest.raises(ValueError, match="already in use"):
        AuthService(db=db).update_profile("user-1", email="taken@example.com")

    # The availability check ran, but no UPDATE was attempted.
    assert len(db.queries) == 1


def test_update_profile_without_fields_returns_the_current_user() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}])

    user = AuthService(db=db).update_profile("user-1")

    assert user.id == "user-1"
    assert len(db.queries) == 1


def test_update_profile_missing_user_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        AuthService(db=FakeDB(results=[{"rows": []}])).update_profile(
            "ghost", full_name="Nobody"
        )


def test_change_password_rewrites_the_hash() -> None:
    new_hash = AuthService._hash_password("a much better password")
    db = FakeDB(results=[{"rows": [_user_row()]}, {"rows": [_user_row(password_hash=new_hash)]}])

    user = AuthService(db=db).change_password(
        "user-1", "correct horse", "a much better password"
    )

    assert AuthService._verify_password("a much better password", user.password_hash)
    assert db.queries[1].params["user_id"] == "user-1"


def test_change_password_rejects_a_wrong_current_password() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}])

    with pytest.raises(ValueError, match="Current password"):
        AuthService(db=db).change_password("user-1", "wrong password", "a much better password")

    assert len(db.queries) == 1


def test_change_password_rejects_reusing_the_current_password() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}])

    with pytest.raises(ValueError, match="different"):
        AuthService(db=db).change_password("user-1", "correct horse", "correct horse")


def test_change_password_missing_user_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        AuthService(db=FakeDB(results=[{"rows": []}])).change_password(
            "ghost", "correct horse", "a much better password"
        )


def test_change_password_raises_when_update_returns_nothing() -> None:
    db = FakeDB(results=[{"rows": [_user_row()]}, {"rows": []}])

    with pytest.raises(ValueError, match="not found"):
        AuthService(db=db).change_password("user-1", "correct horse", "a much better password")


def test_revoke_all_tokens_scopes_to_the_user() -> None:
    db = FakeDB(results=[{"success": True}])

    assert AuthService(db=db).revoke_all_tokens("user-1") is True
    assert "DELETE FROM auth_tokens" in db.queries[0].sql
    assert db.queries[0].params == {"user_id": "user-1"}


# ——————————————————————————————————————————————————————————————
# TokenService


@pytest.fixture()
def fernet_key(monkeypatch) -> str:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", key)
    return key


def _token_row(ciphertext: str, **overrides):
    row = {
        "id": "token-1",
        "provider": "openai",
        "label": "default",
        "ciphertext": ciphertext,
        "created_at": NOW,
        "last_used_at": None,
    }
    row.update(overrides)
    return row


def test_mask_long_and_short_tokens() -> None:
    assert TokenService._mask("sk-abcdefghijklmnop") == "sk-abc...klmnop"
    assert TokenService._mask("short") == "•••rt"
    assert TokenService._mask("ab") == "ab"


def test_encrypt_decrypt_round_trip(fernet_key: str) -> None:
    service = TokenService(db=FakeDB())

    ciphertext = service._encrypt("plaintext-secret")

    assert ciphertext != "plaintext-secret"
    assert service._decrypt(ciphertext) == "plaintext-secret"


def test_decrypt_rejects_foreign_ciphertext(fernet_key: str) -> None:
    foreign = Fernet(Fernet.generate_key()).encrypt(b"other").decode("ascii")

    with pytest.raises(RuntimeError, match="could not be decrypted"):
        TokenService(db=FakeDB())._decrypt(foreign)


def test_missing_encryption_key_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TOKEN_ENCRYPTION_KEY", "")

    with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY"):
        TokenService(db=FakeDB())._encrypt("secret")


def test_token_tables_are_created_once() -> None:
    db = FakeDB()
    service = TokenService(db=db)

    service._get_db()
    service._get_db()

    assert len(db.executes) == 1
    assert "user_provider_tokens" in db.executes[0].sql


def test_add_token_inserts_new_row(fernet_key: str) -> None:
    ciphertext = Fernet(fernet_key.encode()).encrypt(b"sk-secret-token").decode("ascii")
    db = FakeDB(insert_results=[[_token_row(ciphertext)]])
    service = TokenService(db=db)

    result = service.add_token("user-1", "openai", "sk-secret-token", "default")

    assert result.id == "token-1"
    assert result.provider == "openai"
    assert result.label == "default"
    assert result.token_preview == "sk-sec...-token"
    assert db.inserts[0].params["ciphertext"] != "sk-secret-token"


def test_add_token_updates_existing_row(fernet_key: str) -> None:
    ciphertext = Fernet(fernet_key.encode()).encrypt(b"sk-new-token").decode("ascii")
    db = FakeDB(
        results=[
            {"rows": [{"id": "token-1"}]},
            {"rows": [_token_row(ciphertext, label=None)]},
        ]
    )
    service = TokenService(db=db)

    result = service.add_token("user-1", "openai", "sk-new-token")

    assert result.id == "token-1"
    assert db.inserts == []
    assert "UPDATE user_provider_tokens" in db.queries[1].sql


def test_add_token_raises_when_nothing_is_returned() -> None:
    with pytest.raises(RuntimeError, match="Failed to save token"):
        TokenService(db=FakeDB(insert_results=[[]])).add_token(
            "user-1", "openai", "sk-token"
        )


def test_list_tokens_masks_every_row(fernet_key: str) -> None:
    ciphertext = Fernet(fernet_key.encode()).encrypt(b"sk-list-token").decode("ascii")
    db = FakeDB(results=[{"rows": [_token_row(ciphertext)]}])

    tokens = TokenService(db=db).list_tokens("user-1")

    assert len(tokens) == 1
    assert tokens[0].token_preview == "sk-lis...-token"
    assert db.queries[0].params == {"user_id": "user-1"}


def test_delete_token_reports_success() -> None:
    assert TokenService(db=FakeDB(results=[{"success": True}])).delete_token(
        "user-1", "token-1"
    ) is True
    assert TokenService(db=FakeDB(results=[{"success": False}])).delete_token(
        "user-1", "token-1"
    ) is False


def test_get_decrypted_token_returns_plaintext_and_stamps_usage(fernet_key: str) -> None:
    ciphertext = Fernet(fernet_key.encode()).encrypt(b"sk-used-token").decode("ascii")
    db = FakeDB(results=[{"rows": [{"id": "token-1", "ciphertext": ciphertext}]}])

    plaintext = TokenService(db=db).get_decrypted_token("user-1", "openai")

    assert plaintext == "sk-used-token"
    assert len(db.executes) == 2  # create table + last_used_at update
    assert "last_used_at" in db.executes[-1].sql


def test_get_decrypted_token_returns_none_when_absent() -> None:
    db = FakeDB(results=[{"rows": []}])

    assert TokenService(db=db).get_decrypted_token("user-1", "openai") is None
    assert len(db.executes) == 1  # only the table bootstrap
