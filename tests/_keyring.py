"""
Keyring credential backend (default).

Implements the backend contract used by ``tests/_bootstrap.py`` when
``CREDENTIAL_BACKEND=keyring`` (the default, including when unset). Fetches
secrets from the OS native keyring (Windows Credential Manager, macOS
Keychain, Linux Secret Service). Run
``uv run scripts/setup_credentials.py --type <type>`` once to store them.

To remove entirely: delete this file and ``scripts/setup_credentials.py``,
drop the ``keyring`` line in ``pyproject.toml``'s ``dev`` group, and set
``CREDENTIAL_BACKEND=keyvault`` in ``.env`` (or restore a keyring-based
``.env``). With this file gone there are no ``keyring`` imports anywhere, so
mypy passes without the ``keyring`` package installed.

The ``*_KEY`` env vars hold the secret identifier used by both backends.
"""

from __future__ import annotations

_KEYRING_SERVICE = "python_repo_template"


def get_user_pass(settings: dict[str, str]) -> tuple[str, str]:
    """Retrieve username and password from the OS keyring.

    Returns:
        ``(username, password)``
    """
    import keyring

    username_key = settings["USERNAME_KEY"]
    password_key = settings["PASSWORD_KEY"]
    username = keyring.get_password(_KEYRING_SERVICE, username_key)
    password = keyring.get_password(_KEYRING_SERVICE, password_key)
    if not username or not password:
        raise ValueError(
            f"Credentials not found in keyring (service='{_KEYRING_SERVICE}', "
            f"keys='{username_key}', '{password_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type user_pass' to store them."
        )
    return username, password


def get_cert_thumbprint(settings: dict[str, str]) -> str:
    """Retrieve the certificate thumbprint from the OS keyring.

    Returns:
        Certificate thumbprint string.
    """
    import keyring

    thumbprint_key = settings["CERT_THUMBPRINT_KEY"]
    thumbprint = keyring.get_password(_KEYRING_SERVICE, thumbprint_key)
    if not thumbprint:
        raise ValueError(
            f"Certificate thumbprint not found in keyring "
            f"(service='{_KEYRING_SERVICE}', key='{thumbprint_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type cert_thumbprint' to store it."
        )
    return thumbprint


def get_service_principal(settings: dict[str, str]) -> tuple[str, str, str]:
    """Retrieve service principal credentials from the OS keyring.

    Returns:
        ``(tenant_id, client_id, client_secret)``
    """
    import keyring

    tenant_id_key = settings["TENANT_ID_KEY"]
    client_id_key = settings["CLIENT_ID_KEY"]
    client_secret_key = settings["CLIENT_SECRET_KEY"]
    tenant_id = keyring.get_password(_KEYRING_SERVICE, tenant_id_key)
    client_id = keyring.get_password(_KEYRING_SERVICE, client_id_key)
    client_secret = keyring.get_password(_KEYRING_SERVICE, client_secret_key)
    if not tenant_id or not client_id or not client_secret:
        raise ValueError(
            f"Service principal credentials not found in keyring "
            f"(service='{_KEYRING_SERVICE}', "
            f"keys='{tenant_id_key}', '{client_id_key}', '{client_secret_key}'). "
            "Run 'uv run scripts/setup_credentials.py --type service_principal' to store them."
        )
    return tenant_id, client_id, client_secret
