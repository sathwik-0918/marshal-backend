import os
from datetime import datetime, timezone, timedelta, date

_offset_hours = float(os.getenv("SCHEDULE_TZ_OFFSET_HOURS", "5.5"))
SCHEDULE_TZ = timezone(timedelta(hours=_offset_hours))


def parse_datetime(value: str) -> datetime:
    """
    Parse any datetime string into a timezone-aware datetime.

    Rules:
    - Values ending in Z are treated as UTC.
    - Values containing an explicit offset keep that offset.
    - Bare datetime strings are treated as schedule-local time.
    """
    normalized = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SCHEDULE_TZ)

    return dt


def to_utc_z(dt: datetime) -> str:
    """
    Convert a datetime to canonical UTC ISO format.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_date(dt: datetime) -> date:
    """
    Get the schedule-local calendar date.
    """
    return dt.astimezone(SCHEDULE_TZ).date()


def local_now() -> datetime:
    """
    Current time in the schedule timezone.
    """
    return datetime.now(SCHEDULE_TZ)


def fmt_local(dt: datetime, with_date: bool = True) -> str:
    """
    Format a datetime for user-facing schedule display.
    """
    local = dt.astimezone(SCHEDULE_TZ)

    hour12 = local.hour % 12 or 12
    clock = f"{hour12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"

    if with_date:
        return f"{local.strftime('%b')} {local.day}, {clock}"

    return clock