## 2026-03-29

- Changed running speed presentation in Markdown reports from km/h to pace per kilometer, including average summary pace and transition samples.
- Added CLI flags to tune elevation smoothing distance and minimum elevation change so session gain/loss can be calibrated per device or file set.
- Replaced raw FIT session ascent/descent totals with a filtered record-derived elevation calculation to reduce barometric noise in session summaries.
- Added unit and integration coverage for noise-resistant elevation gain/loss handling.
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
- Added optional historical weather enrichment in CLI `auto` mode so reports can include conditions and wind when Garmin Connect-style weather is not embedded in the FIT file.
