---
name: pharmacy-bot-development
description: Implement, review, or refactor features in the pharmacy_notify_bot repository. Use for work derived from PROJECT_SPECIFICATION.md or GitHub Issues, especially Python architecture, domain boundaries, PostgreSQL persistence, Telegram presentation, tests, migrations, and issue completion.
---

# Pharmacy Bot Development

## Workflow

1. Read the target Issue and its complete sub-issue hierarchy before editing.
2. Read only the relevant sections of `PROJECT_SPECIFICATION.md`.
3. Inspect the worktree and preserve unrelated changes.
4. Keep the implementation inside the requested Issue boundary. Represent dependencies on later epics as ports or extension points instead of implementing them early.
5. Follow the architecture and commands in [references/project-conventions.md](references/project-conventions.md).
6. Add tests that express the Issue acceptance criteria.
7. Run the full local validation suite before reporting completion.
8. Do not close Issues, push, or create a PR unless the user explicitly requests it.

## Requirement Traceability

- Put the GitHub Issue number in the implementation handoff and PR description.
- Prefer business-oriented tests whose names describe the acceptance rule.
- Keep policy decisions in application or domain code, not Telegram handlers.
- Treat `PROJECT_SPECIFICATION.md` as the product source of truth and the target Issue as the active delivery scope.
- If the Issue conflicts with the specification, stop and surface the conflict rather than silently choosing one.

## Repository Skills

When the task changes Telegram onboarding or consent behavior, also read and use `../telegram-onboarding/SKILL.md`.
