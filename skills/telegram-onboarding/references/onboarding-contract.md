# Onboarding contract

## Server-side states

- `new`: the user exists or has just been created and has not started the current onboarding.
- `awaiting_consent`: the current required document bundle has not been accepted.
- `completed`: the current required document bundle is accepted.
- `declined`: the user explicitly declined the current bundle.

An accepted old bundle does not grant access when a new required bundle is configured.

## Required routes

| Input | Condition | Result |
|---|---|---|
| `/start` | current bundle accepted | main menu |
| `/start` | current bundle not accepted | welcome or consent prompt |
| Continue | not accepted | document bundle and explicit actions |
| Accept | current bundle pending | persist consent, mark completed, show next actions |
| Accept | already accepted | show completed state without another consent record |
| Decline | not accepted | mark declined, keep help and documents available |
| `/start` | declined | explain restricted state and allow document review/reconsideration |

## Document bundle

Identify a required bundle by the pair of terms and privacy-policy versions. Display both versions and externally configurable HTTPS URLs.

## Rendering

- Explain that the bot monitors third-party data.
- State that availability is not guaranteed.
- State that the bot does not sell products or provide medical advice.
- Offer documents and help before consent.
- After acceptance, offer a stable `add subscription` action and the main menu.

## Tests

Cover new, pending, declined, accepted-current, accepted-old-version, duplicate `/start`, duplicate acceptance, storage failure, private-chat enforcement, and missing callback message.
