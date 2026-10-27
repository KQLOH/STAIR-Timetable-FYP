# AGENTS.md — STAIR-Timetable Development Instructions

## Project identity

Project Name: STAIR-Timetable — Schema-and Temporal-Consistency-Aware Timetable for Safe Calendar Synchronisation.

This repository supports the Software Engineering Final Year Project (FYP). Development must remain aligned with the approved research scope and evidence.

## Research references and scope

The project must follow these three approved research reference documents:

- STAIR-Timetable Research Report
- Supervisor / Moderator Review
- Supervision Answers

Preserve the approved research objectives RO1–RO4 and research questions RQ1–RQ4. Do not silently redefine, omit, or broaden them.

Follow the approved methodology, baseline definitions, evaluation protocols, and calendar safety requirements. When relevant approved documents or evidence are not available in the workspace, identify that limitation and do not invent their contents.

## Architecture and implementation

- Maintain the existing FastAPI backend and HTML/CSS/JavaScript frontend unless an architectural change is explicitly approved by the user.
- Prefer extending existing code over unnecessary rewrites.
- Treat the previously proposed architecture and canonical event schema as provisional design guidance only. They do not prove that any feature has been implemented.
- Clearly distinguish implemented functionality, verified evidence, provisional results, and planned features in code documentation and task reports.
- For normal development tasks, implement only the requested module or milestone.
- Before changing code, inspect the relevant files and explain the intended implementation.
- Keep code readable, modular, testable, and suitable for an undergraduate Software Engineering FYP.

## Evidence, data, and evaluation integrity

- Never fabricate missing data, source evidence, gold annotations, test results, or evaluation metrics.
- Do not treat the 1,934 pre-annotation rows as independently verified gold events.
- Do not perform formal held-out evaluation until the required independent audit, gold freeze, and test-seal procedures have been completed.
- Preserve the approved baseline definitions and evaluation protocols. Report what was actually run and the evidence available to support each result.

## Privacy and calendar safety

- Do not automatically synchronize events to real Google Calendars without explicit user approval.
- Protect TAR UMT credentials, private timetable data, API secrets, and OAuth tokens. Do not expose them in logs, commits, examples, or reports.
- Keep original research documents, source ZIP archives, datasets, and baseline evidence packages unchanged unless the user explicitly authorizes modifications.

## Dependencies and destructive operations

- Run appropriate tests after code changes and report actual results.
- Do not delete files, change database schemas, install major dependencies, or perform destructive operations without user approval.
- Do not claim tests or checks passed unless they were actually performed; identify skipped checks and their reasons.

## End-of-task report

At the end of each development task, report:

- Files changed
- Features implemented
- Tests performed
- Known limitations
- Next recommended step
