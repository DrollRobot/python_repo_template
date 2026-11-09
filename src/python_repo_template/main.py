"""Core logic for the python_repo_template package.

Put your main implementation here. Split into more modules (e.g.
`models.py`, `utils.py`) as the package grows -- there is no need to
keep everything in one file.

This module can also be run directly:
    uv run python -m python_repo_template --arg1 value
# <template-only feature="config_system">

Configuration comes from the config system (see
``src/python_repo_template/config/``): non-secret values from the per-user
config.toml, secrets from the user-selected credential backend, with env-var
overrides for CI. The ``--profile`` flag below is the standard shape for
multi-tenant use; keep it even if your project starts single-tenant.
# </template-only>
"""

from __future__ import annotations

import argparse


def main() -> None:
    """Entry point for the command-line interface.

    Parses arguments and calls the core logic. Keep this function thin --
    argument parsing and I/O belong here; business logic belongs in other
    functions that are easy to test without subprocess calls.
    """
    parser = argparse.ArgumentParser(description="FIXME: describe what this tool does")

    # <template-only feature="config_system">
    parser.add_argument(
        "--profile",
        default=None,
        help="Config profile to use (default: default_profile from config.toml).",
    )
    # </template-only>
    # FIXME: replace these arguments with ones that match your use case
    parser.add_argument("--arg1", required=True, help="FIXME: describe this argument")
    parser.add_argument(
        "--arg2",
        type=int,
        default=1,
        help="FIXME: describe this argument (default: 1)",
    )

    args = parser.parse_args()
    # <template-only feature="config_system">

    from python_repo_template.config import load_settings

    # Fails loudly (naming the fix) when required config is missing.
    settings = load_settings(profile=args.profile)
    # </template-only>

    # FIXME: call your core logic here, e.g.
    # <template-only feature="config_system">
    # client = ApiClient(settings.api_url, secret=settings.client_secret)
    # </template-only>
    # <template-only feature="!config_system">
    # client = ApiClient(args.arg1)
    # </template-only>
    print(
        args.arg1,
        args.arg2,
        # <template-only feature="config_system">
        settings.api_url,
        # </template-only>
    )


# This block runs only when the file is executed directly, not when it is
# imported as a module.  Keep it as a one-liner that calls main() so the
# real logic stays in a testable function.
if __name__ == "__main__":
    main()
