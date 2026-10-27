# Final integration review and manual publication

The team leader authorized local source integration and manages source-sharing/publication permissions. No declaration workflow is restarted here. The complete application is on local branch `feature/complete-v1-handover`, preserving the original starter commit. No automatic staging, commit or push was performed.

1. Review [the exact changed-file list and results](github-integration-review.md), the [source manifest](../COMPLETE-SOURCE-RELEASE-MANIFEST.json) and [setup](setup.md).
2. Confirm the branch and origin below. Review all changes, including untracked source; `git diff` alone does not show untracked files. No private runtime/data/journal or original Git history belongs in the commit.
3. After final owner review, use the generated NUL-separated allowlist in ignored `data/temp/v1-release-paths.nul`. It contains source/documentation paths only. Do not stage `.venv/`, `data/` or the pathspec file itself. Stop if the working tree changes after this review.
4. Inspect staged contents before committing. Then push this exact feature branch to the existing origin. Public main remains the starter until a reviewed merge.

Run manually from the existing GitHub checkout:

```powershell
git branch --show-current
git remote get-url origin
git status --short --untracked-files=all
# Expected: feature/complete-v1-handover and the existing KQLOH/STAIR-Timetable-FYP origin.
# OWNER REVIEW/DECISION before staging:
git add --pathspec-from-file=data/temp/v1-release-paths.nul --pathspec-file-nul
git diff --cached --stat
git diff --cached --name-only
git diff --cached --check
# Inspect the staged source/privacy list before the next two commands.
git commit -m "Integrate complete STAIR-Timetable V1 prototype source"
git push -u origin feature/complete-v1-handover
```

The old starter-only verification and proposals remain historical. Synthetic software checks are not real timetable recognition accuracy, Gold/PRE closure, formal held-out evaluation or owner V1 sign-off. No real calendar synchronization or live operator enrollment is enabled by publishing source.
