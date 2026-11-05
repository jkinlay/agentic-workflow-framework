"""Pure operating-calendar contract; schedules presentation, never execution.

No file writes, settings authentication, reporting enablement or merge authority.
Omitted settings resolve in memory so historical configuration bytes stay intact.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ValidationError
from .canonical import fingerprint, timestamp


_DEFAULTS = {
    "timezone": "Europe/London",
    "work_mode": "continuous",
    "review_batching": {
        "enabled": True, "weekdays": [1, 2, 3, 4, 5],
        "presentation_time": "07:00", "cutoff_time": "17:00",
    },
    "eod": {"time": "17:00", "weekdays": [1, 2, 3, 4, 5, 6, 7]},
}


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _zone(name):
    _require(isinstance(name, str) and bool(name), "Calendar timezone must be an IANA name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValidationError(
            "Calendar IANA timezone is unavailable or invalid; provision IANA tzdata "
            "for this Python runtime or correct operating_calendar.timezone; scheduling refused"
        ) from exc


def _clock(value, path):
    _require(isinstance(value, str) and
             re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value) is not None,
             path + " must be strict local HH:MM")


def validate_calendar(value):
    """Validate a partial calendar object and return a detached effective object.

    Only this subtree is accepted. Unknown keys, explicit nulls, duplicates and
    coercible booleans/numbers refuse. This does not validate the operating root.
    """
    _require(isinstance(value, dict), "operating_calendar must be an object")
    _require(set(value) <= set(_DEFAULTS), "Unknown operating_calendar field")
    result = deepcopy(_DEFAULTS)
    for key, item in value.items():
        if key in ("review_batching", "eod"):
            _require(isinstance(item, dict), "operating_calendar." + key + " must be an object")
            _require(set(item) <= set(_DEFAULTS[key]), "Unknown operating_calendar." + key + " field")
            result[key].update(deepcopy(item))
        else:
            result[key] = deepcopy(item)
    _zone(result["timezone"])
    _require(result["work_mode"] == "continuous", "Calendar work_mode must be continuous")
    review = result["review_batching"]
    _require(type(review["enabled"]) is bool, "review_batching.enabled must be Boolean")
    for key in ("review_batching", "eod"):
        days = result[key]["weekdays"]
        _require(isinstance(days, list) and bool(days) and
                 all(type(day) is int and 1 <= day <= 7 for day in days) and
                 len(set(days)) == len(days), key + ".weekdays must be unique ISO weekdays 1..7")
        # Day ordering is semantically irrelevant; equivalent choices share a pin.
        result[key]["weekdays"] = sorted(days)
    _clock(review["presentation_time"], "review_batching.presentation_time")
    _clock(review["cutoff_time"], "review_batching.cutoff_time")
    _clock(result["eod"]["time"], "eod.time")
    _require(review["presentation_time"] < review["cutoff_time"],
             "Review presentation time must precede cutoff time")
    return result


def effective_calendar(config):
    """Resolve root OPERATING_CONFIG's optional calendar without mutating it.

    Stable consumer API: root mapping in, complete calendar mapping out. An absent
    subtree defaults; an explicit null refuses. Unrelated root fields are ignored.
    """
    _require(isinstance(config, dict), "Operating configuration must be an object")
    return validate_calendar(config.get("operating_calendar", {}))


def utc_instant(value):
    """Accept RFC3339 or an aware datetime and return an aware UTC datetime."""
    if isinstance(value, datetime):
        _require(value.tzinfo is not None and value.utcoffset() is not None,
                 "Calendar instants require an explicit timezone")
        return value.astimezone(timezone.utc)
    return timestamp(value)


def utc_text(value):
    return utc_instant(value).isoformat().replace("+00:00", "Z")


def _valid_instants(local, zone):
    found = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            found.add(instant)
    return found


def _local_boundary(day, clock, zone):
    """Later fold occurrence; first valid local instant after a gap.

    The bounded search also covers IANA zones that skipped an entire date.
    Checking the final minute at second resolution preserves historical rules.
    """
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute)
    found = _valid_instants(local, zone)
    if found:
        return max(found)
    for offset in range(1, 2 * 24 * 60 + 1):
        probe = local + timedelta(minutes=offset)
        if _valid_instants(probe, zone):
            for second in range(1, 61):
                found = _valid_instants(probe - timedelta(minutes=1) + timedelta(seconds=second), zone)
                if found:
                    return max(found)
    raise ValidationError("Calendar local boundary cannot be resolved from IANA rules")


def next_review_presentation(ready_at, calendar):
    """Return a candidate-neutral presentation decision with stable wire fields.

    ``calendar`` is a partial/effective subtree, not the operating root. Output
    ``presentation_at`` is RFC3339 UTC; ``ready_at`` retains the input RFC3339
    text (aware datetimes normalize to UTC). ``calendar_sha256`` pins effective
    settings. Batching gates only routine final human review presentation.
    """
    effective = validate_calendar(calendar)
    ready = utc_instant(ready_at)
    review = effective["review_batching"]
    presentation = ready
    if review["enabled"]:
        zone = _zone(effective["timezone"])
        day = ready.astimezone(zone).date()
        try:
            for offset in range(9):
                current = day + timedelta(days=offset)
                if current.isoweekday() not in review["weekdays"]:
                    continue
                opening = _local_boundary(current, review["presentation_time"], zone)
                closing = _local_boundary(current, review["cutoff_time"], zone)
                if opening >= closing:
                    # Gap resolution can collapse a short custom window.
                    continue
                if ready < closing:
                    presentation = max(ready, opening)
                    break
            else:
                raise ValidationError("No valid review presentation window in calendar horizon")
        except (OverflowError, ValueError) as exc:
            raise ValidationError("Review calendar exceeds supported datetime range") from exc
    return {"ready_at": ready_at if isinstance(ready_at, str) else utc_text(ready),
            "presentation_at": utc_text(presentation),
            "calendar_sha256": fingerprint("operating-calendar", effective),
            "held": presentation > ready,
            "merge_authorized": False}
