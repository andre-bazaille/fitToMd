# AGENTS: Engineering Rules For fitToMd

## Purpose

These instructions define how contributors and coding agents must build features in this repository.

## Mandatory Architecture Style

All new work MUST follow Domain-Driven Design (DDD).

## Bounded Contexts

Do not leak implementation details across contexts.

## Testing Requirements (Non-Optional)

Systematic unit test coverage is required for every feature and bug fix.

- Every behavior change MUST ship with tests in `tests/`.
- New modules MUST include happy-path and failure-path tests.
- External API interactions MUST be tested with fakes/mocks (no live network dependency).
- A change is incomplete if unit tests are missing.

## Quality Gate

Before a feature is considered done:

- `pytest` must pass.
- New public classes/functions need type hints.
- Persistence schema changes must have tests that validate read/write behavior.

## Change tracking

Always update changelog.md file to reflect latest changes.