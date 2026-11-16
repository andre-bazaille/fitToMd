"""Exercise COROS export quirks through real binary decoding and CLI wiring."""

import io
import json
import warnings
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import fitdecode
import pytest

import fit_to_md.cli as cli
from fit_to_md.infrastructure.weather.open_meteo import (
    OpenMeteoHistoricalWeatherProvider,
)

SOURCE = Path(__file__).resolve().parents[1] / "fit_files" / "coros_synthetic.fit"


def test_synthetic_coros_fixture_contains_export_quirks_and_valid_crcs() -> None:
    with pytest.warns(UserWarning, match="invalid field size 1") as caught:
        with fitdecode.FitReader(SOURCE, check_crc=fitdecode.CrcCheck.RAISE) as reader:
            frames = [
                frame
                for frame in reader
                if frame.frame_type == fitdecode.FIT_FRAME_DATA
            ]

    assert len(caught) == 1
    file_id = next(frame for frame in frames if frame.name == "file_id")
    assert file_id.get_value("manufacturer") == "coros"
    assert file_id.get_value("product_name") == "Synthetic COROS"
    session = next(frame for frame in frames if frame.name == "session")
    assert not session.has_field("start_position_lat")
    assert not session.has_field("start_position_long")
    assert session.get_value("avg_temperature") == 17
    assert not any(frame.name == "weather_conditions" for frame in frames)
    assert [
        frame.get_value("avg_temperature") for frame in frames if frame.name == "lap"
    ] == [23, 15]
    records = [frame for frame in frames if frame.name == "record"]
    assert len(records) == 61
    assert records[0].get_value("position_lat") is None
    assert records[1].get_value("position_lat") is not None
    assert records[-1].get_value("distance") == 2000


@pytest.mark.parametrize(
    ("mode", "now_day", "provider_fails"),
    [
        ("fit", 10, False),
        ("auto", 10, False),
        ("auto", 1, False),
        ("auto", 1, True),
    ],
)
def test_coros_cli_weather_config_and_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    now_day: int,
    provider_fails: bool,
) -> None:
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> io.BytesIO:
        calls.append(url)
        if provider_fails:
            raise TimeoutError("synthetic weather timeout")
        return io.BytesIO(
            json.dumps(
                {
                    "hourly": {
                        "time": ["2020-01-01T12:00"],
                        "temperature_2m": [6.0],
                        "apparent_temperature": [4.0],
                        "weather_code": [3],
                        "wind_speed_10m": [5.0],
                        "wind_direction_10m": [90.0],
                    }
                }
            ).encode()
        )

    def weather_provider() -> OpenMeteoHistoricalWeatherProvider:
        assert mode == "auto", "FIT-only mode must not construct a weather provider"
        return OpenMeteoHistoricalWeatherProvider(
            urlopen_fn=fake_urlopen,
            now_fn=lambda: datetime(2020, 1, now_day, 23, tzinfo=UTC),
        )

    monkeypatch.setattr(cli, "OpenMeteoHistoricalWeatherProvider", weather_provider)
    config = tmp_path / "coros.config"
    config.write_text(
        f"weather-mode={mode}\noutput-by-activity-time=true\n"
        "dynamics-step-size=60\nelevation-source=fit\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.md"
    stdout, stderr = io.StringIO(), io.StringIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        status = cli.run(
            [str(SOURCE), "--config", str(config), "--output", str(output)],
            stdout=stdout,
            stderr=stderr,
        )

    assert status == 0
    assert caught == []
    markdown = output.read_text(encoding="utf-8")
    assert markdown == stdout.getvalue()
    assert "# FIT Report: 2020-01-01 Running" in markdown
    assert "- **Total Distance:** 2.00 km" in markdown
    assert "## Kilometric Splits" in markdown
    assert "Device Temperature" not in markdown
    assert "17.0C" not in markdown
    if mode == "auto":
        assert len(calls) == 1
        url = urlsplit(calls[0])
        query = parse_qs(url.query)
        assert query["latitude"] == ["48.000000"]
        assert query["longitude"] == ["2.000000"]
        assert query["start_date"] == ["2020-01-01"]
        assert query["end_date"] == ["2020-01-01"]
        assert url.netloc == (
            "api.open-meteo.com" if now_day == 1 else "archive-api.open-meteo.com"
        )
    else:
        assert calls == []
    if mode == "auto" and not provider_fails:
        assert (
            "- **Weather:** 6.0C, feels like 4.0C, Overcast, Wind 5.0 km/h E [historical]"
            in markdown
        )
    else:
        assert "- **Weather:** FIT and historical weather data unavailable" in markdown
    if provider_fails:
        assert "synthetic weather timeout" in stderr.getvalue()
        assert "[provider_unavailable]" in stderr.getvalue()
    else:
        assert stderr.getvalue() == ""
