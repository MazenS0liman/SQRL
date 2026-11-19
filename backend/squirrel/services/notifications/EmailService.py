#!/usr/bin/python
"""
Email Service
=============

Minimal SMTP sender used by the notification outbox worker.

Kept dependency-free (stdlib ``smtplib`` / ``email``) so the API image does not
need an extra mail SDK. Delivery is synchronous and therefore always called
from a worker task, never from a request handler.
"""
# ——————————————————————————————————————————————————————————————
# Imports

# Standard Libraries
import smtplib
from email.message import EmailMessage
from typing import Optional

# Logging
from loguru import logger

# Settings
from squirrel.core.config import settings


class EmailNotConfiguredError(RuntimeError):
    """Raised when outbound email is enabled but SMTP is not configured."""


class EmailService:
    """Send plain-text (and optional HTML) email over SMTP."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        sender: Optional[str] = None,
        use_tls: Optional[bool] = None,
        use_ssl: Optional[bool] = None,
    ) -> None:
        self.host = host if host is not None else settings.SMTP_HOST
        self.port = port if port is not None else settings.SMTP_PORT
        self.username = username if username is not None else settings.SMTP_USERNAME
        self.password = password if password is not None else settings.SMTP_PASSWORD
        self.sender = sender if sender is not None else settings.SMTP_FROM
        self.use_tls = use_tls if use_tls is not None else settings.SMTP_USE_TLS
        self.use_ssl = use_ssl if use_ssl is not None else settings.SMTP_USE_SSL

    @property
    def is_configured(self) -> bool:
        """Whether enough SMTP settings exist to attempt delivery."""
        return bool(self.host and self.sender)

    def send(
        self,
        to: str,
        subject: str,
        body: str,
        html: Optional[str] = None,
    ) -> None:
        """
        Send one email.

        :param to: Recipient address.
        :param subject: Subject line.
        :param body: Plain-text body.
        :param html: Optional HTML alternative.
        :raises EmailNotConfiguredError: if SMTP settings are incomplete.
        :raises smtplib.SMTPException: on delivery failure.
        """
        if not self.is_configured:
            raise EmailNotConfiguredError(
                "SMTP_HOST and SMTP_FROM must be set to send notification email."
            )
        if not to:
            raise ValueError("A recipient address is required.")

        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if html:
            message.add_alternative(html, subtype="html")

        logger.info("Sending notification email to {} (subject={!r})", to, subject)
        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=30) as client:
                self._login_and_send(client, message)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=30) as client:
                if self.use_tls:
                    client.starttls()
                self._login_and_send(client, message)

    def _login_and_send(
        self,
        client: smtplib.SMTP,
        message: EmailMessage,
    ) -> None:
        if self.username:
            client.login(self.username, self.password)
        client.send_message(message)
