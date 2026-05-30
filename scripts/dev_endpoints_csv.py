"""Dev script: fetch and parse the Adlumin endpoints CSV for a given tenant.

Usage:
    uv run scripts/dev_endpoints_csv.py --tenant "Tenant Display Name"
    uv run scripts/dev_endpoints_csv.py --id 1234
    uv run scripts/dev_endpoints_csv.py --list-tenants
"""

from __future__ import annotations

import argparse
import dataclasses
import json

from _common import add_tenant_args, adlumin_session, switch_tenant

from adlumin_web_tools.pages import parse_endpoints_csv, request_endpoints_csv


def main() -> None:
    """Parse CLI arguments, authenticate, and print endpoint records for the chosen tenant."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_tenant_args(parser)
    args = parser.parse_args()

    with adlumin_session() as (client, _settings):
        switch_tenant(client, args)

        print("Requesting endpoints CSV...")
        text = request_endpoints_csv(client)

        print("Parsing...")
        data = parse_endpoints_csv(text)

        print(json.dumps(dataclasses.asdict(data), indent=2))


if __name__ == "__main__":
    main()
