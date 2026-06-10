"""Create an isolated git worktree on a fresh branch and open it in VS Code.

Intended for running multiple agents in parallel: each gets its own checkout
on its own branch, forked from current upstream. A VS Code workspace is
generated (copied from the repo's existing one where possible), forced to
point only at its own worktree, and kept out of git via the shared
.git/info/exclude.

Usage:
    python scripts/new_worktree.py issue-42
    python scripts/new_worktree.py fix/login develop
    python scripts/new_worktree.py issue-42 --no-bootstrap

Env overrides:
    WT_HOME   — parent dir for worktrees (default: sibling '<repo>-wt' folder)
    WT_BASE   — default base branch (default: develop)
    WT_PREFIX — branch prefix (default: 'wt/')
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

import _cli as cli


def slug_arg(value: str) -> str:
    """Validate the worktree slug.

    Args:
        value: Slug from the command line.

    Returns:
        The validated slug.

    Raises:
        argparse.ArgumentTypeError: If the slug contains disallowed characters.
    """
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
        raise argparse.ArgumentTypeError(
            "slug may only contain letters, digits, and . _ / - characters"
        )
    if ".." in value:
        raise argparse.ArgumentTypeError("slug may not contain '..'")
    return value


def parse_args() -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="Create an isolated git worktree on a fresh branch and open it in VS Code."
    )
    parser.add_argument("slug", type=slug_arg, help="short name for the work, e.g. issue-42")
    parser.add_argument(
        "base",
        nargs="?",
        default=os.environ.get("WT_BASE") or "develop",
        help="branch to fork from (default: $WT_BASE, then develop)",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="skip the per-worktree dependency install",
    )
    return parser.parse_args()


def find_source_workspace(search_dirs: list[Path], exclude_name: str) -> Path | None:
    """Find a workspace file to use as a template.

    Prefers one already in the new worktree (a committed workspace, checked
    out from the branch) over one in the main repo root. Never returns the
    target file itself.

    Args:
        search_dirs: Directories to search, in priority order.
        exclude_name: File name of the workspace being generated.

    Returns:
        The template workspace path, or ``None`` if none was found.
    """
    for directory in search_dirs:
        for hit in sorted(directory.glob("*.code-workspace")):
            if hit.is_file() and hit.name != exclude_name:
                return hit
    return None


def link_or_copy(src: Path, dst: Path, label: str) -> None:
    """Symlink ``dst`` to ``src``, copying instead where symlinks are unavailable.

    Creating symlinks on Windows requires Developer Mode or elevation, so a
    plain copy is the fallback.

    Args:
        src: Existing file to link to.
        dst: Link (or copy) to create.
        label: Short name for progress output.
    """
    try:
        dst.symlink_to(src)
        print(f"Linked {label}")
    except OSError:
        shutil.copyfile(src, dst)
        print(f"Symlink unavailable; copied {label} instead")


def main() -> None:
    """Create the worktree, generate its workspace, bootstrap it, and open VS Code."""
    args = parse_args()

    # --- resolve paths --------------------------------------------------------

    # rev-parse fails fast if not in a repo; --git-common-dir is the shared
    # .git, not the worktree stub, and may come back relative to the cwd.
    repo_root = Path(cli.capture(["git", "rev-parse", "--show-toplevel"]))
    common = cli.capture(["git", "rev-parse", "--git-common-dir"])
    common_dir = Path(common) if Path(common).is_absolute() else (Path.cwd() / common).resolve()
    repo_name = repo_root.name

    prefix = os.environ.get("WT_PREFIX") or "wt/"
    branch = f"{prefix}{args.slug}"
    dir_slug = args.slug.replace("/", "-")
    wt_home = Path(os.environ.get("WT_HOME") or repo_root.parent / f"{repo_name}-wt")
    wt_path = wt_home / dir_slug

    # --- guards -----------------------------------------------------------------

    if wt_path.exists():
        cli.die(f"{wt_path} already exists")
    if cli.capture(["git", "branch", "--list", branch]):
        cli.die(f"branch {branch} already exists")

    # --- create the worktree ----------------------------------------------------

    print("Fetching origin...")
    cli.run(["git", "fetch", "--quiet", "origin"])

    print(f"Creating worktree: {wt_path}  (branch {branch} <- origin/{args.base})")
    wt_home.mkdir(parents=True, exist_ok=True)
    cli.run(["git", "worktree", "add", "-b", branch, str(wt_path), f"origin/{args.base}"])

    # --- generate the workspace -------------------------------------------------

    ws_file = wt_path / f"{dir_slug}.code-workspace"
    src_ws = find_source_workspace([wt_path, repo_root], ws_file.name)

    ws = None
    if src_ws:
        try:
            # NB: VS Code workspace files are often JSONC (// comments,
            # trailing commas), which json.loads rejects. Fall back to a
            # minimal workspace rather than crashing.
            ws = json.loads(src_ws.read_text(encoding="utf-8-sig"))
            print(f"Workspace template: {src_ws}")
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            cli.warn(f"Could not parse {src_ws} ({exc}); generating a minimal workspace.")
            ws = None
    if not isinstance(ws, dict):
        ws = {"settings": {}}

    # Guard: the workspace must point only at this worktree.
    ws["folders"] = [{"path": "."}]
    ws_file.write_text(json.dumps(ws, indent=2) + "\n", encoding="utf-8")

    # Keep generated workspace files out of git. info/exclude lives in the
    # shared .git and is never committed, so this covers every worktree
    # without touching the tracked .gitignore.
    exclude = common_dir / "info" / "exclude"
    pattern = "*.code-workspace"
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if pattern not in existing.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(pattern + "\n")

    # --- bootstrap dependencies -------------------------------------------------

    if not args.no_bootstrap:
        # Link local .vscode/launch.json and settings.json into the worktree.
        # Skip any that already exist: a committed .vscode file is checked out
        # by the worktree, and we don't clobber tracked files.
        vscode_src = repo_root / ".vscode"
        vscode_dst = wt_path / ".vscode"
        for name in ("launch.json", "settings.json"):
            src = vscode_src / name
            dst = vscode_dst / name
            if src.exists() and not dst.exists():
                vscode_dst.mkdir(parents=True, exist_ok=True)
                link_or_copy(src, dst, f".vscode/{name}")

        # Link every .env / .env.* file (testing, production, ...) so each
        # worktree shares the repo's single copy. Skip .env.example templates.
        for env_file in sorted(repo_root.iterdir()):
            name = env_file.name
            if not env_file.is_file():
                continue
            if name != ".env" and not name.startswith(".env."):
                continue
            if name.endswith(".example"):
                continue
            link_or_copy(env_file, wt_path / name, name)

        # uv sync / npm ci
        if (wt_path / "uv.lock").exists() or (wt_path / "pyproject.toml").exists():
            uv = shutil.which("uv")
            if uv:
                print("Bootstrapping: uv sync")
                cli.run([uv, "sync"], cwd=wt_path)
        elif (wt_path / "package-lock.json").exists():
            npm = shutil.which("npm")
            if npm:
                print("Bootstrapping: npm ci")
                cli.run([npm, "ci"], cwd=wt_path)

    # --- open VS Code -----------------------------------------------------------

    code = shutil.which("code")
    if code:
        cli.run([code, str(ws_file)])
    else:
        print(f"VS Code 'code' command not found; open manually: {ws_file}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(130)
