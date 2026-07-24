"""Module entry point: ``python -m python_repo_template.config``."""

from __future__ import annotations

import sys

from python_repo_template.config.cli import main

if __name__ == "__main__":
    sys.exit(main())
