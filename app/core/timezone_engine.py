"""Timezone and weekly boundary calculation engine."""

from datetime import UTC, datetime, time, timedelta

import pytz


def get_timezone_obj(tz_name: str) -> pytz.BaseTzInfo:
    """Safely get pytz timezone object with fallback to UTC."""
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.UTC


def get_current_week_window(
    tz_name: str = "America/Los_Angeles",
    now_dt: datetime | None = None,
) -> tuple[str, datetime, datetime, datetime]:
    """Calculate the current weekly quota window starting at Monday 00:00:00 in the given timezone.

    Returns:
        week_id: ISO week identifier e.g. "2026-W35"
        start_utc: Datetime in UTC representing Monday 00:00:00 in the target timezone
        end_utc: Datetime in UTC representing Sunday 23:59:59 in the target timezone
        current_utc: Current datetime in UTC
    """
    tz = get_timezone_obj(tz_name)

    if now_dt is None:
        now_utc = datetime.now(UTC)
    else:
        if now_dt.tzinfo is None:
            now_utc = now_dt.replace(tzinfo=UTC)
        else:
            now_utc = now_dt.astimezone(UTC)

    # Convert current UTC time to application timezone
    now_local = now_utc.astimezone(tz)

    # Monday of the current week in local timezone
    # weekday(): Monday is 0 and Sunday is 6
    days_since_monday = now_local.weekday()
    monday_date = (now_local - timedelta(days=days_since_monday)).date()

    # Start: Monday 00:00:00 local
    monday_midnight_local = tz.localize(datetime.combine(monday_date, time.min))
    start_utc = monday_midnight_local.astimezone(UTC)

    # End: Sunday 23:59:59.999999 local
    sunday_date = monday_date + timedelta(days=6)
    sunday_end_local = tz.localize(datetime.combine(sunday_date, time.max))
    end_utc = sunday_end_local.astimezone(UTC)

    # ISO week string: YYYY-Www (e.g. 2026-W35)
    iso_year, iso_week, _ = now_local.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"

    return week_id, start_utc, end_utc, now_utc


def is_new_week(last_week_id: str | None, current_week_id: str) -> bool:
    """Check if the recorded week ID differs from current week ID."""
    if not last_week_id:
        return False
    return last_week_id != current_week_id
