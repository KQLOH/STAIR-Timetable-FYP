# Supervisor review and safe publication

The current system is prepared locally. **Commit, push, merge and force-push require the owner's confirmation.** This document does not execute or authorize those actions.

## What the supervisor can inspect

Use the current README, [setup](setup.md), [scope](scope-and-status.md) and [local Windows review guide](local-student-review.md). Review the actual source/tests for upload, S2b/RT4, constrained reconstruction, source evidence, explicit human assertions, Windows source-scoped authorization, encrypted persistence, revalidation and blocked calendar preview. The built-in **Try sample** view and synthetic suite need no private timetable or university login.

No private screenshots, datasets, OCR captures/predictions, checked references, experimental scores, DPAPI journals, credentials, logs or local investigation reports belong in the proposed source publication. Generic research programs and invented regression fixtures are source code, not a supplied research evidence package.

## Proposed publication sequence

1. Review the exact source allowlist, hashes, test results and exclusions prepared outside the repository. Preserve excluded untracked material; do not use `git clean`, `reset --hard` or broad `git add .`.
2. Once the owner confirms, create `codex/supervisor-review-20261116` from the current feature HEAD. This preserves existing commits and working changes. Stop if that branch already exists or the reviewed files changed.
3. Stage only the reviewed paths, inspect the staged diff/private-file scan, then create a descriptive source-only commit. No historical results or private review records are rewritten.
4. Refresh remote metadata and confirm the destination branch is still absent before pushing. If the destination exists, stop and compare; never overwrite or force-push it. A normal push to the new branch leaves existing remote branches untouched.
5. Share `https://github.com/KQLOH/STAIR-Timetable-FYP/tree/codex/supervisor-review-20261116` only after a successful confirmed push. The URL is a proposal until that happens. Main remains unchanged; merging is a separate owner decision.

The private preparation manifest records the actual local and remote tips inspected for this review; it is not a live guarantee. Recheck them immediately before publication. Existing release manifests/installation reports refer to earlier snapshots and must not be relabeled as current evidence.

## Owner-only command outline

These are review steps, **not commands to run automatically**:

```powershell
git status --short --branch
git fetch --no-tags origin
git switch -c codex/supervisor-review-20261116
# Verify source hashes and use the exact prepared allowlist; never git add .
git add --pathspec-from-file='<absolute path to reviewed paths.nul>' --pathspec-file-nul
git diff --cached --stat
git diff --cached --check
# Inspect the staged source/privacy list before confirming the commit.
git commit -m "Prepare complete STAIR working system for supervisor review"
git ls-remote --heads origin refs/heads/codex/supervisor-review-20261116
# Stop if the remote branch exists; do not force or overwrite it.
git push --set-upstream origin codex/supervisor-review-20261116
```

The actual commit hash can be reported only after the owner authorizes and the commit is created. No invitation or repository visibility change is performed by this preparation. If repository access changes, the owner must arrange supervisor access separately.
