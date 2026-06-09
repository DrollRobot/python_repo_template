# AGENTS.WORKTREE.md

Instructions for working inside an isolated git worktree. Complements any
repo-level `AGENTS.md`; this file wins on worktree/PR matters.

## Assume

- You're in a worktree the user already opened, on branch `wt/<slug>`, forked
  from and tracking the remote base (normally `origin/develop`) that is the PR
  target — i.e. created with `git worktree add -b wt/<slug> origin/<base>`
  after a fetch.
- `git` and `gh` exist and are authenticated. Never handle credentials yourself.

## Testing

- Run tests as described in [AGENTS.TESTING.md](AGENTS.TESTING.md).

## Commits

- Agents should commit freely, ensuring pre-commit checks pass.
- Commit in logical units with conventional-commit messages
  (`type(scope): summary`). Keep the branch buildable.

## Branching model: git flow

This project uses **git flow**. That means:

- `main` is the release branch. It is **protected** — never commit, merge, or
  push to it directly. Releases land on `main` only through the maintainer's
  release process.
- `develop` is the integration branch. It is the **default base** for all
  feature work and pull requests. Unless the user explicitly says otherwise,
  the PR target is always `develop`, never `main`.
- Feature work happens on `wt/<slug>` branches forked
  from `develop`, and merges back into `develop` via PR.

If you ever find yourself about to push or open a PR against `main`, stop —
that is almost certainly wrong. The target is `develop`.

## Pull Requests

When the user confirms they're ready to complete the worktree and create a pull
request, follow this procedure:

**1. Confirm the base before pushing**
A `-u` push repoints tracking, so we need to be sure.
```
git rev-parse --abbrev-ref '@{u}'
```
The above will output something like origin/develop. The base you would use in
step 3 is 'develop' — the git flow integration branch, and the default target
for this project. Never target `main`.
Still not sure? Ask the user. Never guess the merge target.

**2. Push:**
```
git symbolic-ref --short HEAD | grep -q '^wt/' || { echo "Not on a wt/ branch;
 refusing to push"; exit 1; }
git push -u origin HEAD
```

**3. Open the PR**
Against the base from step 1:
```
gh pr create --base <BASE> --title "<type(scope): summary>" --body-file <file>
```
Add `Closes #N` to the body if the slug encodes an issue. Use `--draft` only if
asked. Write a real description, not `--fill`.

**PR body template**
```
## What
What this change does and why, in a sentence or two.

## Changes
- The meaningful changes, not every commit.

## Testing
- What you ran and the result.

## Notes
- Tradeoffs, follow-ups, risks.

Closes #<issue>   <!-- omit if none -->
```

**4. Report to the user**

The PR URL and a one-line summary, then stop. The user will clean up the worktree.

## Review

- Do not push or open a PR until the user says the worktree is complete ("done",
  "ship it", etc.). Commit freely before then.
- Never merge, approve, enable auto-merge, force-push, or push to the base
  branch. Your job ends at "PR opened, here's the URL."
- Stay in this worktree. Don't touch sibling worktrees, the main checkout, hooks,
  or `.git/info/exclude`.
