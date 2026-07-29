---
name: telegram-onboarding
description: Implement or review Telegram onboarding and consent flows in pharmacy_notify_bot. Use when changing /start routing, welcome screens, document links and versions, explicit consent, declined consent, onboarding recovery, post-onboarding calls to action, aiogram callbacks, or onboarding tests.
---

# Telegram Onboarding

## Workflow

1. Read the active onboarding story and all of its sub-stories.
2. Read [references/onboarding-contract.md](references/onboarding-contract.md).
3. Model routing as an application decision independent of aiogram.
4. Persist user state and consent before presenting a success response.
5. Keep callbacks thin: validate the Telegram context, call the application service, render its result, and answer the callback.
6. Make `/start`, document viewing, acceptance, decline, and post-onboarding actions idempotent.
7. Test the application policy separately from aiogram rendering and routing.

## Guardrails

- Never infer consent from continued usage or an unrelated action.
- Never enable protected actions until the current required document bundle is accepted.
- Store the accepted document versions, timestamp, and acceptance method.
- Keep legal text and URLs configurable; do not embed production policy content in handlers.
- Do not ask for a product, address, or exact geolocation before explaining the service.
- Do not claim that availability is guaranteed or provide medical advice.
- Do not implement the subscription wizard while working only on onboarding; expose a stable next-action callback instead.
