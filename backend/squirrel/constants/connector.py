#!/usr/bin/python
"""
Data Connector Constants
========================

Defines the fixed vocabulary of connector types the Data Connectors page can
create.

Adding a new connector type is: add a ``ConnectorTypeSpec`` to
``CONNECTOR_TYPE_SPECS`` below, and (optionally) a tester function in
``DataConnectorService._TEST_DISPATCH`` if live "Test" support is wanted
right away — untested types simply report a clear "not implemented yet"
error from the Test button rather than silently pretending to succeed.
"""
# ——————————————————————————————————————————————————————————————
# Imports
from __future__ import annotations

# Standard Libraries
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ——————————————————————————————————————————————————————————————
# Connector types

class ConnectorType:
    """
    Valid values for ``DataConnection.type``.
    """
    # Database
    POSTGRES      = "postgres"

    ALL = {POSTGRES}


class ConnectionStatus:
    """
    Valid values for ``DataConnection.status``.
    """

    UNTESTED  = "untested"
    CONNECTED = "connected"
    ERROR     = "error"

    ALL = {UNTESTED, CONNECTED, ERROR}


# ——————————————————————————————————————————————————————————————
# Field specs


@dataclass(frozen=True)
class ConnectorFieldSpec:
    """
    Describes one config field a connector type collects.

    :param key: Key this value is stored under in ``config``.
    :param label: Human-readable label for the form.
    :param type: One of ``"text"``, ``"password"``, ``"number"``, ``"textarea"``.
    :param required: Whether the field must be present on create.
    :param placeholder: Optional form placeholder text.
    :param secret: If ``True``, this field is encrypted at rest and never
        returned in plaintext by the API — on read it comes back blank, and
        on update a blank value means "keep the existing secret" (matching
        the frontend form's "leave blank to keep current value" behaviour).
    """

    key: str
    label: str
    type: str = "text"
    required: bool = True
    placeholder: Optional[str] = None
    secret: bool = False


@dataclass(frozen=True)
class ConnectorTypeSpec:
    id: str
    label: str
    description: str
    fields: List[ConnectorFieldSpec] = field(default_factory=list)

    def field_keys(self) -> List[str]:
        return [f.key for f in self.fields]

    def secret_keys(self) -> List[str]:
        return [f.key for f in self.fields if f.secret]

    def required_keys(self) -> List[str]:
        return [f.key for f in self.fields if f.required]


CONNECTOR_TYPE_SPECS: Dict[str, ConnectorTypeSpec] = {
    ConnectorType.POSTGRES: ConnectorTypeSpec(
        id=ConnectorType.POSTGRES,
        label="Postgres",
        description="Connect to a PostgreSQL database.",
        fields=[
            ConnectorFieldSpec("host", "Host", placeholder="db.example.com"),
            ConnectorFieldSpec("port", "Port", type="number", required=False, placeholder="5432"),
            ConnectorFieldSpec("database", "Database"),
            ConnectorFieldSpec("username", "Username"),
            ConnectorFieldSpec("password", "Password", type="password", secret=True),
        ],
    )
}


def get_connector_type_spec(connector_type: str) -> ConnectorTypeSpec:
    """
    Look up the field spec for a connector type.

    :raises ValueError: if ``connector_type`` isn't a known type.
    """
    spec = CONNECTOR_TYPE_SPECS.get(connector_type)
    if spec is None:
        raise ValueError(f"Unknown connector type '{connector_type}'.")
    return spec