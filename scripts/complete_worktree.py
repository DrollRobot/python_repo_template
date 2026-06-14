"""Interactively complete a worktree: verify, push, and open a pull request.

Picks up where the agent leaves off. Once the feature/fix is committed and a
PR description has been written to PR.md, this walks through the remaining
steps one at a time. Before each action it shows what is about to happen and
prompts for confirmation (y/n); answering 'n' aborts without taking the
remaining steps. The output of every git and gh command is shown.

The procedure mirrors AGENTS.WORKTREE.md:
  1. Confirm we are on a wt/ branch in a worktree (never main/develop).
  2. Verify the working tree is clean — everything is committed. PR.md itself
     is exempt; it may stay uncommitted since it only feeds `gh pr create`.
  3. Resolve the PR base from the branch's UPSTREAM *before* pushing, since
     `git push -u` repoints tracking. Refuses to target main.
  4. Show the PR.md body and confirm the title.
  5. Push the branch with -u.
  6. Open the PR with `gh pr create --base <base> --body-file PR.md`.
  7. Report the PR URL and stop. The worktree is NOT cleaned up — that is
     left to the user, per AGENTS.WORKTREE.md.

Pass -y/--yes to answer every prompt with 'y' for non-interactive use.

Usage:
    python scripts/complete_worktree.py
    python scripts/complete_worktree.py --title "feat(auth): add SSO login" --draft
    python scripts/complete_worktree.py -y

Requirements:
    - Run from inside the worktree, on a wt/ branch with all work committed
      (PR.md itself does not need to be committed).
    - `git` and `gh` installed and authenticated.
    - A PR.md body file written by the agent at the worktree root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _cli as cli

# Version of this helper script itself. Bump on every change so copies in other
# repos can be compared: patch = bugfix, minor = new flag/behavior, major =
# breaking CLI change.
__version__ = "1.0.0"


def dirty_status_lines(status_lines: list[str], exempt_path: str | None) -> list[str]:
    """Filter ``git status --porcelain`` lines, ignoring the exempt PR body file.

    Args:
        status_lines: Output lines from ``git status --porcelain``.
        exempt_path: Repo-root-relative POSIX path allowed to stay uncommitted
            (the PR body file), or ``None`` when no path is exempt.

    Returns:
        The lines describing changes to any file other than ``exempt_path``.
    """
    dirty: list[str] = []
    for line in status_lines:
        if not line.strip():
            continue
        # Porcelain v1: two status letters, a space, then the path. Paths with
        # special characters are quoted; the plain strip covers the simple case.
        path = line[3:].strip().strip('"')
        if exempt_path is not None and path == exempt_path:
            continue
        dirty.append(line)
    return dirty


def parse_args() -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Complete a worktree: verify it is committed, push the branch, and open a PR."
    )
    parser.add_argument(
        "--title",
        help="PR title (default: subject of the last commit; always confirmed interactively)",
    )
    parser.add_argument(
        "--base",
        help="override the PR base branch (default: read from the branch's upstream)",
    )
    parser.add_argument(
        "--body-file",
        default="PR.md",
        help="path to the PR body file (default: PR.md at the worktree root)",
    )
    parser.add_argument("--draft", action="store_true", help="open the PR as a draft")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="assume 'yes' to every confirmation prompt (non-interactive)",
    )
    return parser.parse_args()


def main() -> None:
    """Run the interactive verify-push-PR flow."""
    args = parse_args()
    cli.set_assume_yes(args.yes)

    cli.info("Script version", __version__)
    print("")

    # --- gather state ----------------------------------------------------------

    cli.section("Worktree setup")

    branch = cli.capture_ok(["git", "symbolic-ref", "--short", "HEAD"])
    if not branch:
        cli.die("Not on a branch (detached HEAD?). Check out the wt/ branch first.")

    if branch in ("main", "master", "develop", "dev"):
        cli.die(
            f"On '{branch}'; this script is for wt/ feature branches, "
            "not the integration/release branch."
        )
    if not branch.startswith("wt/"):
        cli.warn(f"  Warning: branch '{branch}' does not look like a wt/ branch.")
        if not cli.confirm("  Continue anyway?"):
            sys.exit(1)

    repo_root = Path(cli.capture(["git", "rev-parse", "--show-toplevel"]))

    # Resolve the PR base. Read it from the branch's configured upstream,
    # because a later `git push -u` will repoint tracking to origin/<branch>
    # and lose it.
    base = args.base
    if not base:
        merge = cli.capture_ok(["git", "config", f"branch.{branch}.merge"])
        if not merge:
            cli.warn(f"  No upstream configured for '{branch}'.")
            base = cli.prompt_value("  Enter the PR base branch", default="develop")
        else:
            base = merge.removeprefix("refs/heads/")
        if base in ("main", "master"):
            cli.die(
                f"Refusing to target '{base}'. This project uses git flow; PRs go to "
                "develop. Pass --base to override deliberately."
            )

    cli.info("Worktree", str(repo_root))
    cli.info("Source branch", branch)
    cli.info("PR base", base)

    # --- PR body ---------------------------------------------------------------

    cli.section("PR body")
    body_path = Path(args.body_file)
    if not body_path.is_absolute():
        body_path = repo_root / body_path

    if not body_path.exists():
        cli.die(
            f"PR body file not found: {body_path}. "
            "Have the agent write the PR description to PR.md first."
        )
    body_text = body_path.read_text(encoding="utf-8-sig")
    if not body_text.strip():
        cli.die(f"PR body file is empty: {body_path}.")
    cli.info("Body file", str(body_path))
    print()
    print(f"{cli.GRAY}{body_text.rstrip()}{cli.RESET}")

    # --- title -----------------------------------------------------------------

    title = args.title or cli.capture(["git", "log", "-1", "--pretty=%s"])
    cli.section("PR title")
    title = cli.prompt_value("Confirm or edit the PR title", default=title)
    if not title.strip():
        cli.die("PR title cannot be empty.")

    # --- working tree status ---------------------------------------------------

    cli.section("Working tree status")
    cli.run(["git", "status", "--short", "--branch"])

    # The PR body file only feeds `gh pr create`, so it may stay uncommitted;
    # exempt it from the clean-tree check (when it lives inside the worktree).
    try:
        exempt = body_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        exempt = None
    status_lines = cli.capture(["git", "status", "--porcelain"]).splitlines()
    dirty = dirty_status_lines(status_lines, exempt)
    if dirty:
        print()
        cli.warn(
            f"  Working tree is not clean. Commit everything except {body_path.name} "
            "before completing the worktree."
        )
        cli.die("Uncommitted changes present; refusing to push.")
    cli.success(f"  Working tree is clean; all changes committed ({body_path.name} is exempt).")

    # --- existing PR guard -----------------------------------------------------

    cli.section("Existing PR check")
    existing_url = cli.capture_ok(["gh", "pr", "view", branch, "--json", "url", "--jq", ".url"])
    if existing_url:
        cli.warn(f"  A pull request already exists for '{branch}':")
        print(f"  {existing_url}")
        cli.warn("  Pushing will update it; a new PR will not be created.")
    else:
        cli.success("  No existing PR for this branch.")

    # --- push ------------------------------------------------------------------

    cli.section("Step: push branch")
    cli.step(f"Push '{branch}' to origin (with -u)?")
    cli.run(["git", "push", "-u", "origin", "HEAD"])

    # --- open PR ---------------------------------------------------------------

    if existing_url:
        cli.section("Done")
        cli.success("  Branch pushed; existing PR updated.")
        cli.info("PR", existing_url)
        cli.info("Current branch", cli.capture(["git", "branch", "--show-current"]))
        print()
        print(f"  {cli.GRAY}Worktree left in place for you to clean up.{cli.RESET}")
        return

    cli.section("Step: open pull request")
    draft_note = " (draft)" if args.draft else ""
    cli.step(f"Open a PR from '{branch}' into '{base}'?{draft_note}")

    create_args = ["gh", "pr", "create", "--base", base, "--title", title]
    create_args += ["--body-file", str(body_path)]
    if args.draft:
        create_args.append("--draft")
    # Capture stdout for the URL; gh's progress messages stream on stderr.
    output = cli.capture(create_args, echo_cmd=True)
    print(output)
    pr_url = output.splitlines()[-1].strip() if output else "(unknown)"

    # --- done ------------------------------------------------------------------

    cli.section("Done")
    cli.success("  Pull request opened.")
    cli.info("PR", pr_url)
    cli.info("Base", base)
    cli.info("Current branch", cli.capture(["git", "branch", "--show-current"]))
    print()
    print(f"  {cli.GRAY}Worktree left in place for you to clean up.{cli.RESET}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
