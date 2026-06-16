"""Shared pytest fixtures.

Unit tests run without any network, filesystem I/O, or external credentials.

Integration tests (marked @pytest.mark.integration) require a populated
.env file at the project root. Exclude them from the default run with:

    pytest -m "not integration"   # unit tests only
    pytest -m integration         # integration tests only

See AGENTS.md for the list of required environment variables.
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_settings() -> dict[str, str]:
    """Load variables from .env and return them as a plain dict.

    Raises RuntimeError when the file is missing so integration tests fail
    with a clear message instead of a confusing KeyError.
    """
    env_file = ".env"
    if not os.path.exists(env_file):
        raise RuntimeError(
            f"{env_file} not found. "
            "Copy .env.example to .env and fill in the values. "
            "See AGENTS.md for details."
        )
    load_dotenv(env_file, override=True)
    # Return only the variables defined in the file so callers can
    # distinguish "set in .env" from "already in the environment".
    with open(env_file) as fh:
        keys = [
            line.split("=", 1)[0].strip()
            for line in fh
            if line.strip() and not line.startswith("#")
        ]
    return {k: os.environ[k] for k in keys if k in os.environ}


# ---------------------------------------------------------------------------
# FIXME: add project-specific fixtures below
# ---------------------------------------------------------------------------
# Example:
#
# @pytest.fixture(scope="session")
# def client(test_settings: dict[str, str]) -> MyClient:
#     """Return a client configured for integration tests."""
#     return MyClient(api_key=test_settings["MY_API_KEY"])
#
# Credentials via the configured backend (CREDENTIAL_BACKEND in .env: keyring by
# default, keyvault when you flip it). The call is backend-agnostic:
#
# from tests._bootstrap import get_user_pass
#
# @pytest.fixture(scope="session")
# def credentials(test_settings: dict[str, str]) -> tuple[str, str]:
#     """Username/password from keyring (dev) or KeyVault (prod) per .env."""
#     return get_user_pass(test_settings)
