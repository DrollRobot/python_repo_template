"""Setup script: store Adlumin credentials in the OS native keyring.

Run this once per credential type, then configure .env to use the
stored credentials for tests and dev scripts without Azure KeyVault.

Usage:
    uv run scripts/setup_credentials.py --type userpassotp
    uv run scripts/setup_credentials.py --type session_token

Credential types:
    userpassotp    -- portal username, password, and TOTP seed (full authentication)
    session_token  -- existing _adlumin_session cookie value (skips authentication)

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

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests._bootstrap import _KEYRING_SERVICE, load_settings  # noqa: E402


def _store_userpassotp(settings: dict[str, str]) -> None:
    username_key = settings["ADLUMIN_USERNAME_SECRET_NAME"]
    password_key = settings["ADLUMIN_PASSWORD_SECRET_NAME"]
    totp_key = settings["ADLUMIN_TOTP_SECRET_NAME"]

    print("Storing userpassotp credentials")
    print(f"  Keyring service : {_KEYRING_SERVICE}")
    print(f"  Username key    : {username_key}")
    print(f"  Password key    : {password_key}")
    print(f"  TOTP seed key   : {totp_key}")
    print()

    username = getpass.getpass("Adlumin portal username (email): ")
    password = getpass.getpass("Adlumin portal password: ")
    totp_seed = getpass.getpass("TOTP seed (base32 secret, not a one-time code): ")

    keyring.set_password(_KEYRING_SERVICE, username_key, username)
    keyring.set_password(_KEYRING_SERVICE, password_key, password)
    keyring.set_password(_KEYRING_SERVICE, totp_key, totp_seed)

    print()
    print("Credentials stored. Add these lines to .env:")
    print()
    print("    CREDENTIAL_BACKEND=keyring")
    print("    CREDENTIAL_TYPE=userpassotp")


def _store_session_token(settings: dict[str, str]) -> None:
    token_key = settings["ADLUMIN_SESSION_TOKEN_SECRET_NAME"]

    print("Storing session token")
    print(f"  Keyring service    : {_KEYRING_SERVICE}")
    print(f"  Session token key  : {token_key}")
    print()

    session_token = getpass.getpass("_adlumin_session cookie value: ")

    keyring.set_password(_KEYRING_SERVICE, token_key, session_token)

    print()
    print("Session token stored. Add these lines to .env:")
    print()
    print("    CREDENTIAL_BACKEND=keyring")
    print("    CREDENTIAL_TYPE=session_token")


def main() -> None:
    """Parse CLI arguments and store the chosen credential type in the OS keyring."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--type",
        dest="cred_type",
        choices=["userpassotp", "session_token"],
        required=True,
        help="Type of credentials to store.",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except FileNotFoundError as exc:
        sys.exit(f"ERROR: .env not found at {exc}")

    if args.cred_type == "session_token":
        if "ADLUMIN_SESSION_TOKEN_SECRET_NAME" not in settings:
            sys.exit(
                "ERROR: ADLUMIN_SESSION_TOKEN_SECRET_NAME is not set in .env. "
                "Add it with the key name to use for the session token."
            )
        _store_session_token(settings)
    else:
        _store_userpassotp(settings)


if __name__ == "__main__":
    main()
