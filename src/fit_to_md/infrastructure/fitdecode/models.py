"""Compatibility exports for activity entities moved to the domain layer.

New code should use ``Activity``, ``ActivityLap``, and ``ActivityRecord`` from
``fit_to_md.domain.activity``.
"""

from fit_to_md.domain.activity import Activity, ActivityLap, ActivityRecord

ParsedActivityData = Activity
ParsedLap = ActivityLap
ParsedRecord = ActivityRecord

__all__ = ["ParsedActivityData", "ParsedLap", "ParsedRecord"]
