from dataclasses import dataclass, field

_DEVELOPER_METADATA_MESSAGES = frozenset({"developer_data_id", "field_description"})
_DEVELOPER_METADATA_FIELDS = frozenset(
    {
        "developer_data_index",
        "field_definition_number",
        "field_name",
        "fit_base_type_id",
    }
)


@dataclass(frozen=True)
class FitSanitizationPolicy:
    """Privacy rules used to turn an activity into a public test fixture."""

    allowed_message_names: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                "activity",
                "event",
                "file_id",
                "lap",
                "record",
                "session",
                "sport",
            }
        )
    )
    removed_field_names: frozenset[str] = field(
        default_factory=lambda: frozenset({"serial_number"})
    )
    remove_unknown_fields: bool = True
    remove_developer_fields: bool = True

    def __post_init__(self) -> None:
        if (
            not self.remove_developer_fields
            and not _DEVELOPER_METADATA_MESSAGES.issubset(self.allowed_message_names)
        ):
            raise ValueError(
                "Keeping developer fields requires developer_data_id and "
                "field_description in the message allowlist."
            )
        if not self.remove_developer_fields and self.removed_field_names.intersection(
            _DEVELOPER_METADATA_FIELDS
        ):
            raise ValueError("Keeping developer fields requires their metadata fields.")

    def keeps_message(self, name: str) -> bool:
        return name in self.allowed_message_names

    def keeps_field(self, name: str, *, is_developer: bool = False) -> bool:
        if is_developer and self.remove_developer_fields:
            return False
        if name in self.removed_field_names:
            return False
        return not (self.remove_unknown_fields and name.startswith("unknown_"))
