# Two-person collaboration

| Person | Requested responsibility | Current collaboration scope |
|---|---|---|
| Student A — owner | System integration; real timetable OCR diagnosis; backend/frontend; GitHub release coordination | Maintain the integrated application, coordinate owner publication review, and use independent synthetic recognition tests |
| Student B — teammate | Gold Construction preparation; source inventory templates; annotation-guide proposals; 29-page audit workflow preparation; documentation and approved synthetic test planning | Improve blank templates, list missing evidence/decisions, plan independent-review tasks and synthetic fixtures |

Student A's diagnosis role does not expand existing private-image eligibility or authorize tuning on an unverified source. Student B prepares workflows; neither student can invent verified Gold, carry out an independent audit on the other's behalf, or silently appoint themselves an independent reviewer.

## Branches and PRs

- `main`: reviewed collaboration baseline; currently retains the starter until the full-source merge.
- `feature/complete-v1-handover`: prepared full application integration; owner Commit/Push and merge review remain manual.
- `feature/real-timetable-recognition`: Student A's scoped proposals/approved work.
- `research/r2-gold-preparation`: Student B's preparation proposals.

After the owner publishes the full-source branch, clone it using [setup](setup.md). After its reviewed merge to main, create scoped development branches from main. Each person can then run:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/real-timetable-recognition
```

Student B uses `research/r2-gold-preparation` instead of the last branch name. If a branch exists, inspect it and coordinate rather than resetting/overwriting it. These commands were not executed during preparation.

Use small scoped commits/PRs. The other student reviews before merge; the owner decides release/merge approval. Do not put private research records, real source images, credentials or held-out data on **any** branch, issue attachment or PR. Use approved private channels/storage for actual protected evidence.
