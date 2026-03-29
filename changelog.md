## 2026-03-29

- Initialized the Python project scaffold with a DDD-aligned `src` layout.
- Added a CLI entrypoint, domain/application/infrastructure boundaries, and a Markdown renderer.
- Added unit test scaffolding for the use case, CLI flow, and Markdown rendering.
- Implemented FIT decoding for session, lap, and record messages with derived kilometer splits and lap transition samples.
- Refactored Markdown output into composable section renderers so new report sections can be added independently.
- Added extractor tests covering record-driven splits, lap fallback behavior, and transition sampling.
- Added CLI flags to configure transition sample interval and transition window without code changes.
- Added integration tests that exercise the real FIT fixtures through the extractor, renderer, and CLI.
- Normalized running cadence from FIT running-cadence fields to full steps per minute and suppressed bogus computed grades during paused transition samples.
- Added temperature-based weather fields to the session summary with FIT extraction fallbacks from session, lap, and record messages.
