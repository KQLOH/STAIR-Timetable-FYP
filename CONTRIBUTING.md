# Contribution workflow

This PUBLIC repository begins as a project starter, not a runnable V1.0 application. Read [AGENTS.md](AGENTS.md), [scope](docs/scope-and-status.md) and [public safety](docs/public-safety.md).

1. Open a scoped issue, identify the author and the other student's reviewer, and explain the intended change before editing.
2. Work on the agreed branch: Student A uses `feature/real-timetable-recognition`; Student B uses `research/r2-gold-preparation`. Keep `main` reviewed and understandable. These names are instructions for future manual work, not branches already created.
3. Use independent synthetic examples and planning templates. Public proposals must say PROPOSED / NOT APPROVED. Actual Gold, private timetable/permission/person records and held-out material stay outside all public branches and PRs.
4. Run the starter tests/link checker for starter changes. Future approved code needs its own meaningful tests and complete dependencies; report actual results and limitations.
5. Review exact files and staged diff. Never blanket-add local material, force-add protected evidence, force-push, mirror all refs or import old Git history.
6. Submit a PR to `main`; the other student reviews scope, source rights, privacy, tests and research boundaries. The owner decides whether to merge. Do not merge your own unreviewed change just to make progress visible.

Role allocation is in [team workflow](docs/team-workflow.md). New source requires the separate [source-release gates](docs/public-safety.md); the starter does not grant permission for the 18 held files. End each task with changed files, implemented/proposed work, actual checks, limitations and next step. No fake Gold, test results or owner/supervisor approval.
