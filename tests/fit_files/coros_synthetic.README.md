# Synthetic COROS regression fixture

`coros_synthetic.fit` is generated entirely from invented data by
`tests/support/binary_coros.py`. It contains no copied GPS track, device ID,
serial number, or personal activity data. It imitates the relevant structure of
the COROS exports inspected during the November 2026 weather fixes; it is not a
file produced by COROS hardware.

The fixture is a 2 km, 10 minute run on 2020-01-01 at 12:00 UTC, with 61 records,
two laps, COROS manufacturer metadata, and valid header and file CRCs. It has:

- No session start coordinates; GPS exists only in the track, after an initial
  sample without a GPS lock.
- A session device temperature of 17C and lap temperatures of 23C and 15C,
  with no ambient weather observation.
- An intentionally short `event.data` field: one byte declared as `uint32`,
  reproducing the recoverable decoder warning in the observed exports.

Integration tests exercise actual FIT decoding, config parsing, weather HTTP
response parsing, and Markdown output. Only the HTTP transport and current time
are replaced, keeping tests offline and covering both recent and archived
weather, FIT-only mode, and provider failure.

Regenerate from the repository root with:

```bash
.venv/bin/python -c 'from pathlib import Path; from tests.support.binary_coros import write_coros_activity; write_coros_activity(Path("tests/fit_files/coros_synthetic.fit"))'
```
