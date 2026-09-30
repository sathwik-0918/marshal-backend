from __future__ import annotations

import re
from typing import Any
from datetime import timedelta

from agent.time_utils import parse_datetime


def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize(text: Any) -> str:
    if text is None:
        return ""
    value = str(text).strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,.;:!?")


def _normalize_for_match(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize(text))


def _activity_start(activity: dict):
    try:
        return parse_datetime(activity["scheduledStart"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _title_matches(activity: dict, requested_title: str) -> bool:
    """
    Exact match first, then containment - "the opening match" should
    still find "Day 1 Opening Match" instead of matching nothing.
    If a vague reference like this matches more than one real activity
    (Day 1 AND Day 2 both have one), BOTH come back as affected. That's
    intentional: the proposal card visibly lists both, so it's easy to
    notice and reject if that's not what you meant - matching nothing,
    or silently guessing one, are both worse than that.
    """
    actual = _normalize_for_match(activity.get("title"))
    requested = _normalize_for_match(requested_title)
    if not actual or not requested:
        return False
    return actual == requested or requested in actual


def _venue_matches(activity: dict, requested_venue: str) -> bool:
    actual = _normalize_for_match(activity.get("venue"))
    requested = _normalize_for_match(requested_venue)
    if not actual or not requested:
        return False
    return actual == requested or requested in actual or actual in requested


def _resource_matches(activity: dict, requested_resource: str) -> bool:
    requested = _normalize_for_match(requested_resource)
    if not requested:
        return False
    for resource in activity.get("requiredResources") or []:
        actual = _normalize_for_match(resource)
        if actual and (actual == requested or requested in actual or actual in requested):
            return True
    return False


def _type_matches(activity: dict, requested_type: str) -> bool:
    actual = _normalize(activity.get("activityType"))
    requested = _normalize(requested_type)
    if not actual or not requested:
        return False
    return actual == requested or requested in actual or actual in requested


def _within_time_window(activity: dict, window_start, window_end) -> bool:
    if window_start is None or window_end is None:
        return True
    start = _activity_start(activity)
    if start is None:
        return False
    try:
        duration = int(activity.get("durationMinutes") or 0)
    except (ValueError, TypeError):
        duration = 0
    if duration <= 0:
        return window_start <= start < window_end
    end = start + timedelta(minutes=duration)
    return start < window_end and window_start < end


def _extract_window(intent):
    start_raw = _get(intent, "time_window_start")
    end_raw = _get(intent, "time_window_end")
    if not start_raw or not end_raw:
        return None, None
    try:
        return parse_datetime(start_raw), parse_datetime(end_raw)
    except (ValueError, TypeError):
        return None, None


def resolve_affected_activities(activities: list[dict], intent: Any) -> list[str]:
    """
    Deterministically turns extracted references into real activity ids.

    The rule that was missing: if the message gives NO entity at all
    (no title, venue, resource, or type) AND NO time window, this
    returns EMPTY. The version this replaces fell through to matching
    EVERY activity when nothing was mentioned - that's exactly how
    "something important has been delayed, fix the schedule" turned
    into a 29-activity proposal. An unscoped request must ask for
    clarification, never silently mean "everything."
    """
    if not activities:
        return []

    named_activities = [str(x).strip() for x in (_get(intent, "named_activities", []) or []) if str(x).strip()]
    mentioned_venues = [str(x).strip() for x in (_get(intent, "mentioned_venues", []) or []) if str(x).strip()]
    mentioned_resources = [str(x).strip() for x in (_get(intent, "mentioned_resources", []) or []) if str(x).strip()]
    activity_type_keywords = [str(x).strip() for x in (_get(intent, "activity_type_keywords", []) or []) if str(x).strip()]
    window_start, window_end = _extract_window(intent)

    any_entity_mentioned = bool(named_activities or mentioned_venues or mentioned_resources or activity_type_keywords)
    has_time_window = window_start is not None and window_end is not None

    if not any_entity_mentioned and not has_time_window:
        return []

    results = []
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        activity_id = activity.get("_id")
        if not activity_id:
            continue

        entity_match = False
        if named_activities:
            entity_match = any(_title_matches(activity, requested) for requested in named_activities)
            if not entity_match:
                continue
        else:
            if mentioned_venues and any(_venue_matches(activity, v) for v in mentioned_venues):
                entity_match = True
            if mentioned_resources and any(_resource_matches(activity, r) for r in mentioned_resources):
                entity_match = True
            if activity_type_keywords and any(_type_matches(activity, t) for t in activity_type_keywords):
                entity_match = True
            if any_entity_mentioned and not entity_match:
                continue

        if has_time_window and not _within_time_window(activity, window_start, window_end):
            continue

        results.append(str(activity_id))

    return results