from datetime import datetime
from typing import Any

PITCH_VERSIONS = {
    "script_version_id": "SCRIPT_VERSION_CHANGED",
    "presentation_version_id": "PRESENTATION_VERSION_CHANGED",
    "standard_version": "CRITERIA_VERSION_CHANGED",
}


def stale_reasons(
    basis: dict[str, Any] | None,
    answered_at: datetime,
    current: dict[str, Any],
    goal_version: int,
    corpus_version: str | None,
) -> list[str]:
    if not isinstance(basis, dict):
        return []
    reasons: set[str] = set()
    for pitch in basis.get("pitches") or []:
        _pitch_reasons(pitch, answered_at, current, reasons)
    if basis.get("conversation_goal_version") != goal_version:
        reasons.add("GOAL_CHANGED")
    if basis.get("sources"):
        if corpus_version is None or "corpus_version" not in basis:
            reasons.add("VERSION_UNKNOWN")
        elif basis["corpus_version"] != corpus_version:
            reasons.add("CORPUS_UPDATED")
    return sorted(reasons)


def _pitch_reasons(
    pitch: dict[str, Any], answered_at: datetime, current: dict[str, Any], reasons: set[str]
) -> None:
    now = current["pitches"].get(pitch.get("pitch_id"))
    if now is None:
        reasons.add("SOURCE_DELETED")
        return
    for key, reason in PITCH_VERSIONS.items():
        if key not in pitch:
            reasons.add("VERSION_UNKNOWN")
        elif pitch[key] != now[key]:
            reasons.add(reason)
    if now["last_completed_at"] is not None and now["last_completed_at"] > answered_at:
        reasons.add("NEW_TAKE")
    for take in pitch.get("takes") or []:
        _take_reasons(take, current, reasons)


def _take_reasons(take: dict[str, Any], current: dict[str, Any], reasons: set[str]) -> None:
    now = current["takes"].get(take.get("take_id"))
    if now is None:
        reasons.add("SOURCE_DELETED")
        return
    for kind, revision in (take.get("results") or {}).items():
        current_revision = now["results"].get(kind)
        if current_revision is None:
            reasons.add("RESULT_UNAVAILABLE")
        elif revision is None:
            reasons.add("VERSION_UNKNOWN")
        elif current_revision != revision:
            reasons.add("RESULT_REVISED")
    for area, version in (take.get("rule_versions") or {}).items():
        current_version = now["rule_versions"].get(area)
        if version is None or current_version is None:
            reasons.add("VERSION_UNKNOWN")
        elif current_version != version:
            reasons.add("RULE_VERSION_CHANGED")
