import pytest

from fit_to_md.domain.privacy import FitSanitizationPolicy


def test_default_policy_keeps_only_messages_required_to_represent_activity() -> None:
    policy = FitSanitizationPolicy()

    assert policy.allowed_message_names == frozenset(
        {"activity", "event", "file_id", "lap", "record", "session", "sport"}
    )
    assert all(policy.keeps_message(name) for name in policy.allowed_message_names)


@pytest.mark.parametrize(
    "message_name",
    [
        "bike_profile",
        "blood_pressure",
        "course",
        "device_info",
        "hrm_profile",
        "monitoring",
        "unknown_327",
        "user_profile",
        "weight_scale",
        "workout",
    ],
)
def test_default_policy_rejects_known_and_unknown_private_messages(
    message_name: str,
) -> None:
    assert not FitSanitizationPolicy().keeps_message(message_name)


def test_policy_accepts_only_messages_in_a_custom_allowlist() -> None:
    policy = FitSanitizationPolicy(
        allowed_message_names=frozenset({"record", "weather_conditions"})
    )

    assert policy.keeps_message("record")
    assert policy.keeps_message("weather_conditions")
    assert not policy.keeps_message("session")
    assert not policy.keeps_message("device_info")


def test_policy_requires_metadata_messages_when_developer_fields_are_kept() -> None:
    with pytest.raises(ValueError, match="developer_data_id and field_description"):
        FitSanitizationPolicy(remove_developer_fields=False)


def test_policy_allows_developer_fields_with_their_metadata_messages() -> None:
    policy = FitSanitizationPolicy(
        allowed_message_names=frozenset(
            {
                "developer_data_id",
                "field_description",
                "record",
            }
        ),
        remove_developer_fields=False,
    )

    assert policy.keeps_message("developer_data_id")
    assert policy.keeps_message("field_description")
    assert policy.keeps_field("custom_metric", is_developer=True)


def test_policy_refuses_to_remove_required_developer_metadata_fields() -> None:
    with pytest.raises(ValueError, match="requires their metadata fields"):
        FitSanitizationPolicy(
            allowed_message_names=frozenset(
                {"developer_data_id", "field_description", "record"}
            ),
            removed_field_names=frozenset({"field_name"}),
            remove_developer_fields=False,
        )


def test_default_policy_removes_identifiers_and_nonstandard_fields() -> None:
    policy = FitSanitizationPolicy()

    assert not policy.keeps_field("serial_number")
    assert not policy.keeps_field("unknown_42")
    assert not policy.keeps_field("custom_power", is_developer=True)
    assert policy.keeps_field("position_lat")
