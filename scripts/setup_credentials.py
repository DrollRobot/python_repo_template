"""Setup script: Stores secrets in the OS native keyring.

Stores secrets in the OS native keyring rather than on disk in .env.testing.
The .env.testing file holds the keyring key names, not the actual secrets.

Run this once per credential type, then configure .env.testing to use the
stored credentials for tests and dev scripts without Azure KeyVault.

Usage:
    uv run scripts/setup_credentials.py --type user_pass
    uv run scripts/setup_credentials.py --type cert_thumbprint
    uv run scripts/setup_credentials.py --type service_principal

Credential types:
    user_pass         -- username and password
    cert_thumbprint   -- certificate thumbprint (single value)
    service_principal -- tenant ID, client ID, and client secret

Credentials are stored in:
  - Windows: Credential Manager
  - macOS:   Keychain
  - Linux:   Secret Service (e.g. GNOME Keyring, KWallet)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

import keyring

from tests._bootstrap import _KEYRING_SERVICE, load_settings

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _store_user_pass(settings: dict[str, str]) -> None:
    print("Script assumes you have the following in your .env file:")
    print("    CREDENTIAL_BACKEND=keyring")
    print("    CREDENTIAL_TYPE=user_pass")
    print("    USERNAME_KEY=python_repo_template_username")
    print("    PASSWORD_KEY=python_repo_template_password")

    username_key = settings["USERNAME_KEY"]
    password_key = settings["PASSWORD_KEY"]

    print()
    print(f"  Keyring service : {_KEYRING_SERVICE}")
    username = getpass.getpass(f"Enter value for {username_key}: ")
    password = getpass.getpass(f"Enter value for {password_key}: ")

    keyring.set_password(_KEYRING_SERVICE, username_key, username)
    keyring.set_password(_KEYRING_SERVICE, password_key, password)

    print()
    print("Credentials stored.")


def _store_cert_thumbprint(settings: dict[str, str]) -> None:
    thumbprint_key = settings["CERT_THUMBPRINT_KEY"]

    print("Script assumes you have the following in your .env file:")
    print("    CREDENTIAL_BACKEND=keyring")
    print("    CREDENTIAL_TYPE=cert_thumbprint")
    print("    CERT_THUMBPRINT_KEY=python_repo_template_cert_thumbprint")

    print()
    print(f"  Keyring service : {_KEYRING_SERVICE}")
    thumbprint = getpass.getpass(f"Enter value for {thumbprint_key}: ")

    keyring.set_password(_KEYRING_SERVICE, thumbprint_key, thumbprint)

    print()
    print("Credentials stored.")


def _store_service_principal(settings: dict[str, str]) -> None:
    tenant_id_key = settings["TENANT_ID_KEY"]
    client_id_key = settings["CLIENT_ID_KEY"]
    client_secret_key = settings["CLIENT_SECRET_KEY"]

    print("Script assumes you have the following in your .env.testing file:")
    print("    CREDENTIAL_BACKEND=keyring")
    print("    CREDENTIAL_TYPE=service_principal")
    print("    TENANT_ID_KEY=python_repo_template_tenant_id")
    print("    CLIENT_ID_KEY=python_repo_template_client_id")
    print("    CLIENT_SECRET_KEY=python_repo_template_client_secret")

    print()
    print(f"  Keyring service : {_KEYRING_SERVICE}")
    tenant_id = getpass.getpass(f"Enter value for {tenant_id_key}: ")
    client_id = getpass.getpass(f"Enter value for {client_id_key}: ")
    client_secret = getpass.getpass(f"Enter value for {client_secret_key}: ")

    keyring.set_password(_KEYRING_SERVICE, tenant_id_key, tenant_id)
    keyring.set_password(_KEYRING_SERVICE, client_id_key, client_id)
    keyring.set_password(_KEYRING_SERVICE, client_secret_key, client_secret)

    print()
    print("Credentials stored.")


def main() -> None:
    """Parse CLI arguments and store the chosen credential type in the OS keyring."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--type",
        dest="cred_type",
        choices=["user_pass", "cert_thumbprint", "service_principal"],
        required=True,
        help="Type of credentials to store.",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except FileNotFoundError as exc:
        sys.exit(f"ERROR: .env not found at {exc}")

    if args.cred_type == "user_pass":
        _store_user_pass(settings)
    elif args.cred_type == "cert_thumbprint":
        _store_cert_thumbprint(settings)
    elif args.cred_type == "service_principal":
        _store_service_principal(settings)
    else:
        sys.exit(f"ERROR: Unsupported credential type: {args.cred_type}")


if __name__ == "__main__":
    main()
