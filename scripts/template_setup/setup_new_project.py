"""Run the whole template-to-project transition in one guided pass.

Walks through the individual setup steps in order, gathering the project name
and GitHub username up front and confirming once before making content changes:

     1. strip template headers
     2. rename the project
     3. set the GitHub username
     4. set the Python version
     5. set the project version    (resets to 0.1.0 for a new project)
     6. reset the changelog         (drops the template's history)
     7. choose a primary shell      (wires Claude Code command hooks)
     8. choose a license            (optional)
     9. report remaining FIXMEs
    10. re-initialize git           (optional, destructive -- confirms separately)
    11. remove this scaffolding     (optional, destructive -- confirms separately)

Each step is also runnable on its own; this just chains them. The destructive
steps prompt for their own confirmation regardless of what you choose here.

Usage:
    uv run scripts/template_setup/setup_new_project.py
"""

from __future__ import annotations

import sys

import _common
import choose_license
import choose_shell
import cleanup
import find_fixmes
import reinit_git
import rename_project
import reset_changelog
import set_github_user
import set_python_version
import set_version
import strip_template_headers


def main() -> None:
    """Run the guided, end-to-end project setup."""
    root = _common.find_root()

    _common.section("New project setup")
    print("  This converts the cloned template into your own project.")
    _common.info("Project root", str(root))

    new_name = _common.prompt_value("New project name (e.g. my-project)")
    gh_user = _common.prompt_value("Your GitHub username")
    if not new_name or not gh_user:
        sys.exit("ERROR: project name and GitHub username are both required.")

    py_version = _common.prompt_value("Python version", default=set_python_version.DEFAULT_VERSION)
    version = _common.prompt_value("Project version", default=set_version.DEFAULT_VERSION)
    shell = choose_shell._prompt_choice()
    want_license = _common.confirm("Choose a license as part of setup?")

    actions = [
        "strip headers",
        "rename project",
        "set GitHub user",
        f"set Python {py_version}",
        f"set version {version}",
        "reset changelog",
        f"wire {shell} hooks",
    ]
    if want_license:
        actions.append("choose license")

    print()
    print("  About to: " + ", ".join(actions) + ".")
    if not _common.confirm("Proceed with these content changes?"):
        sys.exit("Aborted; nothing changed.")

    strip_template_headers.run(root, assume_yes=True)
    rename_project.run(root, new_name, assume_yes=True)
    set_github_user.run(root, gh_user, assume_yes=True)
    set_python_version.run(root, py_version, assume_yes=True)
    set_version.run(root, version, assume_yes=True)
    reset_changelog.run(root, assume_yes=True)
    choose_shell.run(root, shell, assume_yes=True)
    if want_license:
        choose_license.run(root, assume_yes=True)

    # Report what is left to fill in by hand.
    find_fixmes.run(root)

    # Destructive steps: gated here, but each confirms its own details.
    if _common.confirm("\nRe-initialize git (delete history, start fresh)?"):
        reinit_git.run(root)

    if _common.confirm("\nRemove the template-setup scaffolding now?"):
        cleanup.run(root)

    _common.section("Setup complete")
    print("  Review the changes, then write some code!")


if __name__ == "__main__":
    main()
