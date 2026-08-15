# Repository Instructions

These instructions apply to the entire repository. More specific `AGENTS.md`
files may add stricter rules for their own directories, but they must not relax
the safety, authorship, signing, history, or release rules below.

## Working Principles

- Preserve user changes and unrelated work. Do not silently stash, reset,
  rewrite, absorb, format, or clean changes outside the current task.
- Do not run destructive Git or filesystem commands without explicit approval.
- Do not expose tokens, proxy credentials, private keys, databases, backups, or
  other secrets in source, logs, commits, or responses.
- Keep a task focused. Update tests and documentation when behavior, commands,
  configuration, dependencies, or migrations change.
- Do not push, open a pull request, publish a tag, or otherwise mutate a remote
  unless the user explicitly authorizes that exact remote action.

## Task Branches

Every task, including small fixes and documentation-only work, must use a new
local branch created from the intended integration branch.

- Name ordinary branches `type/short-kebab`.
- Use one of the commit types listed under **Commits** as `type`.
- Write the slug with lowercase ASCII letters, digits, and hyphens. Prefer two
  to six words and keep the complete branch name at or below 48 characters.
- WIP and fixup commits are allowed only on the task branch.
- After successful validation, squash an ordinary task into one clean, signed
  commit on local `master`, verify it, and then delete the local task branch.
- If local `master` moves during the task, stop and ask the user how to proceed.
  Do not automatically rebase, merge the new `master`, or transplant a squash.
- Never force-push or rewrite published history.

Do not run `git fetch` without asking the user each time. Before the first
remote action in an environment, ask for the GitHub username and confirm the
remote and exact target branch. A bare request such as "push" is ambiguous:
ask for the remote and target branch before acting.

## Large Tasks and Subtasks

A task is large and must be decomposed when any of these conditions is true:

- it touches three or more subsystems;
- it combines schema or migration work with a behavior change;
- it contains independently implementable parts; or
- it is expected to require more than one final commit.

Before implementing a large task, present a working plan containing its goals,
dependency graph, proposed branch names, and acceptance criteria. Use a parent
integration branch and branch each subtask from it as
`type/parent-subtask`, keeping the complete name at or below 48 characters.

- Run independent subtasks in parallel when doing so is safe and workers are
  available. Run dependent subtasks sequentially. If no workers are available,
  perform the same decomposition sequentially.
- Give every writing worker its own temporary Git worktree and subtask branch.
  Never let multiple workers write in the same worktree.
- Assign clear file ownership and acceptance criteria to each worker. The
  integration owner coordinates dependencies and resolves only in-scope
  conflicts.
- Validate each subtask, then squash it into the integration branch as one
  clean, signed Conventional Commit and verify the signature.
- Validate the complete integration branch, then fast-forward it into local
  `master`. Preserve the separate final subtask commits; this is the explicit
  exception to the one-task/one-squash rule.
- Remove temporary worktrees and local branches only after successful
  integration.

## Commits

Use English Conventional Commit messages in this form:

```text
type(scope)!: imperative lowercase description
```

- Allowed types: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`,
  `ci`, `chore`, and `revert`.
- Scope is optional. Allowed scopes are `bank`, `poker`, `stats`, `db`,
  `admin`, `runtime`, `deploy`, `ux`, and `deps`. Ask before introducing a new
  scope.
- Use an imperative, lowercase subject without a trailing period. Limit the
  full subject line to 72 characters.
- Wrap body lines at 100 characters and keep the complete body at or below 800
  characters.
- A final squash body should concisely cover the changes, improvements,
  migrations, risks, and validation performed.
- If necessary detail does not fit, put it in `CHANGELOG.md` and refer to that
  section in normal commit prose.
- Add `Fixes #...` or `Refs #...` only for an issue explicitly provided by the
  user or otherwise verified. Other trailers require explicit approval.
- Never add AI or agent attribution trailers, including `Generated-by`,
  `Assisted-by`, or an AI `Co-authored-by`, unless the user explicitly asks for
  that exact attribution.

Breaking changes and irreversible migrations require explicit approval before
implementation. Mark them with `!`, add an accurate `BREAKING CHANGE:` footer,
and document them in `CHANGELOG.md`. This footer is the required exception to
the issue-only trailer rule.

## Author Identity and Signing

Final commits, release commits, and release tags must be signed. Before the
first commit in every new environment or worktree, and whenever repository-local
identity is absent or has changed, ask the user for all of the following:

- Git author name (`user.name`);
- Git author email (`user.email`);
- signing backend (`ssh` or OpenPGP);
- signing key path or identifier; and
- the verification principal and public key needed for local verification.

Do not infer these values from global Git configuration, remotes, or commit
history. Store approved values only in repository-local Git configuration.
Never put concrete identity or key values in tracked project files.

Do not fall back to an unsigned final commit. If signing is unavailable, stop
and ask the user. Verify every final commit with `git verify-commit` before
integration. For SSH signing, configure an untracked allowed-signers file under
`.git` and use it for local verification.

## Validation

For code changes, run all of the following from the repository root:

```text
uv run ruff format --check .
uv run ruff check .
pyright
uv run pytest
git diff --check
```

For documentation-only changes, perform a content and Markdown review and run
`git diff --check`; the Python suite is not required.

A failing required check blocks integration. If a failure is demonstrably
pre-existing, show the before-and-after evidence and ask the user before
integrating anyway. For a large task, run targeted checks for every subtask and
the complete required suite on the integration branch.

## Project Safeguards

- Keep the project on Python 3.13 and use its locked uv/Nix workflow. Do not
  modify lock files unless the task intentionally changes dependencies or the
  toolchain.
- Change the database schema through idempotent Alembic migrations. Never edit
  a production SQLite database manually.
- Preserve atomic economy updates across player state, corporation state,
  ledger entries, and events.
- Keep Telegram callback data within 64 bytes, escape Telegram HTML, and expose
  private game data only in an authorized private context.

## Changelog

Use Keep a Changelog structure with an `[Unreleased]` section and the
categories `Added`, `Changed`, `Fixed`, and `Security`. Record user-facing
changes, important internal decisions, migrations, and material risks. Create
`CHANGELOG.md` only when a substantive future change or release needs it; do
not create it solely for repository-policy documentation.

## Versioning and Releases

Use Semantic Versioning 2.0, but never change a version, create a release
commit, or create a tag without explicit user approval.

Before `1.0.0`, normally recommend:

- patch for a compatible bug fix;
- minor for a backward-compatible feature; and
- the next minor for a breaking change.

At and after `1.0.0`, use standard major/minor/patch semantics. Recommend a
release only after a completed integration task when accumulated changes
justify one. Find the latest reachable, verified, signed `v*` tag and assess all
commits and diffs since it, including user-visible behavior, migrations,
configuration, and operational changes. Present the exact proposed version and
rationale, then wait for approval.

If no release tag exists, ask whether to establish a selected current commit as
`v0.1.0` or choose a new first release version. Do not silently treat current
`master` or the version in `pyproject.toml` as an existing release.

For risky changes, the agent may recommend a prerelease such as
`v0.4.0-rc.1`, but must obtain approval before using it.

After version approval:

1. Create `release/vX.Y.Z`; this is an explicit branch-naming exception.
2. Update the version in `pyproject.toml` and move relevant `[Unreleased]`
   entries into a dated changelog section.
3. Run the complete validation suite.
4. Create and verify the signed commit
   `chore(release): release vX.Y.Z`.
5. Integrate it locally and create a signed annotated tag `vX.Y.Z`.

Publishing the release commit and publishing the tag are remote mutations.
Confirm the remote, branch, tag, and refspec with the user before pushing them.
