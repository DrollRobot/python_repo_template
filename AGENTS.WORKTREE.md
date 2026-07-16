# AGENTS.WORKTREE.md

Instructions for working inside an isolated git worktree. Complements any
repo-level `AGENTS.md`; this file wins on worktree/PR matters.

## Assume

- You're in a worktree the user already opened, on branch `wt/<slug>`, forked
  from and tracking the remote base (normally `origin/develop`) that is the PR
  target — i.e. created with `git worktree add -b wt/<slug> origin/<base>`
  after a fetch.

## Testing

- Run tests as described in [AGENTS.TESTING.md](AGENTS.TESTING.md).

## Commit
- Commit any untracked files.
- Commit messages should follow Conventional Commits format:
   https://www.conventionalcommits.org/en/v1.0.0/
   And qoomon's commit message style guide:
   https://gist.github.com/qoomon/5dfcdf8eec66a051ecd85625518cfd13
   Fetch both pages at least once every session. Do not rely on memory alone.

## Completing the worktree

Pushing and opening the PR are **not** your job. Do **not** run `git push` or
`gh pr create`. The user will handle that.

When the user says the worktree is done ("done", "ship it", etc.), your job is to
leave it in this state:

**1. Commit everything**
Nothing uncommitted, nothing untracked. Commit all work in logical units with
conventional-commit messages.

**2. Write the PR description to `PR.md`**
- **`PR.md` must open with fenced front-matter that sets the PR title.**
  `complete_worktree.py` reads the title from there; there is no fallback, so a
  missing `title:` aborts the script. The format is a `---` fence, a
  `title:` line, a closing `---` fence, then the body:

  ```
  ---
  title: type(scope): summary
  ---
  <PR body...>
  ```

  Use a clean `type(scope): summary` for the title. The fence lines are metadata
  and are stripped before the body is sent to GitHub, so they never appear in
  the PR.
- For the body, write a real description using
  `.github/PULL_REQUEST_TEMPLATE.md` as a template, but in the style of a human
  summarizing the work, not a checklist for the contributor.
- Add `Closes #N` if the slug encodes an issue. Do not commit `PR.md`.

**3. Stop and tell the user**
Report that everything is committed and `PR.md` is written, then stop. The user
takes it from there.

## Review

- Never push, open a PR, run `scripts/complete_worktree.py`, merge, approve,
  enable auto-merge, force-push, or push to the base branch. Your job ends at
  "everything committed, `PR.md` written." Commit freely before then.
- Stay in this worktree. Don't touch sibling worktrees, the main checkout, hooks,
  or `.git/info/exclude`.
