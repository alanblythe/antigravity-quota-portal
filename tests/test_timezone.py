"""Unit tests for timezone handling and weekly window boundaries."""

from datetime import UTC, datetime

from app.core.timezone_engine import get_current_week_window, is_new_week


def test_get_current_week_window_monday_start():
    # Thursday, August 27, 2026, 12:00:00 UTC
    dt = datetime(2026, 8, 27, 12, 0, 0, tzinfo=UTC)
    week_id, start_utc, end_utc, now_utc = get_current_week_window("America/Los_Angeles", dt)

    assert week_id == "2026-W35"

    # Monday in Los Angeles was August 24, 2026
    # PDT is UTC-7, so Monday 00:00:00 PDT is Monday 07:00:00 UTC
    assert start_utc.year == 2026
    assert start_utc.month == 8
    assert start_utc.day == 24
    assert start_utc.hour == 7
    assert start_utc.minute == 0


def test_is_new_week():
    assert is_new_week("2026-W34", "2026-W35") is True
    assert is_new_week("2026-W35", "2026-W35") is False
    assert is_new_week(None, "2026-W35") is False
