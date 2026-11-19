"""Tests for the stdlib SMTP email sender."""
from __future__ import annotations

import pytest

from squirrel.services.notifications.EmailService import (
    EmailNotConfiguredError,
    EmailService,
)


class FakeSMTP:
    """Minimal ``smtplib.SMTP`` stand-in."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.messages: list = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


@pytest.fixture(autouse=True)
def clear_instances() -> None:
    FakeSMTP.instances.clear()


def test_is_configured_requires_host_and_sender() -> None:
    assert EmailService(host="smtp.example", sender="a@b.test").is_configured
    assert not EmailService(host="", sender="a@b.test").is_configured
    assert not EmailService(host="smtp.example", sender="").is_configured


def test_send_without_configuration_raises() -> None:
    service = EmailService(host="", sender="a@b.test")

    with pytest.raises(EmailNotConfiguredError):
        service.send("to@b.test", "Subject", "Body")


def test_send_requires_recipient() -> None:
    service = EmailService(host="smtp.example", sender="a@b.test")

    with pytest.raises(ValueError):
        service.send("", "Subject", "Body")


def test_send_uses_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "squirrel.services.notifications.EmailService.smtplib.SMTP", FakeSMTP
    )
    service = EmailService(
        host="smtp.example",
        port=587,
        username="user",
        password="secret",
        sender="from@b.test",
        use_tls=True,
        use_ssl=False,
    )

    service.send("to@b.test", "Subject", "Body")

    client = FakeSMTP.instances[0]
    assert client.host == "smtp.example"
    assert client.port == 587
    assert client.started_tls is True
    assert client.logged_in == ("user", "secret")
    message = client.messages[0]
    assert message["From"] == "from@b.test"
    assert message["To"] == "to@b.test"
    assert message["Subject"] == "Subject"
    assert "Body" in message.get_content()


def test_send_uses_implicit_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "squirrel.services.notifications.EmailService.smtplib.SMTP_SSL", FakeSMTP
    )
    service = EmailService(
        host="smtp.example",
        port=465,
        sender="from@b.test",
        use_tls=False,
        use_ssl=True,
    )

    service.send("to@b.test", "Subject", "Body")

    client = FakeSMTP.instances[0]
    assert client.port == 465
    assert client.started_tls is False
    assert client.logged_in is None


def test_send_includes_html_alternative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "squirrel.services.notifications.EmailService.smtplib.SMTP", FakeSMTP
    )
    service = EmailService(
        host="smtp.example",
        sender="from@b.test",
        use_tls=False,
        use_ssl=False,
    )

    service.send("to@b.test", "Subject", "Plain body", html="<p>HTML body</p>")

    message = FakeSMTP.instances[0].messages[0]
    assert message.is_multipart()
    content_types = {part.get_content_type() for part in message.iter_parts()}
    assert content_types == {"text/plain", "text/html"}
