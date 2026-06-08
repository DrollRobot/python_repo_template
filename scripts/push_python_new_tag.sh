#!/usr/bin/env bash
#
# release.sh — Merge the current branch into main, bump the version, tag, and push.
#
# Usage:
#   ./release.sh <bump-level>
#
# Arguments:
#   bump-level      Semantic version bump: patch | minor | major
#                     patch — bug fixes only           (1.4.2 -> 1.4.3)
#                     minor — new features, no breaks  (1.4.2 -> 1.5.0)
#                     major — breaking changes         (1.4.2 -> 2.0.0)
#
# Examples:
#   ./release.sh patch
#   ./release.sh minor
#   ./release.sh major
#
# What it does:
#   1. Detects the current branch (must not be main).
#   2. Switches to main and merges the current branch in.
#   3. Runs `uv version --bump <level>` to update pyproject.toml and uv.lock.
#   4. Commits the version bump and creates an annotated tag (v<version>).
#   5. Pushes main and tags to origin.
#   6. Switches back to the source branch, merges main, and pushes it.
#
# Requirements:
#   - Run from inside the source branch with a clean working tree.
#   - `uv` installed and the project uses uv for version management.
#   - Push access to origin for both main and the source branch.

set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <bump-level>" >&2
    echo "  bump-level: patch | minor | major" >&2
    exit 1
fi

BUMP="$1"

case "$BUMP" in
    patch|minor|major) ;;
    *)
        echo "Error: bump-level must be patch, minor, or major (got: $BUMP)" >&2
        exit 1
        ;;
esac

SOURCE=$(git symbolic-ref --short HEAD 2>/dev/null) || {
    echo "Error: not on a branch (detached HEAD?)" >&2
    exit 1
}

if [[ "$SOURCE" == "main" ]]; then
    echo "Error: already on main; switch to the source branch first" >&2
    exit 1
fi

if ! git diff-index --quiet HEAD --; then
    echo "Error: working tree is not clean; commit or stash changes first" >&2
    exit 1
fi

git switch main
git merge "$SOURCE"

uv version --bump "$BUMP"

VERSION=$(uv version --short)
git add .
git commit -m "Release v${VERSION}"
git tag -a "v${VERSION}" -m "Release ${VERSION}"
git push origin main
git push origin --tags

git switch "$SOURCE"
git merge main
git push origin "$SOURCE"